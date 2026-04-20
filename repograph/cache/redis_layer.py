"""Redis hot cache layer — optional, degrades gracefully if Redis is unavailable."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

LOGGER = logging.getLogger(__name__)

# TTL constants (seconds)
TTL_SUMMARY = int(os.getenv("REPOGRAPH_CACHE_TTL_SUMMARY", "3600"))       # 1 hour
TTL_WORKING_SET = int(os.getenv("REPOGRAPH_CACHE_TTL_WS", "600"))         # 10 min
TTL_SESSION = int(os.getenv("REPOGRAPH_CACHE_TTL_SESSION", "300"))        # 5 min
TTL_VERIFY = int(os.getenv("REPOGRAPH_CACHE_TTL_VERIFY", "300"))          # 5 min

REDIS_URL = os.getenv("REPOGRAPH_REDIS_URL", "redis://localhost:6379/0")

_client = None
_available: bool | None = None


def _get_client():
    global _client, _available
    if _available is False:
        return None
    if _client is not None:
        return _client
    try:
        import redis as _redis
        _client = _redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        _client.ping()
        _available = True
        LOGGER.info("Redis connected: %s", REDIS_URL)
    except Exception as exc:
        LOGGER.warning("Redis unavailable (%s) — running without cache", exc)
        _available = False
        _client = None
    return _client


def is_available() -> bool:
    return _get_client() is not None


def get(key: str) -> Any | None:
    client = _get_client()
    if not client:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def set(key: str, value: Any, ttl: int = TTL_SUMMARY) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        client.setex(key, ttl, json.dumps(value))
        return True
    except Exception:
        return False


def delete(key: str) -> bool:
    client = _get_client()
    if not client:
        return False
    try:
        client.delete(key)
        return True
    except Exception:
        return False


def delete_pattern(pattern: str) -> int:
    client = _get_client()
    if not client:
        return 0
    try:
        keys = client.keys(pattern)
        if keys:
            return client.delete(*keys)
        return 0
    except Exception:
        return 0


def get_or_set(key: str, loader, ttl: int = TTL_SUMMARY) -> Any:
    """Return cached value or call loader() and cache the result."""
    cached = get(key)
    if cached is not None:
        return cached, True
    value = loader()
    if value is not None:
        set(key, value, ttl)
    return value, False


def status() -> dict[str, Any]:
    client = _get_client()
    if not client:
        return {"available": False, "url": REDIS_URL}
    try:
        info = client.info("server")
        return {
            "available": True,
            "url": REDIS_URL,
            "redis_version": info.get("redis_version"),
        }
    except Exception as exc:
        return {"available": False, "url": REDIS_URL, "error": str(exc)}
