from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping, Sequence
import os


@dataclass(frozen=True)
class StagnationStopRules:
    schema_version: int
    version: str
    repeat_window: int
    delta_threshold: float


@dataclass(frozen=True)
class StagnationStopVerdict:
    stop: bool
    reason: str
    repeat_window: int
    repeated_tags: List[str]
    delta: float | None
    delta_threshold: float
    require_score_delta: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop": bool(self.stop),
            "reason": str(self.reason),
            "repeat_window": int(self.repeat_window),
            "repeated_tags": list(self.repeated_tags),
            "delta": (float(self.delta) if isinstance(self.delta, (int, float)) else None),
            "delta_threshold": float(self.delta_threshold),
            "require_score_delta": bool(self.require_score_delta),
        }


class StagnationStopService:
    """
    Shared hard-rule service for deterministic stagnation detection.

    Used by:
    - AutoLoopService
    - Judge stop policy / JudgeAggregator
    """

    def __init__(self, *, env: Mapping[str, str] | None = None) -> None:
        self._env_override = env
        self._rules = self._load_rules()

    @property
    def rules(self) -> StagnationStopRules:
        return self._rules

    def reload(self) -> StagnationStopRules:
        self._rules = self._load_rules()
        return self._rules

    def evaluate(
        self,
        *,
        tag_history: Sequence[Iterable[Any]],
        score_history: Sequence[float | None] | None = None,
        repeat_window: int | None = None,
        delta_threshold: float | None = None,
        require_score_delta: bool = False,
    ) -> StagnationStopVerdict:
        window = int(repeat_window if isinstance(repeat_window, int) else self._rules.repeat_window)
        if window < 2:
            window = 2
        threshold = float(
            delta_threshold
            if isinstance(delta_threshold, (int, float))
            else self._rules.delta_threshold
        )
        if threshold < 0:
            threshold = 0.0

        normalized_history = [_normalize_tags(item) for item in tag_history]
        if len(normalized_history) < window:
            return StagnationStopVerdict(
                stop=False,
                reason="insufficient_history",
                repeat_window=window,
                repeated_tags=[],
                delta=None,
                delta_threshold=threshold,
                require_score_delta=bool(require_score_delta),
            )

        tail = normalized_history[-window:]
        baseline = tail[0]
        if not baseline:
            return StagnationStopVerdict(
                stop=False,
                reason="empty_tags",
                repeat_window=window,
                repeated_tags=[],
                delta=None,
                delta_threshold=threshold,
                require_score_delta=bool(require_score_delta),
            )
        repeated = all(row == baseline for row in tail[1:])
        if not repeated:
            return StagnationStopVerdict(
                stop=False,
                reason="tags_changed",
                repeat_window=window,
                repeated_tags=[],
                delta=None,
                delta_threshold=threshold,
                require_score_delta=bool(require_score_delta),
            )

        delta = _score_delta(score_history)
        if require_score_delta and delta is None:
            return StagnationStopVerdict(
                stop=False,
                reason="score_delta_unavailable",
                repeat_window=window,
                repeated_tags=list(baseline),
                delta=None,
                delta_threshold=threshold,
                require_score_delta=True,
            )

        if delta is not None and delta > threshold:
            return StagnationStopVerdict(
                stop=False,
                reason="delta_above_threshold",
                repeat_window=window,
                repeated_tags=list(baseline),
                delta=float(delta),
                delta_threshold=threshold,
                require_score_delta=bool(require_score_delta),
            )

        return StagnationStopVerdict(
            stop=True,
            reason="stagnation",
            repeat_window=window,
            repeated_tags=list(baseline),
            delta=(float(delta) if isinstance(delta, (int, float)) else None),
            delta_threshold=threshold,
            require_score_delta=bool(require_score_delta),
        )

    def _load_rules(self) -> StagnationStopRules:
        env = self._env()
        repeat_window = _parse_positive_int(env.get("STAGNATION_REPEAT_WINDOW"), default=2)
        delta_threshold = _parse_non_negative_float(env.get("STAGNATION_DELTA_THRESHOLD"), default=0.005)
        return StagnationStopRules(
            schema_version=1,
            version="v1",
            repeat_window=max(2, int(repeat_window)),
            delta_threshold=max(0.0, float(delta_threshold)),
        )

    def _env(self) -> Mapping[str, str]:
        if self._env_override is not None:
            return self._env_override
        return os.environ


_SERVICE: StagnationStopService | None = None


def get_stagnation_stop_service(
    *,
    env: Mapping[str, str] | None = None,
    reload: bool = False,
) -> StagnationStopService:
    global _SERVICE
    if _SERVICE is None or env is not None:
        _SERVICE = StagnationStopService(env=env)
        return _SERVICE
    if reload:
        _SERVICE.reload()
    return _SERVICE


def _normalize_tags(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values:
        tag = str(raw).strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    return sorted(out)


def _score_delta(score_history: Sequence[float | None] | None) -> float | None:
    if not isinstance(score_history, Sequence) or len(score_history) < 2:
        return None
    left = score_history[-2]
    right = score_history[-1]
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    return abs(float(right) - float(left))


def _parse_positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return int(default)
    return parsed if parsed > 0 else int(default)


def _parse_non_negative_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(default)
    return parsed if parsed >= 0.0 else float(default)

