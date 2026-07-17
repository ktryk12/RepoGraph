from __future__ import annotations

from typing import Any


class ReputationTracker:
    DEFAULT = 1.0
    MIN = 0.5
    MAX = 2.0
    _TTL_SECONDS = 7 * 24 * 60 * 60

    def __init__(self, *, redis_client: Any) -> None:
        self.redis = redis_client

    async def get(self, agent_id: str) -> float:
        key = self._key(agent_id)
        raw = await self.redis.get(key)
        if raw is None:
            return float(self.DEFAULT)
        try:
            value = float(_as_text(raw))
        except Exception:
            return float(self.DEFAULT)
        return _clamp(value, low=self.MIN, high=self.MAX)

    async def update(self, agent_id: str, was_majority: bool) -> float:
        current = await self.get(agent_id)
        factor = 1.05 if bool(was_majority) else 0.98
        updated = _clamp(current * factor, low=self.MIN, high=self.MAX)
        await self.redis.setex(self._key(agent_id), self._TTL_SECONDS, str(updated))
        return updated

    @staticmethod
    def _key(agent_id: str) -> str:
        return f"rep:{str(agent_id)}"


def _clamp(value: float, *, low: float, high: float) -> float:
    if value < low:
        return float(low)
    if value > high:
        return float(high)
    return float(value)


def _as_text(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")

