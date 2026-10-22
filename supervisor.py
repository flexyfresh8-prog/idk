"""
Supervisor — the watchdog that keeps the bot alive.

What it does, in one sentence: run app.py, and if it ever dies, run it again.

Why this exists
---------------
Before this, the auto-start chain was: Windows logon -> scheduled task ->
run_hidden.ps1 -> app.py. Every one of those was a single point of failure.
The task fired once and, if the app died ten minutes later, nothing brought
it back. Worse, run_hidden.ps1 looked for a virtualenv that did not exist
(.venv instead of .venv2), fell back to the system Python, and the app died
on `import discord` — silently, because it was running under pythonw with no
console and no log file.

So this process:
  * runs app.py as a child process, with its output captured to logs\\app.log
  * restarts it whenever it exits, forever, with a capped backoff
  * never runs two apps at once (a lock file does that)
  * writes logs\\supervisor.log so "why did it stop" is always answerable

Usage:
    python supervisor.py            # run in the foreground
    pythonw supervisor.py           # run hidden (what the startup scripts use)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from instancelock import SingleInstanceLock

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

SUPERVISOR_LOG = LOG_DIR / "supervisor.log"
APP_LOCK = BASE_DIR / ".app.lock"
SUPERVISOR_LOCK = BASE_DIR / ".supervisor.lock"

# How long to wait before the first start (gives the network a moment).
START_DELAY = float(os.getenv("SUPERVISOR_START_DELAY", "5"))
# Backoff between restarts when the app keeps dying.
RESTART_DELAY = float(os.getenv("SUPERVISOR_RESTART_DELAY", "10"))
RESTART_DELAY_MAX = float(os.getenv("SUPERVISOR_RESTART_DELAY_MAX", "120"))
# An app that lasted this long is considered healthy - the backoff resets.
HEALTHY_AFTER = float(os.getenv("SUPERVISOR_HEALTHY_AFTER", "120"))
# How often to poll the app's /api/health endpoint while it is running.
HEALTH_POLL_SEC = float(os.getenv("SUPERVISOR_HEALTH_POLL_SEC", "60"))
# If the bot's heartbeat is stale for this many seconds, kill the app.
HEALTH_STALE_GRACE = float(os.getenv("SUPERVISOR_HEALTH_STALE_GRACE", "180"))
# Number of consecutive stale polls before killing (safety against false positives).
HEALTH_STALE_THRESHOLD = int(os.getenv("SUPERVISOR_HEALTH_STALE_THRESHOLD", "2"))
# Port the app listens on.
APP_PORT = int(os.getenv("PORT", "5000"))


def _log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} [supervisor] {message}"
    try:
        with open(SUPERVISOR_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def find_python() -> str:
    """The interpreter that actually has the dependencies installed.

    The old scripts looked for .venv, which did not exist, and fell back to
    the system Python — which has no `discord` module. So: look at the
    project virtualenvs first, and only then the system Python.
    """
    candidates = [
        BASE_DIR / ".venv2" / "Scripts" / "python.exe",
        BASE_DIR / ".venv" / "Scripts" / "python.exe",
        BASE_DIR / ".venv2" / "bin" / "python",
        BASE_DIR / ".venv" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python"


def python_has_deps(python: str) -> bool:
    """Check that this interpreter can import what the app needs."""
    try:
        result = subprocess.run(
            [python, "-c", "import discord, openai, flask"],
            capture_output=True,
            timeout=60,
        )
    except Exception as e:  # noqa: BLE001
        _log(f"Could not check {python}: {e}")
        return False
    if result.returncode != 0:
        _log(f"{python} is missing dependencies: "
             f"{result.stderr.decode(errors='ignore').strip()[-300:]}")
        return False
    return True


def app_is_running() -> bool:
    """True when another copy of the app is already serving."""
    lock = SingleInstanceLock(str(APP_LOCK))
    if lock.acquire():
        lock.release()
        return False
    return True


def _poll_app_health() -> dict | None:
    """Hit the app's /api/health endpoint. Returns the JSON dict, or None on failure."""
    try:
        url = f"http://127.0.0.1:{APP_PORT}/api/health"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception as e:  # noqa: BLE001
        _log(f"Health poll failed: {type(e).__name__}: {e}")
        return None


