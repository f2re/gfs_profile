from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class FlowState:
    product: str = "profile"
    step: str = "idle"
    point: Any | None = None
    candidates: list[Any] = field(default_factory=list)
    pending_lead: int | None = None
    pending_run: Any | None = None
    lead_page: int = 0
    updated_at: float = field(default_factory=time.time)


class InMemorySessionStore:
    def __init__(self, ttl_seconds: int = 1800) -> None:
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._items: dict[tuple[str, str, str], FlowState] = {}
        self._lock = RLock()

    @staticmethod
    def _key(platform: str, user_id: str, chat_id: str) -> tuple[str, str, str]:
        return str(platform), str(user_id), str(chat_id)

    def get(self, platform: str, user_id: str, chat_id: str) -> FlowState | None:
        key = self._key(platform, user_id, chat_id)
        now = time.time()
        with self._lock:
            state = self._items.get(key)
            if state is None:
                return None
            if now - state.updated_at > self.ttl_seconds:
                self._items.pop(key, None)
                return None
            return state

    def set(self, platform: str, user_id: str, chat_id: str, state: FlowState) -> FlowState:
        state.updated_at = time.time()
        with self._lock:
            self._items[self._key(platform, user_id, chat_id)] = state
        return state

    def clear(self, platform: str, user_id: str, chat_id: str) -> None:
        with self._lock:
            self._items.pop(self._key(platform, user_id, chat_id), None)

    def cleanup(self) -> int:
        now = time.time()
        with self._lock:
            stale = [key for key, state in self._items.items() if now - state.updated_at > self.ttl_seconds]
            for key in stale:
                self._items.pop(key, None)
        return len(stale)
