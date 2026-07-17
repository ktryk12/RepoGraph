from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, TypeVar
import json
import logging
import os
import time

import yaml

from policy.constitution_service import get_constitution_service


logger = logging.getLogger(__name__)
T = TypeVar("T")
DEFAULT_PERF_BUDGETS_PATH = Path(__file__).with_name("perf_budgets.yaml")


class PerfBudgetViolation(RuntimeError):
    def __init__(self, *, call_name: str, elapsed_ms: float, budget_ms: float) -> None:
        self.call_name = str(call_name)
        self.elapsed_ms = float(elapsed_ms)
        self.budget_ms = float(budget_ms)
        super().__init__(
            f"perf_budget_exceeded call={self.call_name} elapsed_ms={self.elapsed_ms:.3f} budget_ms={self.budget_ms:.3f}"
        )


@dataclass(frozen=True)
class PerfBudgetState:
    path: Path
    schema_version: int
    version: str
    default_budget_ms: float
    hard_mode_default: bool
    hard_mode_ci_default: bool
    telemetry_log_path: str
    call_budgets: Dict[str, float]


class PerfBudgetService:
    """
    Enforce per-call latency budgets with optional hard-fail mode.

    Env overrides:
    - PERF_BUDGET_HARD_MODE=true|false
    - PERF_BUDGET_HARD_MODE_CI=true|false
    - PERF_BUDGET_OVERRUN_LOG=<path>
    """

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else DEFAULT_PERF_BUDGETS_PATH
        self._env_override = env
        self._state = load_perf_budgets(self._path)

    @property
    def state(self) -> PerfBudgetState:
        return self._state

    def reload(self) -> PerfBudgetState:
        self._state = load_perf_budgets(self._path)
        return self._state

    def get_budget_ms(self, call_name: str, *, default: float | None = None) -> float:
        key = str(call_name or "").strip()
        if key in self._state.call_budgets:
            return float(self._state.call_budgets[key])
        if default is not None:
            return float(default)
        return float(self._state.default_budget_ms)

    def hard_mode_enabled(self) -> bool:
        env = self._env()
        explicit = _parse_optional_bool(env.get("PERF_BUDGET_HARD_MODE"))
        if explicit is not None:
            return bool(explicit)

        ci_hard = _parse_optional_bool(env.get("PERF_BUDGET_HARD_MODE_CI"))
        if ci_hard is not None and _in_ci(env):
            return bool(ci_hard)

        if _in_ci(env):
            return bool(self._state.hard_mode_ci_default)
        return bool(self._state.hard_mode_default)

    def telemetry_log_path(self) -> Path:
        env = self._env()
        override = str(env.get("PERF_BUDGET_OVERRUN_LOG", "")).strip()
        if override:
            return Path(override)
        return Path(str(self._state.telemetry_log_path))

    def budgeted_call(
        self,
        call_name: str,
        fn: Callable[[], T],
        budget_ms: float | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
        hard_budget: bool | None = None,
    ) -> T:
        name = str(call_name or "").strip() or "unknown_call"
        budget = float(self.get_budget_ms(name, default=budget_ms) if budget_ms is None else budget_ms)
        hard = self.hard_mode_enabled() if hard_budget is None else bool(hard_budget)

        started = time.perf_counter()
        value = fn()
        elapsed_ms = _elapsed_ms(started)

        if elapsed_ms <= budget:
            return value

        overrun_ms = max(0.0, elapsed_ms - budget)
        event = {
            "event_type": "perf_budget_overrun",
            "timestamp_utc": _now_utc_iso(),
            "call_name": name,
            "budget_ms": round(budget, 3),
            "elapsed_ms": round(elapsed_ms, 3),
            "overrun_ms": round(overrun_ms, 3),
            "hard_budget": bool(hard),
            "metadata": dict(metadata or {}),
        }
        self._emit_overrun(event)
        if hard:
            raise PerfBudgetViolation(call_name=name, elapsed_ms=elapsed_ms, budget_ms=budget)
        return value

    def _emit_overrun(self, event: Dict[str, Any]) -> None:
        logger.warning("telemetry=%s", json.dumps(event, ensure_ascii=True, sort_keys=True, default=str))
        path = self.telemetry_log_path()
        constitution = get_constitution_service()
        constitution.require("write_path", {"path": path, "operation": "perf_budget_overrun_log"})
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=True, sort_keys=True, default=str) + "\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()

    def _env(self) -> Mapping[str, str]:
        if self._env_override is not None:
            return self._env_override
        return os.environ


_PERF_BUDGET_SERVICE: PerfBudgetService | None = None


def get_perf_budget_service(
    *,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    reload: bool = False,
) -> PerfBudgetService:
    global _PERF_BUDGET_SERVICE
    if _PERF_BUDGET_SERVICE is None or path is not None or env is not None:
        _PERF_BUDGET_SERVICE = PerfBudgetService(path=path, env=env)
        return _PERF_BUDGET_SERVICE
    if reload:
        _PERF_BUDGET_SERVICE.reload()
    return _PERF_BUDGET_SERVICE


def budgeted_call(
    name: str,
    fn: Callable[[], T],
    budget_ms: float | None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
    hard_budget: bool | None = None,
) -> T:
    return get_perf_budget_service().budgeted_call(
        call_name=name,
        fn=fn,
        budget_ms=budget_ms,
        metadata=metadata,
        hard_budget=hard_budget,
    )


def load_perf_budgets(path: str | Path) -> PerfBudgetState:
    target = Path(path).resolve()
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        payload = {}

    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    raw_call_budgets = payload.get("call_budgets")
    if not isinstance(raw_call_budgets, dict):
        raw_call_budgets = {}

    call_budgets: Dict[str, float] = {}
    for raw_name, raw_budget in raw_call_budgets.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        parsed = _parse_positive_float(raw_budget)
        if parsed is None:
            continue
        call_budgets[name] = parsed

    default_budget_ms = _parse_positive_float(defaults.get("budget_ms")) or 1500.0
    hard_mode_default = _parse_bool(defaults.get("hard_mode"), default=False)
    hard_mode_ci_default = _parse_bool(defaults.get("hard_mode_ci"), default=False)
    telemetry_log_path = str(defaults.get("telemetry_log_path") or "artifacts/telemetry/perf_budget_overruns.jsonl")
    schema_version = _safe_int(payload.get("schema_version"), default=1)
    version = str(payload.get("version") or "unknown")
    return PerfBudgetState(
        path=target,
        schema_version=schema_version,
        version=version,
        default_budget_ms=default_budget_ms,
        hard_mode_default=hard_mode_default,
        hard_mode_ci_default=hard_mode_ci_default,
        telemetry_log_path=telemetry_log_path,
        call_budgets=call_budgets,
    )


def _parse_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed <= 0:
        return None
    return parsed


def _parse_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
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


def _in_ci(env: Mapping[str, str]) -> bool:
    return _parse_bool(env.get("CI"), default=False)


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - float(started_at)) * 1000.0


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
