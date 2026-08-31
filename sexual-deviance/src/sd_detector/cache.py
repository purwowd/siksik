from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Any, Optional


class ResultCache:
    """LRU cache in-memory berdasarkan hash konten + mode."""

    def __init__(self, max_size: int = 2048, ttl_sec: float = 3600.0) -> None:
        self.max_size = max_size
        self.ttl_sec = ttl_sec
        self._store: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    @staticmethod
    def key(content: bytes, mode: str) -> str:
        digest = hashlib.sha256(content).hexdigest()
        return f"{mode}:{digest}"

    def get(self, key: str) -> Optional[dict[str, Any]]:
        entry = self._store.get(key)
        if not entry:
            return None
        ts, value = entry
        if time.time() - ts > self.ttl_sec:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: dict[str, Any]) -> None:
        if key in self._store:
            del self._store[key]
        self._store[key] = (time.time(), value)
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def stats(self) -> dict[str, int]:
        return {"size": len(self._store), "max_size": self.max_size}
