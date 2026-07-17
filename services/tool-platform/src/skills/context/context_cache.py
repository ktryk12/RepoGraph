"""
skill_runtime/context/context_cache.py — Redis-cache for ContextPack (TTL 5 min).

Genbrug: Redis database /3 (samme som context-plane).
Fallback: in-memory dict hvis Redis ikke er tilgængeligt.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)
_REDIS_URL = os.getenv("CONTEXT_PLANE_REDIS_URL", "redis://localhost:6379/3")
_TTL       = int(os.getenv("SKILL_CONTEXT_CACHE_TTL", "300"))  # 5 min


class ContextCache:
    def __init__(self) -> None:
        self._redis  = None
        self._memory: Dict[str, tuple] = {}  # key → (value, expires_at)
        self._connect()

    def _connect(self) -> None:
        try:
            import redis
            self._redis = redis.from_url(_REDIS_URL, decode_responses=True, socket_timeout=2)
            self._redis.ping()
        except Exception as exc:
            _log.warning("context_cache_redis_unavailable error=%s — using memory fallback", exc)
            self._redis = None

    def _key(self, skill_id: str, prompt: str) -> str:
        h = hashlib.sha256(f"{skill_id}:{prompt}".encode()).hexdigest()[:16]
        return f"skill_ctx:{h}"

    def get(self, skill_id: str, prompt: str) -> Optional[Dict[str, Any]]:
        key = self._key(skill_id, prompt)
        if self._redis:
            try:
                raw = self._redis.get(key)
                return json.loads(raw) if raw else None
            except Exception:
                pass
        # Memory fallback
        entry = self._memory.get(key)
        if entry and entry[1] > time.monotonic():
            return entry[0]
        self._memory.pop(key, None)
        return None

    def set(self, skill_id: str, prompt: str, pack: Dict[str, Any]) -> None:
        key = self._key(skill_id, prompt)
        if self._redis:
            try:
                self._redis.setex(key, _TTL, json.dumps(pack, ensure_ascii=True))
                return
            except Exception:
                pass
        # Memory fallback (cap at 500 entries)
        if len(self._memory) >= 500:
            oldest = min(self._memory, key=lambda k: self._memory[k][1])
            del self._memory[oldest]
        self._memory[key] = (pack, time.monotonic() + _TTL)
