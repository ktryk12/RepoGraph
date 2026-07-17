from __future__ import annotations

import threading
import time
from typing import Any

from application.ports import DedupeStore


class RedisDedupeStore(DedupeStore):
    def __init__(
        self,
        *,
        redis_url: str | None,
        namespace: str = "request_gate",
        allow_in_memory_fallback: bool = True,
    ) -> None:
        self._namespace = str(namespace).strip() or "request_gate"
        self._allow_in_memory_fallback = bool(allow_in_memory_fallback)
        self._redis = self._connect(redis_url=redis_url)
        self._memory_lock = threading.Lock()
        self._memory: dict[str, float] = {}

    def claim(self, *, key: str, ttl_seconds: int) -> bool:
        ttl = max(1, int(ttl_seconds))
        redis_key = f"{self._namespace}:{key}"
        if self._redis is not None:
            try:
                return bool(self._redis.set(redis_key, "1", nx=True, ex=ttl))
            except Exception:
                if not self._allow_in_memory_fallback:
                    raise
        if not self._allow_in_memory_fallback:
            return False
        return self._claim_in_memory(redis_key=redis_key, ttl_seconds=ttl)

    def backend(self) -> str:
        if self._redis is not None:
            return "redis"
        if self._allow_in_memory_fallback:
            return "memory"
        return "unavailable"

    def _claim_in_memory(self, *, redis_key: str, ttl_seconds: int) -> bool:
        now = time.time()
        with self._memory_lock:
            expired = [k for k, until in self._memory.items() if until <= now]
            for old in expired:
                self._memory.pop(old, None)
            current = self._memory.get(redis_key)
            if current is not None and current > now:
                return False
            self._memory[redis_key] = now + ttl_seconds
            return True

    @staticmethod
    def _connect(*, redis_url: str | None) -> Any | None:
        raw_url = str(redis_url or "").strip()
        if not raw_url:
            return None
        try:
            import redis  # type: ignore
        except Exception:
            return None
        try:
            client = redis.Redis.from_url(raw_url)
            client.ping()
            return client
        except Exception:
            return None

