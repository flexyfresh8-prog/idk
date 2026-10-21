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
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import threading
import time
import sys
from collections import deque
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv, set_key

# ---------------------------------------------------------------------------
# Flask app + shared state
# ---------------------------------------------------------------------------
app = Flask(__name__, template_folder="templates", static_folder="static")
load_dotenv(override=True)

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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("dashboard")


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

    @property
    def uptime(self) -> int:
        if not self.started_at or not self.running:
            return 0
        return int(time.time() - self.started_at)


state = BotState()


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

    try:
        state.running = True
        state.started_at = time.time()
        state.loop.run_until_complete(bot_module.bot.start(bot_module.DISCORD_USER_TOKEN))
    except Exception as e:
        state.last_error = f"{type(e).__name__}: {e}"
        log.error("Bot crashed: %s", state.last_error)
    finally:
        state.running = False
        state.started_at = None
        log.info("Bot thread exited.")


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/api/status")
def api_status():
    cfg = state.module
    return jsonify({
        "running": state.running,
        "uptime": state.uptime,
        "last_error": state.last_error,
        "logs": list(log_capture),
        "config": {
            "ai_provider": getattr(cfg, "AI_PROVIDER", os.getenv("AI_PROVIDER", "")) if cfg else os.getenv("AI_PROVIDER", ""),
            "ai_model": getattr(cfg, "AI_MODEL", os.getenv("AI_MODEL", "")) if cfg else os.getenv("AI_MODEL", ""),
            "target_user_id": getattr(cfg, "TARGET_USER_ID", os.getenv("TARGET_USER_ID", "")) if cfg else os.getenv("TARGET_USER_ID", ""),
            "has_discord_token": bool(os.getenv("DISCORD_USER_TOKEN", "")),
            "has_ai_key": bool(os.getenv("AI_API_KEY", "")),
        },
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    if state.running:
        return jsonify({"ok": False, "error": "Bot is already running"}), 400
    if not os.getenv("DISCORD_USER_TOKEN"):
        return jsonify({"ok": False, "error": "DISCORD_USER_TOKEN not set in .env"}), 400
    state.last_error = None
    state.thread = threading.Thread(target=_bot_thread_main, daemon=True)
    state.thread.start()
    return jsonify({"ok": True, "msg": "Bot starting..."})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    if not state.running or not state.bot or not state.loop:
        return jsonify({"ok": False, "error": "Bot is not running"}), 400
    try:
        asyncio.run_coroutine_threadsafe(state.bot.close(), state.loop).result(timeout=10)
        return jsonify({"ok": True, "msg": "Bot stopping..."})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


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
    "TARGET_USER_ID",
    "STARTUP_MESSAGE",
    "SYSTEM_PROMPT",
    "MAX_HISTORY",
    "MAX_RESPONSE_CHARS",
    "TYPING_BASE_SEC",
    "TYPING_PER_CHAR_SEC",
    "TYPING_MAX_SEC",
    "CHANNEL_WHITELIST",
    "OPENROUTER_REFERER",
    "OPENROUTER_TITLE",
]
# Keys whose values we never send back to the browser in plaintext.
SECRET_KEYS = {"DISCORD_USER_TOKEN", "AI_API_KEY"}

# Default AI model per provider (used by the dashboard for the "use default" hint).
DEFAULT_MODELS = {
    "openrouter": "meta-llama/llama-3.2-3b-instruct:free",
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
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # threaded=True so multiple browser requests don't block on the bot thread
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
