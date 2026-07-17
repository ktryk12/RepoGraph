"""
claim_detector/deduper.py — Redis-based deduplication (mønster fra request-gate).

Key: SHA-256 af normaliseret claim-tekst.
TTL: 24 timer (configurable via CLAIM_DEDUP_TTL_SECONDS).
Fallback: in-memory set hvis Redis er utilgængeligt.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Optional, Set

_log = logging.getLogger("claim_detector.deduper")

_TTL = int(os.getenv("CLAIM_DEDUP_TTL_SECONDS", str(60 * 60 * 24)))
_REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/5")
_KEY_PREFIX = "claim_dedup:"


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text


def _fingerprint(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode()).hexdigest()


class Deduper:
    """
    Redis-backed deduplicator. Falls back to in-memory set without raising.
    Thread-safe: Redis operations are atomic via SETNX.
    """

    def __init__(self) -> None:
        self._redis = self._connect()
        self._memory: Set[str] = set()

    def is_duplicate(self, text: str) -> bool:
        key = _KEY_PREFIX + _fingerprint(text)
        if self._redis:
            try:
                return bool(self._redis.exists(key))
            except Exception:
                pass
        return key in self._memory

    def mark_seen(self, text: str) -> None:
        key = _KEY_PREFIX + _fingerprint(text)
        if self._redis:
            try:
                self._redis.setex(key, _TTL, "1")
                return
            except Exception:
                pass
        self._memory.add(key)
        # Prevent unbounded growth of in-memory fallback
        if len(self._memory) > 10_000:
            self._memory.clear()

    @staticmethod
    def _connect() -> Optional[object]:
        try:
            import redis
            client = redis.from_url(_REDIS_URL, decode_responses=True)
            client.ping()
            return client
        except Exception as exc:
            _log.warning("deduper_redis_unavailable error=%s — using in-memory fallback", exc)
            return None
