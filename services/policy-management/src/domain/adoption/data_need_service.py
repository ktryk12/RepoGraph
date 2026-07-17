from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping
import os
import time

import yaml


DEFAULT_DATA_NEED_PATH = Path(__file__).with_name("data_need.yaml")
TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off"}


class DataNeedViolation(RuntimeError):
    def __init__(self, *, reason: str, scope_id: str) -> None:
        self.reason = str(reason)
        self.scope_id = str(scope_id)
        super().__init__(f"data_need_blocked reason={self.reason} scope_id={self.scope_id}")


@dataclass(frozen=True)
class DataNeedState:
    path: Path
    schema_version: int
    version: str
    enabled: bool
    max_discoveries_per_scope: int
    cooldown_seconds: float


@dataclass(frozen=True)
class DataNeedDecision:
    allowed: bool
    scope_id: str
    reason: str
    budget_limit: int
    budget_used: int
    budget_remaining: int
    cooldown_seconds: float
    cooldown_remaining_seconds: float
    next_allowed_at: str | None
    evaluated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "scope_id": str(self.scope_id),
            "reason": str(self.reason),
            "budget_limit": int(self.budget_limit),
            "budget_used": int(self.budget_used),
            "budget_remaining": int(self.budget_remaining),
            "cooldown_seconds": float(self.cooldown_seconds),
            "cooldown_remaining_seconds": float(self.cooldown_remaining_seconds),
            "next_allowed_at": self.next_allowed_at,
            "evaluated_at": self.evaluated_at,
        }


class DataNeedService:
    """
    Gate discovery/ingest-style operations with cooldown + budget per scope.

    Scope is caller-defined (for example run_id, context_id, or episode group id).
    """

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else DEFAULT_DATA_NEED_PATH
        self._state = load_data_need(self._path)
        self._env_override = env
        self._usage: Dict[str, list[float]] = {}

    @property
    def state(self) -> DataNeedState:
        return self._state

    def reload(self) -> DataNeedState:
        self._state = load_data_need(self._path)
        return self._state

    def reset(self, *, scope_id: str | None = None) -> None:
        if scope_id is None:
            self._usage = {}
            return
        key = str(scope_id).strip()
        if key:
            self._usage.pop(key, None)

    def evaluate(
        self,
        *,
        scope_id: str,
        max_discoveries: int | None = None,
        cooldown_seconds: float | None = None,
        now_ts: float | None = None,
    ) -> DataNeedDecision:
        key = str(scope_id or "").strip() or "default"
        now = _coerce_now_ts(now_ts)
        history = list(self._usage.get(key, []))
        used = len(history)

        enabled = self._enabled()
        budget_limit = _resolve_budget_limit(
            explicit=max_discoveries,
            env_value=self._env().get("DATA_NEED_MAX_DISCOVERIES"),
            default=self._state.max_discoveries_per_scope,
        )
        cooldown = _resolve_cooldown(
            explicit=cooldown_seconds,
            env_value=self._env().get("DATA_NEED_COOLDOWN_SECONDS"),
            default=self._state.cooldown_seconds,
        )
        budget_remaining = max(0, int(budget_limit - used))

        cooldown_remaining = 0.0
        next_allowed_at = None
        if history and cooldown > 0:
            elapsed = max(0.0, float(now - history[-1]))
            cooldown_remaining = max(0.0, float(cooldown - elapsed))
            if cooldown_remaining > 0:
                next_allowed_at = _iso_utc(now + cooldown_remaining)

        if not enabled:
            reason = "disabled"
            allowed = False
        elif used >= budget_limit:
            reason = "budget_exhausted"
            allowed = False
        elif cooldown_remaining > 0:
            reason = "cooldown_active"
            allowed = False
        else:
            reason = "allowed"
            allowed = True

        return DataNeedDecision(
            allowed=bool(allowed),
            scope_id=key,
            reason=reason,
            budget_limit=int(budget_limit),
            budget_used=int(used),
            budget_remaining=int(max(0, budget_limit - used)),
            cooldown_seconds=float(cooldown),
            cooldown_remaining_seconds=float(cooldown_remaining),
            next_allowed_at=next_allowed_at,
            evaluated_at=_iso_utc(now),
        )

    def acquire(
        self,
        *,
        scope_id: str,
        max_discoveries: int | None = None,
        cooldown_seconds: float | None = None,
        now_ts: float | None = None,
    ) -> DataNeedDecision:
        decision = self.evaluate(
            scope_id=scope_id,
            max_discoveries=max_discoveries,
            cooldown_seconds=cooldown_seconds,
            now_ts=now_ts,
        )
        if decision.allowed:
            key = str(decision.scope_id)
            now = _coerce_now_ts(now_ts)
            history = list(self._usage.get(key, []))
            history.append(float(now))
            self._usage[key] = history
            return DataNeedDecision(
                allowed=True,
                scope_id=decision.scope_id,
                reason=decision.reason,
                budget_limit=decision.budget_limit,
                budget_used=decision.budget_used + 1,
                budget_remaining=max(0, decision.budget_limit - (decision.budget_used + 1)),
                cooldown_seconds=decision.cooldown_seconds,
                cooldown_remaining_seconds=0.0,
                next_allowed_at=(
                    _iso_utc(now + float(decision.cooldown_seconds))
                    if float(decision.cooldown_seconds) > 0
                    else None
                ),
                evaluated_at=decision.evaluated_at,
            )
        return decision

    def require(
        self,
        *,
        scope_id: str,
        max_discoveries: int | None = None,
        cooldown_seconds: float | None = None,
        now_ts: float | None = None,
    ) -> DataNeedDecision:
        decision = self.acquire(
            scope_id=scope_id,
            max_discoveries=max_discoveries,
            cooldown_seconds=cooldown_seconds,
            now_ts=now_ts,
        )
        if not decision.allowed:
            raise DataNeedViolation(reason=decision.reason, scope_id=decision.scope_id)
        return decision

    def _enabled(self) -> bool:
        override = _parse_optional_bool(self._env().get("DATA_NEED_ENABLED"))
        if override is not None:
            return bool(override)
        return bool(self._state.enabled)

    def _env(self) -> Mapping[str, str]:
        if self._env_override is not None:
            return self._env_override
        return os.environ


