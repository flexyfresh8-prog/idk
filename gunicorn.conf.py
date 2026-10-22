"""Gunicorn config.

The web app and the Discord bot share one process, and the bot has to connect
exactly once. `post_worker_init` runs inside the worker AFTER the fork, which
is the only safe place to start the bot thread: starting it at import time
would run it in gunicorn's master process instead.

The app's own single-instance lock (.bot.lock) guarantees that a second
worker - or a second copy of the app running on the same machine - does not
connect a second bot to the same Discord account. Two bots on one account is
the fastest way to get banned.
"""


def post_worker_init(worker):
    from app import maybe_auto_start

    maybe_auto_start()
