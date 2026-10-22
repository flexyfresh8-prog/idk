"""
Persistent memory storage for the Discord AI self-bot.
Stores conversation history, user facts, and long-term memories to disk.
"""
from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

BASE_DIR = Path(__file__).parent
MEM_DIR = BASE_DIR / "mem"
MSG_DIR = MEM_DIR / "msg"
STORH_DIR = MEM_DIR / "storh"
THINGS_DIR = MEM_DIR / "thingsitold"

for d in (MSG_DIR, STORH_DIR, THINGS_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    """Sanitize a name for use as a filename."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


class MemoryStore:
    """Handles persistent storage for different memory types."""

    def __init__(self):
        self.msg_dir = MSG_DIR
        self.storh_dir = STORH_DIR
        self.things_dir = THINGS_DIR

    # ---------- Message History (per channel) ----------
    def _msg_path(self, channel_id: int) -> Path:
        return self.msg_dir / f"channel_{channel_id}.json"

    def save_messages(self, channel_id: int, messages: Deque[dict], max_len: int) -> None:
        """Save conversation history for a channel."""
        path = self._msg_path(channel_id)
        data = {
            "channel_id": channel_id,
            "updated": time.time(),
            "messages": list(messages)[-max_len:],
        }
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[memory] Failed to save messages for channel {channel_id}: {e}")

    def load_messages(self, channel_id: int, max_len: int) -> Deque[dict]:
        """Load conversation history for a channel."""
        path = self._msg_path(channel_id)
        if not path.exists():
            return deque(maxlen=max_len)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            msgs = data.get("messages", [])
            return deque(msgs[-max_len:], maxlen=max_len)
        except Exception as e:
            print(f"[memory] Failed to load messages for channel {channel_id}: {e}")
            return deque(maxlen=max_len)

    # ---------- Long-term Storage (user facts, preferences, etc.) ----------
    def _storh_path(self, key: str) -> Path:
        return self.storh_dir / f"{_safe_name(key)}.json"

    def save_storh(self, key: str, value: Any) -> None:
        """Save a persistent key-value entry."""
        path = self._storh_path(key)
        data = {"key": key, "value": value, "updated": time.time()}
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[memory] Failed to save storh {key}: {e}")

    def load_storh(self, key: str, default: Any = None) -> Any:
        """Load a persistent key-value entry."""
        path = self._storh_path(key)
        if not path.exists():
            return default
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("value", default)
        except Exception as e:
            print(f"[memory] Failed to load storh {key}: {e}")
            return default

    def list_storh(self) -> List[str]:
        """List all stored keys."""
        try:
            return [p.stem for p in self.storh_dir.glob("*.json")]
        except Exception:
            return []

    def delete_storh(self, key: str) -> bool:
        """Delete a stored entry."""
        path = self._storh_path(key)
        try:
            if path.exists():
                path.unlink()
                return True
        except Exception as e:
            print(f"[memory] Failed to delete storh {key}: {e}")
        return False

    # ---------- Things I Told (long-term memory of bot's own statements) ----------
    def _things_path(self, channel_id: int) -> Path:
        return self.things_dir / f"channel_{channel_id}.json"

    def save_thing_told(self, channel_id: int, text: str, max_items: int = 100) -> None:
        """Record something the bot said, to avoid repetition."""
        path = self._things_path(channel_id)
        items = self.load_things_told(channel_id)
        items.append({"text": text, "time": time.time()})
        if len(items) > max_items:
            items = items[-max_items:]
        try:
            path.write_text(json.dumps({"channel_id": channel_id, "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[memory] Failed to save thing told for channel {channel_id}: {e}")

    def load_things_told(self, channel_id: int) -> List[dict]:
        """Load things the bot has said in this channel."""
        path = self._things_path(channel_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("items", [])
        except Exception as e:
            print(f"[memory] Failed to load things told for channel {channel_id}: {e}")
            return []

    def was_told_recently(self, channel_id: int, text: str, window_sec: float = 3600) -> bool:
        """Check if the bot said something similar recently."""
        items = self.load_things_told(channel_id)
        now = time.time()
        lowered = text.lower().strip()
        for item in items:
            if now - item["time"] > window_sec:
                continue
            if lowered in item["text"].lower() or item["text"].lower() in lowered:
                return True
        return False


# Global instance
memory = MemoryStore()