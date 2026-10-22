"""
Flask web dashboard for the Discord AI self-bot.

Run locally:
    python app.py
Then open http://localhost:5000 in your browser.

Production (gunicorn, single worker + threads for shared state):
    gunicorn -w 1 --threads 4 app:app --bind 0.0.0.0:${PORT:-5000}

Endpoints:
    GET  /              -> dashboard UI
    GET  /api/status    -> JSON: bot state, uptime, config summary, recent logs
    GET  /api/config    -> JSON: current .env values (secrets masked to 'set'/'unset')
    POST /api/config    -> write form values to .env, then reload env vars
    POST /api/start     -> start the bot in a background thread (uses .env vars)
    POST /api/stop      -> gracefully stop the running bot
    POST /api/send      -> DM the TARGET_USER with given text
    POST /api/reload    -> reload .env (so you can edit config without restart)

Startup behaviour:
    AUTO_START=1 (default) makes the bot connect by itself when the web app
    boots, so it comes back on its own after a reboot without anyone clicking
    "Start Bot". AUTO_RESTART=1 re-connects it if the connection dies.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import logging.handlers
import os
import threading
import time
import sys
from collections import deque
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv, set_key

from instancelock import SingleInstanceLock

# ---------------------------------------------------------------------------
# Flask app + shared state
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
load_dotenv(override=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

log_capture = deque(maxlen=300)  # last 300 log lines


class _DequeHandler(logging.Handler):
    """Send log records into the dashboard's in-memory deque."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_capture.appendleft(
                f"{time.strftime('%H:%M:%S')} [{record.levelname}] {record.getMessage()}"
            )
        except Exception:
            pass


# Hook the root logger so we capture the bot's log output too.
_h = _DequeHandler()
_h.setLevel(logging.INFO)
logging.getLogger().addHandler(_h)

# NOTE: logging.basicConfig() is a no-op once the root logger already has a
# handler, so calling it here would leave the root level at WARNING and every
# log.info() from bot.py would be discarded before reaching the dashboard.
# Configure the console output and the root level explicitly instead.
_console = logging.StreamHandler()
_console.setLevel(logging.INFO)
_console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(_console)
logging.getLogger().setLevel(logging.INFO)
log = logging.getLogger("dashboard")


def _add_file_log() -> None:
    """Also write everything to logs\\app.log.

    The app usually runs with pythonw — no console, no output. Without a log
    file, "it closed again" is unanswerable, because the reason is gone the
    moment the process dies.
    """
    try:
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "app.log"),
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger().addHandler(handler)
    except OSError as e:
        logging.getLogger("dashboard").warning("Could not open the log file: %s", e)


_add_file_log()