def main() -> int:
    lock = SingleInstanceLock(str(SUPERVISOR_LOCK))
    if not lock.acquire():
        _log("Another supervisor is already running - nothing to do.")
        return 0

    python = find_python()
    _log(f"Supervisor started (python: {python})")
    if not python_has_deps(python):
        _log("ERROR: no usable Python found. Create the virtualenv with:")
        _log("       python -m venv .venv2 && .venv2\\Scripts\\pip install -r requirements.txt")
        return 1

    if START_DELAY > 0:
        _log(f"Waiting {START_DELAY:.0f}s before the first start.")
        time.sleep(START_DELAY)

    delay = RESTART_DELAY
    while True:
        # If somebody else is already serving the app, do not start a second
        # one - just wait and keep watching.
        if app_is_running():
            _log("The app is already running elsewhere - waiting for it to stop.")
            time.sleep(30)
            continue

        _log("Starting app.py ...")
        started = time.time()
        try:
            # stdout/stderr go to a file: if the app dies before its logging
            # is set up (a bad import, a syntax error), the traceback is
            # still on disk instead of nowhere.
            crash_log = open(LOG_DIR / "app.out.log", "a", encoding="utf-8")
            crash_log.write(f"\n--- app.py started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            crash_log.flush()
            proc = subprocess.Popen(
                [python, "-u", "app.py"],
                cwd=str(BASE_DIR),
                stdout=crash_log,
                stderr=subprocess.STDOUT,
            )
        except Exception as e:  # noqa: BLE001
            _log(f"Could not start app.py: {e}")
            time.sleep(delay)
            delay = min(RESTART_DELAY_MAX, delay * 2)
            continue

        # ---- Watch the running app: poll health every HEALTH_POLL_SEC ----
        # We don't just block on proc.wait() because a stuck-but-alive
        # bot (process alive, asyncio loop frozen, gateway dead) needs an
        # out-of-process watchdog to kill it. The in-process watchdog in
        # app.py handles most cases, but this one is a backup for when the
        # loop itself is wedged.
        stale_streak = 0
        next_poll = time.time() + HEALTH_POLL_SEC
        while True:
            # Did the process exit on its own?
            rc = proc.poll()
            if rc is not None:
                crash_log.close()
                uptime = time.time() - started
                _log(f"app.py exited with code {rc} after {uptime:.0f}s.")
                if uptime >= HEALTHY_AFTER:
                    delay = RESTART_DELAY
                else:
                    delay = min(RESTART_DELAY_MAX, delay * 2)
                break

            # Time for a health poll?
            now = time.time()
            if now < next_poll:
                time.sleep(min(5.0, max(0.5, next_poll - now)))
                continue
            next_poll = now + HEALTH_POLL_SEC

            health = _poll_app_health()
            if health is None:
                # /api/health didn't respond — process might still be
                # booting Flask, or the loop is stuck. Don't kill yet;
                # let proc.poll() catch a real crash.
                stale_streak = 0
                continue

            bot_ready = health.get("bot_ready", False)
            bot_latency = health.get("bot_latency")
            running = health.get("running", False)

            if not running or not bot_ready:
                # Either the bot hasn't started yet, or it just crashed
                # internally. Reset stale streak — app.py's in-process
                # watchdog will handle this.
                stale_streak = 0
                continue

            # If latency is None (no heartbeat yet) or > grace, count it
            # as stale.
            is_stale = (bot_latency is None) or (bot_latency > HEALTH_STALE_GRACE)
            if is_stale:
                stale_streak += 1
                _log(f"Bot heartbeat stale (latency={bot_latency}, "
                     f"stale_streak={stale_streak}/{HEALTH_STALE_THRESHOLD}).")
                if stale_streak >= HEALTH_STALE_THRESHOLD:
                    _log("Killing app.py — bot is alive-but-stuck.")
                    try:
                        proc.terminate()
                        proc.wait(timeout=10)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    crash_log.close()
                    uptime = time.time() - started
                    _log(f"app.py killed after {uptime:.0f}s (was stuck).")
                    if uptime >= HEALTHY_AFTER:
                        delay = RESTART_DELAY
                    else:
                        delay = min(RESTART_DELAY_MAX, delay * 2)
                    break
            else:
                if stale_streak > 0:
                    _log(f"Bot heartbeat recovered (latency={bot_latency}).")
                stale_streak = 0

        _log(f"Restarting app.py in {delay:.0f}s.")
        time.sleep(delay)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        _log("Supervisor stopped.")
        raise SystemExit(0)
