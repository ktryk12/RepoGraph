from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Mapping
import os

from policy.stagnation_stop_service import StagnationStopService, get_stagnation_stop_service


DEFAULT_FATAL_TAGS = (
    "schema_invalid",
    "deterministic_review_error",
    "license_risk",
)


@dataclass(frozen=True)
class JudgeStopRules:
    schema_version: int
    version: str
    max_iterations: int
    stagnation_delta_threshold: float
    fatal_precedence: bool
    fatal_tags: tuple[str, ...]


@dataclass(frozen=True)
class JudgeStopDecision:
    stop: bool
    reason: str
    iteration: int
    max_iterations: int
    stagnation_delta: float | None
    fatal_tags_triggered: List[str]
    hard_fail_tags: List[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stop": bool(self.stop),
            "reason": str(self.reason),
            "iteration": int(self.iteration),
            "max_iterations": int(self.max_iterations),
            "stagnation_delta": (float(self.stagnation_delta) if isinstance(self.stagnation_delta, (int, float)) else None),
            "fatal_tags_triggered": list(self.fatal_tags_triggered),
            "hard_fail_tags": list(self.hard_fail_tags),
        }


class JudgeStopRulesService:
    """
    Deterministic stop policy for judge loops.

    Env overrides:
    - JUDGE_STOP_MAX_ITERATIONS
    - JUDGE_STOP_STAGNATION_DELTA_THRESHOLD
    - JUDGE_STOP_FATAL_PRECEDENCE
    - JUDGE_STOP_FATAL_TAGS (comma-separated)
    """

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        stagnation_stop: StagnationStopService | None = None,
    ) -> None:
        self._env_override = env
        self._rules = self._load_rules()
        self._stagnation_stop = stagnation_stop or get_stagnation_stop_service(env=env)

    @property
    def rules(self) -> JudgeStopRules:
        return self._rules

    def reload(self) -> JudgeStopRules:
        self._rules = self._load_rules()
        return self._rules

    def evaluate(
        self,
        *,
        iteration: int,
        hard_fail_tags: Iterable[Any],
        previous_hard_fail_tags: Iterable[Any] | None = None,
        score: float | None = None,
        previous_score: float | None = None,
    ) -> JudgeStopDecision:
        tags = _normalize_tags(hard_fail_tags)
        previous_tags = _normalize_tags(previous_hard_fail_tags or [])
        fatal_hits = [tag for tag in tags if tag in self._rules.fatal_tags]
        stagnation = self._stagnation_stop.evaluate(
            tag_history=[previous_tags, tags],
            score_history=[previous_score, score],
            repeat_window=2,
            delta_threshold=float(self._rules.stagnation_delta_threshold),
            require_score_delta=True,
        )
        stagnation_delta = stagnation.delta
        stagnated = bool(stagnation.stop)

        if self._rules.fatal_precedence and fatal_hits:
            return JudgeStopDecision(
                stop=True,
                reason="fatal_precedence",
                iteration=int(iteration),
                max_iterations=int(self._rules.max_iterations),
                stagnation_delta=stagnation_delta,
                fatal_tags_triggered=fatal_hits,
                hard_fail_tags=tags,
            )

        if int(iteration) >= int(self._rules.max_iterations):
            return JudgeStopDecision(
                stop=True,
                reason="max_iterations",
                iteration=int(iteration),
                max_iterations=int(self._rules.max_iterations),
                stagnation_delta=stagnation_delta,
                fatal_tags_triggered=fatal_hits,
                hard_fail_tags=tags,
            )

        if not tags:
            return JudgeStopDecision(
                stop=True,
                reason="hard_pass",
                iteration=int(iteration),
                max_iterations=int(self._rules.max_iterations),
                stagnation_delta=stagnation_delta,
                fatal_tags_triggered=fatal_hits,
                hard_fail_tags=tags,
            )

        if stagnated:
            return JudgeStopDecision(
                stop=True,
                reason="stagnation",
                iteration=int(iteration),
                max_iterations=int(self._rules.max_iterations),
                stagnation_delta=stagnation_delta,
                fatal_tags_triggered=fatal_hits,
                hard_fail_tags=tags,
            )

        return JudgeStopDecision(
            stop=False,
            reason="continue",
            iteration=int(iteration),
            max_iterations=int(self._rules.max_iterations),
            stagnation_delta=stagnation_delta,
            fatal_tags_triggered=fatal_hits,
            hard_fail_tags=tags,
        )

    def _load_rules(self) -> JudgeStopRules:
        env = self._env()
        max_iterations = _parse_positive_int(env.get("JUDGE_STOP_MAX_ITERATIONS"), default=3)
        delta_threshold = _parse_non_negative_float(env.get("JUDGE_STOP_STAGNATION_DELTA_THRESHOLD"), default=0.005)
        fatal_precedence = _parse_bool(env.get("JUDGE_STOP_FATAL_PRECEDENCE"), default=True)
        fatal_tags = _parse_csv(env.get("JUDGE_STOP_FATAL_TAGS"))
        if not fatal_tags:
            fatal_tags = list(DEFAULT_FATAL_TAGS)
        return JudgeStopRules(
            schema_version=1,
            version="v1",
            max_iterations=max_iterations,
            stagnation_delta_threshold=delta_threshold,
            fatal_precedence=fatal_precedence,
            fatal_tags=tuple(sorted(set(fatal_tags))),
        )

    def _env(self) -> Mapping[str, str]:
        if self._env_override is not None:
            return self._env_override
        return os.environ


_SERVICE: JudgeStopRulesService | None = None


def get_judge_stop_rules_service(
    *,
    env: Mapping[str, str] | None = None,
    reload: bool = False,
) -> JudgeStopRulesService:
    global _SERVICE
    if _SERVICE is None or env is not None:
        _SERVICE = JudgeStopRulesService(env=env)
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


def _parse_bool(value: Any, *, default: bool) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_csv(value: Any) -> List[str]:
    if value is None:
        return []
    out: List[str] = []
    for part in str(value).split(","):
        token = part.strip()
        if token:
            out.append(token)
    return out