_DATA_NEED_SERVICE: DataNeedService | None = None


def get_data_need_service(
    *,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    reload: bool = False,
    reset_usage: bool = False,
) -> DataNeedService:
    global _DATA_NEED_SERVICE
    if _DATA_NEED_SERVICE is None or path is not None or env is not None:
        _DATA_NEED_SERVICE = DataNeedService(path=path, env=env)
        if reset_usage:
            _DATA_NEED_SERVICE.reset()
        return _DATA_NEED_SERVICE
    if reload:
        _DATA_NEED_SERVICE.reload()
    if reset_usage:
        _DATA_NEED_SERVICE.reset()
    return _DATA_NEED_SERVICE


def load_data_need(path: str | Path) -> DataNeedState:
    target = Path(path).resolve()
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        payload = {}

    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}

    schema_version = _safe_int(payload.get("schema_version"), default=1)
    version = str(payload.get("version") or "unknown")
    enabled = _parse_bool(defaults.get("enabled"), default=True)
    max_discoveries = _safe_int(defaults.get("max_discoveries_per_scope"), default=25)
    if max_discoveries < 1:
        max_discoveries = 1
    cooldown = _safe_float(defaults.get("cooldown_seconds"), default=0.0)
    if cooldown < 0:
        cooldown = 0.0
    return DataNeedState(
        path=target,
        schema_version=schema_version,
        version=version,
        enabled=bool(enabled),
        max_discoveries_per_scope=int(max_discoveries),
        cooldown_seconds=float(cooldown),
    )


def _coerce_now_ts(now_ts: float | None) -> float:
    if isinstance(now_ts, (int, float)):
        return float(now_ts)
    return float(time.time())


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_budget_limit(*, explicit: int | None, env_value: Any, default: int) -> int:
    if isinstance(explicit, int):
        return max(1, int(explicit))
    env_limit = _safe_int(env_value, default=default)
    return max(1, int(env_limit))


def _resolve_cooldown(*, explicit: float | None, env_value: Any, default: float) -> float:
    if isinstance(explicit, (int, float)):
        return max(0.0, float(explicit))
    env_cd = _safe_float(env_value, default=default)
    return max(0.0, float(env_cd))


def _parse_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    return None


def _parse_bool(value: Any, *, default: bool) -> bool:
    parsed = _parse_optional_bool(value)
    if parsed is None:
        return bool(default)
    return bool(parsed)


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)