class BotState:
    """Holds the bot thread, loop, and lifecycle flags."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.bot = None  # discord.py-self Bot instance
        self.module = None  # the imported `bot` module
        self.running = False
        self.started_at: float | None = None
        self.last_error: str | None = None
        self.started_by: str | None = None  # "auto" (startup) or "manual" (button)
        self.stop_requested = False  # user pressed Stop -> do not auto-restart
        self.restarts = 0  # consecutive auto-restarts since the last good run

    @property
    def uptime(self) -> int:
        if not self.started_at or not self.running:
            return 0
        return int(time.time() - self.started_at)


state = BotState()


# ---------------------------------------------------------------------------
# Startup behaviour
# ---------------------------------------------------------------------------
# AUTO_START=1  -> the bot connects on its own when the web app boots. Nobody
#                   has to click "Start Bot" after a reboot.
# AUTO_RESTART=1 -> if the connection dies, bring it back after
#                   AUTO_RESTART_DELAY seconds (a bad token still fails, but a
#                   dropped socket does not end the session).
def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("%s=%r is not a number - using %s", name, raw, default)
        return default


AUTO_START = _env_flag("AUTO_START", True)
AUTO_START_DELAY = _env_float("AUTO_START_DELAY", 3.0)
AUTO_RESTART = _env_flag("AUTO_RESTART", True)
AUTO_RESTART_DELAY = _env_float("AUTO_RESTART_DELAY", 30.0)
# 0 means "never give up". The bot coming back is the entire point of the
# auto-start, and a limit just means the bot stays dead until someone
# notices and clicks the button.
AUTO_RESTART_MAX = int(_env_float("AUTO_RESTART_MAX", 0))
AUTO_RESTART_BACKOFF_MAX = _env_float("AUTO_RESTART_BACKOFF_MAX", 300.0)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class _SingleInstanceLock(SingleInstanceLock):
    """Backwards-compatible alias for the shared lock in instancelock.py."""


# Held for as long as this process owns the Discord connection.
_bot_lock = _SingleInstanceLock(os.path.join(BASE_DIR, ".bot.lock"))

# Held for as long as THIS web app is running. A second copy of the app would
# fight over the port and over the bot, so it refuses to start.
_app_lock = SingleInstanceLock(os.path.join(BASE_DIR, ".app.lock"))


# ---------------------------------------------------------------------------
# Bot lifecycle (runs in a background thread with its own asyncio loop)
# ---------------------------------------------------------------------------
def _bot_thread_main() -> None:
    """Entry point for the bot background thread."""
    # Import fresh each time so we pick up the latest .env values.
    import bot as bot_module
    importlib.reload(bot_module)
    state.module = bot_module

    state.loop = asyncio.new_event_loop()
    asyncio.set_event_loop(state.loop)
    state.bot = bot_module.bot

    log.info("Bot thread starting — provider=%s model=%s",
             bot_module.AI_PROVIDER, bot_module.AI_MODEL)

    # Heartbeat watchdog — force-close the bot if the gateway goes silently
    # stale (a common failure mode where the TCP socket is open but Discord
    # has dropped the session). Without this, the dashboard shows "Online"
    # forever and the bot stops responding.
    async def _heartbeat_watchdog() -> None:
        grace = float(os.getenv("BOT_HEARTBEAT_GRACE_SEC", "90"))
        poll_every = float(os.getenv("BOT_HEARTBEAT_POLL_SEC", "30"))
        startup_timeout = float(os.getenv("BOT_STARTUP_TIMEOUT_SEC", "120"))
        last_good_latency = time.time()   # when we last saw a small, real latency
        startup_at = time.time()
        reported_startup = False
        while True:
            await asyncio.sleep(poll_every)
            if state.bot is None or state.bot.is_closed():
                return
            # Startup timeout: if the bot hasn't reached is_ready() within
            # startup_timeout seconds, the WS is stuck — force close so the
            # supervisor can restart us.
            if not state.bot.is_ready():
                if time.time() - startup_at > startup_timeout:
                    log.error("Bot failed to reach READY state within %.0fs — "
                              "forcing close so supervisor can restart.",
                              startup_timeout)
                    try:
                        await state.bot.close()
                    except Exception as e:
                        log.error("bot.close() failed: %s", e)
                    return
                if not reported_startup:
                    log.warning("Bot still not READY after %.0fs — "
                                "watchdog is watching.",
                                time.time() - startup_at)
                    reported_startup = True
                continue
            # Once ready, watch bot.latency. In discord.py-self, latency is
            # time.monotonic() - last_heartbeat_ack_time, so it GROWS when no
            # new ACK arrives. If it returns inf, the WS is gone entirely.
            try:
                lat = state.bot.latency
            except Exception:
                continue
            # NaN (lat != lat) — never had a heartbeat; treat as stale.
            # inf — WS is gone; treat as stale.
            if lat != lat or lat == float("inf"):
                stale_for = time.time() - last_good_latency
                if stale_for > grace:
                    log.error("Bot latency is inf/NaN for %.0fs — "
                              "forcing close.", stale_for)
                    try:
                        await state.bot.close()
                    except Exception as e:
                        log.error("bot.close() failed: %s", e)
                    return
                continue
            # Real latency number — if small, the bot is healthy.
            if lat < grace:
                last_good_latency = time.time()
            else:
                stale_for = time.time() - last_good_latency
                if stale_for > grace:
                    log.error("Bot heartbeat stale for %.0fs (latency=%.1fs) — "
                              "forcing close.", stale_for, lat)
                    try:
                        await state.bot.close()
                    except Exception as e:
                        log.error("bot.close() failed: %s", e)
                    return

    started_at = time.time()
    try:
        state.running = True
        state.started_at = started_at
        # Schedule the watchdog on the bot's event loop.
        state.loop.create_task(_heartbeat_watchdog())
        state.loop.run_until_complete(bot_module.bot.start(bot_module.DISCORD_USER_TOKEN))
    except Exception as e:
        state.last_error = f"{type(e).__name__}: {e}"
        log.error("Bot crashed: %s", state.last_error)
    finally:
        state.running = False
        state.started_at = None
        log.info("Bot thread exited.")
        _bot_lock.release()
        _maybe_restart(started_at)


def _maybe_restart(started_at: float) -> None:
    """Bring the bot back up if it died on its own (not via the Stop button)."""
    if state.stop_requested or not AUTO_RESTART:
        return

    # It stayed up for a while before dying: that is a blip, not a broken
    # setup, so it gets a fresh budget of restart attempts.
    if time.time() - started_at > 300:
        state.restarts = 0

    if AUTO_RESTART_MAX > 0 and state.restarts >= AUTO_RESTART_MAX:
        log.error("Bot keeps dying and AUTO_RESTART_MAX (%d) is reached — giving up. "
                  "Fix whatever is in the logs, then press Start Bot.",
                  AUTO_RESTART_MAX)
        return

    state.restarts += 1
    # Back off a little on every consecutive failure, but never so far that
    # the bot stays quiet for long.
    delay = min(AUTO_RESTART_BACKOFF_MAX, AUTO_RESTART_DELAY * min(state.restarts, 6))
    log.warning("Bot stopped unexpectedly — reconnecting in %.0fs (attempt %d%s).",
                delay, state.restarts,
                f"/{AUTO_RESTART_MAX}" if AUTO_RESTART_MAX > 0 else "")

    def _restart() -> None:
        time.sleep(delay)
        if state.stop_requested:
            return
        ok, msg = start_bot("auto")
        if ok:
            log.info("Auto-restart: %s", msg)
        else:
            log.warning("Auto-restart failed: %s", msg)

    threading.Thread(target=_restart, daemon=True, name="bot-restart").start()


def start_bot(source: str = "manual") -> tuple[bool, str]:
    """Start the Discord bot in its background thread.

    `source` is "auto" (startup / crash recovery) or "manual" (dashboard
    button) and is reported back on /api/status so the dashboard can show
    whether the bot came up on its own.
    """
    if state.running:
        return False, "Bot is already running"
    if not os.getenv("DISCORD_USER_TOKEN"):
        return False, "DISCORD_USER_TOKEN not set in .env"
    if not _bot_lock.acquire():
        log.warning("Another process already holds the Discord connection — "
                    "not starting a second bot on this account.")
        return False, "Another process is already running the bot"

    if source == "manual":
        state.restarts = 0
    state.last_error = None
    state.stop_requested = False
    state.started_by = source
    state.running = True
    state.started_at = time.time()
    state.thread = threading.Thread(target=_bot_thread_main, daemon=True, name="discord-bot")
    state.thread.start()
    return True, "Bot starting..."


def stop_bot() -> tuple[bool, str]:
    """Stop the running bot and wait for the thread to finish."""
    state.stop_requested = True
    if not state.running or not state.bot or not state.loop:
        return False, "Bot is not running"
    try:
        # Schedule the close on the bot's loop, but do NOT wait on the returned
        # future. Closing the gateway ends the bot thread's run_until_complete,
        # which stops the loop before the future's completion callback can run
        # — so .result() would always time out even though the shutdown worked.
        # Waiting on the bot thread is what actually observes the shutdown.
        asyncio.run_coroutine_threadsafe(state.bot.close(), state.loop)
        if state.thread:
            state.thread.join(timeout=15)
        if state.thread and state.thread.is_alive():
            # Stuck thread — at least reset the dashboard state so a fresh
            # Start Bot can take over. The leaked thread will be cleaned up
            # when the process exits.
            state.running = False
            state.started_at = None
            log.error("Bot did not stop within 15s — state reset, "
                      "stuck thread leaked (will exit with process).")
            return False, "Bot did not stop within 15s — state reset"
        state.running = False
        state.started_at = None
        return True, "Bot stopped."
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def maybe_auto_start() -> None:
    """Connect the bot automatically when the web app boots."""
    if not AUTO_START:
        log.info("AUTO_START is off — press Start Bot to connect.")
        return
    if not os.getenv("DISCORD_USER_TOKEN"):
        log.warning("AUTO_START is on but DISCORD_USER_TOKEN is missing — bot not started.")
        return

    def _go() -> None:
        time.sleep(AUTO_START_DELAY)
        ok, msg = start_bot("auto")
        if ok:
            log.info("AUTO_START: %s", msg)
        else:
            log.warning("AUTO_START: %s", msg)

    log.info("AUTO_START is on — bot connects in %.0fs.", AUTO_START_DELAY)
    threading.Thread(target=_go, daemon=True, name="autostart").start()


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/health")
def api_health():
    """A cheap endpoint for the supervisor / for checking the bot is alive."""
    bot_ready = False
    bot_latency = None
    if state.bot is not None:
        try:
            bot_ready = state.bot.is_ready()
            lat = state.bot.latency
            if lat == lat and lat != float("inf"):  # filter NaN and inf
                bot_latency = lat
        except Exception:
            pass
    return jsonify({
        "ok": True,
        "running": state.running,
        "bot_ready": bot_ready,
        "bot_latency": bot_latency,  # seconds since last heartbeat ACK
        "uptime": state.uptime,
        "pid": os.getpid(),
        "restarts": state.restarts,
        "last_error": state.last_error,
    })


@app.route("/api/status")
def api_status():
    cfg = state.module
    return jsonify({
        "running": state.running,
        "uptime": state.uptime,
        "last_error": state.last_error,
        "auto_start": AUTO_START,
        "auto_restart": AUTO_RESTART,
        "started_by": state.started_by,
        "restarts": state.restarts,
        "logs": list(log_capture),
        "config": {
            "ai_provider": getattr(cfg, "AI_PROVIDER", os.getenv("AI_PROVIDER", "")) if cfg else os.getenv("AI_PROVIDER", ""),
            "ai_model": getattr(cfg, "AI_MODEL", os.getenv("AI_MODEL", "")) if cfg else os.getenv("AI_MODEL", ""),
            "target_user_id": getattr(cfg, "TARGET_USER_ID", os.getenv("TARGET_USER_ID", "")) if cfg else os.getenv("TARGET_USER_ID", ""),
            "has_discord_token": bool(os.getenv("DISCORD_USER_TOKEN", "")),
            "has_ai_key": bool(os.getenv("AI_API_KEY", "")),
            "has_hf_token": bool(os.getenv("HUGGINGFACE_TOKEN", "")),
            "ai_targets": [t["label"] for t in cfg.build_ai_targets()] if cfg and hasattr(cfg, "build_ai_targets") else [],
        },
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    ok, msg = start_bot("manual")
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "msg": msg})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    ok, msg = stop_bot()
    if not ok:
        return jsonify({"ok": False, "error": msg}), 400
    return jsonify({"ok": True, "msg": msg})


@app.route("/api/send", methods=["POST"])
def api_send():
    if not state.running or not state.bot or not state.loop:
        return jsonify({"ok": False, "error": "Bot is not running — start it first"}), 400
    data = request.get_json(force=True, silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "No text provided"}), 400

    async def _send() -> tuple[bool, str]:
        if not state.module:
            return False, "Bot module not loaded"
        target_id = state.module.TARGET_USER_ID
        if not target_id:
            return False, "TARGET_USER_ID not set in .env"
        try:
            user = await state.bot.fetch_user(int(target_id))
            dm = await user.create_dm()
            payload = text[:1900]
            await dm.send(payload)
            return True, f"Sent DM to {user} ({len(payload)} chars)"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    try:
        fut = asyncio.run_coroutine_threadsafe(_send(), state.loop)
        ok, msg = fut.result(timeout=15)
        return jsonify({"ok": ok, "msg": msg})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/reload", methods=["POST"])
def api_reload():
    """Reload .env so config edits take effect on next bot start."""
    if state.running:
        return jsonify({"ok": False, "error": "Stop the bot before reloading config"}), 400
    load_dotenv(override=True)
    return jsonify({"ok": True, "msg": "Reloaded .env — start the bot to apply."})


# ---------------------------------------------------------------------------
# Configuration management — edit .env from the dashboard UI
# ---------------------------------------------------------------------------
# Keys the dashboard is allowed to read / write. Anything else in .env is left
# alone (preserved on write).
CONFIG_KEYS = [
    "DISCORD_USER_TOKEN",
    "AI_PROVIDER",
    "AI_API_KEY",
    "AI_MODEL",
    "AI_BASE_URL",
    "HUGGINGFACE_TOKEN",
    "FALLBACK_MODELS",
    "HF_FALLBACK_MODELS",
    "TARGET_USER_ID",
    "STARTUP_MESSAGE",
    "SYSTEM_PROMPT",
    "MAX_HISTORY",
    "MAX_RESPONSE_CHARS",
    "MAX_TOKENS",
    "THINK_BASE_SEC",
    "THINK_MAX_SEC",
    "THINK_TAIL_SEC",
    "TYPING_BASE_SEC",
    "TYPING_PER_CHAR_SEC",
    "TYPING_MAX_SEC",
    "JITTER_SEC",
    "SPLIT_MESSAGES",
    "TEEN_STYLE",
    "EMOJI_CHANCE",
    "REACTION_CHANCE",
    "TYPO_CHANCE",
    "AI_TEMPERATURE",
    "REPLY_CHANCE",
    "REACT_ONLY_CHANCE",
    "USE_REACTIONS",
    "FOLLOWUP_CHANCE",
    "TYPING_PAUSE_CHANCE",
    "PING_REPLY_CHANCE",
    "DEBOUNCE_SEC",
    "MAX_REPLIES_PER_MIN",
    "CHANNEL_WHITELIST",
    "OPENROUTER_REFERER",
    "OPENROUTER_TITLE",
    "AUTO_START",
    "AUTO_START_DELAY",
    "AUTO_RESTART",
    "AUTO_RESTART_DELAY",
    "AUTO_RESTART_MAX",
    "AUTO_RESTART_BACKOFF_MAX",
    "AI_TIMEOUT_SEC",
    "AI_MAX_RETRIES",
    "AI_STREAM",
    "AI_COOLDOWN_SEC",
    "TYPING_TAIL_MAX_SEC",
]
# Keys whose values we never send back to the browser in plaintext.
SECRET_KEYS = {"DISCORD_USER_TOKEN", "AI_API_KEY", "HUGGINGFACE_TOKEN"}

# Default AI model per provider (used by the dashboard for the "use default" hint).
DEFAULT_MODELS = {
    "openrouter": "poolside/laguna-s-2.1:free",
    "huggingface": "meta-llama/Llama-3.3-70B-Instruct",
    "openai": "gpt-4o-mini",
    "custom": "",
}


@app.route("/api/config", methods=["GET"])
def api_config_get():
    """Return current .env values for all whitelisted keys.
    Secrets come back as {set: bool, preview: 'last 4 chars'} only —
    the actual token never leaves the server.
    """
    out = {}
    for k in CONFIG_KEYS:
        v = os.getenv(k, "")
        if k in SECRET_KEYS:
            out[k] = {"set": bool(v), "preview": (v[-4:] if v else "")}
        else:
            out[k] = v
    return jsonify({
        "ok": True,
        "values": out,
        "defaults": DEFAULT_MODELS,
    })


@app.route("/api/config", methods=["POST"])
def api_config_save():
    """Write JSON body values to .env.
    For secret keys: an empty/missing value means 'leave unchanged'.
    For other keys: an empty value clears the entry.
    """
    if state.running:
        return jsonify({"ok": False,
                        "error": "Stop the bot before editing config"}), 400

    data = request.get_json(force=True, silent=True) or {}
    env_path = os.path.join(os.getcwd(), ".env")

    # Make sure .env exists so set_key has something to write to.
    if not os.path.exists(env_path):
        try:
            with open(env_path, "w", encoding="utf-8") as f:
                f.write("# Written by dashboard\n")
        except Exception as e:
            return jsonify({"ok": False,
                            "error": f"Could not create .env: {e}"}), 500

    written = []
    skipped = []
    for k in CONFIG_KEYS:
        if k not in data:
            continue
        new_val = data[k]
        if new_val is None:
            new_val = ""
        new_val = str(new_val)
        # Secrets: empty value = 'leave unchanged'
        if k in SECRET_KEYS and new_val == "":
            existing = os.getenv(k, "")
            if existing:
                skipped.append(k)
                continue
            # else: fall through and write empty (still sets the key, value is empty)
        try:
            set_key(env_path, k, new_val, quote_mode="auto")
            written.append(k)
        except Exception as e:
            return jsonify({"ok": False,
                            "error": f"Failed to write {k}: {e}"}), 500

    # Reload env vars in this running process so subsequent bot starts see the
    # new values.
    load_dotenv(override=True)

    msg = f"Saved {len(written)} value(s)."
    if skipped:
        msg += f" Kept existing: {', '.join(skipped)}."
    msg += " Click 'Start Bot' to apply."
    return jsonify({"ok": True, "msg": msg, "written": written, "skipped": skipped})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
# Connect the bot on its own (AUTO_START=1) so nothing has to be clicked
# after a reboot. This runs at import time, so it also works under gunicorn
# (where __main__ never runs).
maybe_auto_start()


def _write_pid_file() -> str:
    """Record our PID so the scripts can find (and stop) us."""
    path = os.path.join(LOG_DIR, "app.pid")
    try:
        with open(path, "w", encoding="ascii") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass
    return path


def _serve(port: int, attempts: int = 10) -> int:
    """Serve the dashboard, moving to the next port if this one is busy.

    A stale process holding port 5000 used to be the difference between "the
    bot is running" and "the bot died silently at startup".
    """
    from waitress import serve

    current = port
    for attempt in range(attempts):
        try:
            log.info("Serving on http://0.0.0.0:%d (waitress)", current)
            serve(app, host="0.0.0.0", port=current, threads=8)
            return current
        except OSError as e:
            # Port in use (or blocked) - try the next one.
            log.warning("Cannot bind port %d (%s) - trying %d.", current, e, current + 1)
            current += 1
            time.sleep(1)
    raise SystemExit(f"Could not bind any port between {port} and {port + attempts - 1}")


if __name__ == "__main__":
    # One app at a time. A second copy would fight over the port and over the
    # Discord connection.
    if not _app_lock.acquire():
        log.error("Another copy of the dashboard is already running - exiting.")
        raise SystemExit(0)

    pid_path = _write_pid_file()
    port = int(os.environ.get("PORT", 5000))

    try:
        _serve(port)
    except ImportError:
        # waitress is not installed - fall back to the dev server.
        log.info("Serving on http://0.0.0.0:%d (flask dev server)", port)
        app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
    finally:
        try:
            os.remove(pid_path)
        except OSError:
            pass
        _app_lock.release()
