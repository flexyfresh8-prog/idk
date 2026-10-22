"""
Single-instance file lock, shared by every entry point.

Two bots on one Discord user account is the fastest way to get that account
banned, and this project can easily end up running twice: the scheduled task
plus a manual run, the supervisor plus a leftover process, two gunicorn
workers, or the user double-clicking a .bat.

The lock is held by an open file descriptor, so the operating system releases
it the instant the owning process dies. There is no stale lock to clean up by
hand - a leftover .lock file on disk is harmless.
"""
from __future__ import annotations

import os
from typing import Optional


class SingleInstanceLock:
    """An OS-level lock so only one process can hold it at a time."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fh = None
        self._held = False

    # -- internals ---------------------------------------------------------
    def _open(self):
        try:
            try:
                return open(self.path, "r+b")
            except FileNotFoundError:
                return open(self.path, "w+b")
        except OSError:
            return None

    def _lock(self, fh) -> bool:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                fh.write(b"1")
                fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(self, fh) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    # -- public API --------------------------------------------------------
    def acquire(self) -> bool:
        """Take the lock. Returns False when somebody else already has it."""
        if self._held:
            return True
        fh = self._open()
        if fh is None:
            # Cannot even open the file - do not block the app over it.
            return False
        if not self._lock(fh):
            try:
                fh.close()
            except OSError:
                pass
            return False
        self._fh = fh
        self._held = True
        return True

    def release(self) -> None:
        if not self._held or self._fh is None:
            return
        try:
            self._unlock(self._fh)
        finally:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
            self._held = False

    def is_held(self) -> bool:
        """True when this object currently owns the lock."""
        return self._held


def is_locked(path: str) -> bool:
    """True when SOME process currently holds the lock at `path`.

    Used by the supervisor to notice that another instance is already serving
    the bot, instead of restarting a duplicate every few seconds.
    """
    lock = SingleInstanceLock(path)
    if lock.acquire():
        lock.release()
        return False
    return True
