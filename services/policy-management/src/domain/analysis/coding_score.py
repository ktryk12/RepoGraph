from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


DEFAULT_WEIGHTS = {
    "tests": 0.70,
    "lint": 0.15,
    "patch_size": 0.10,
    "repairs": 0.05,
}

DEFAULT_LIMITS = {
    "max_patch_lines": 300,
    "max_repairs": 4,
}


@dataclass(frozen=True)
class CodingScoreResult:
    total: float
    components: Dict[str, float]
    penalties: List[str]
    explanations: List[Dict[str, Any]]
    hard_fail: bool
    hard_fail_reasons: List[str]


def score_coding(
    outcome: Dict[str, Any],
    *,
    weights: Dict[str, float] | None = None,
    limits: Dict[str, int] | None = None,
) -> CodingScoreResult:
    """
    Deterministic coding score with hard gates.

    Hard gates:
      - scope_violation -> FAIL
      - patch_apply_failed -> FAIL
      - tests_passed is False -> FAIL
    """
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    limits = {**DEFAULT_LIMITS, **(limits or {})}

    penalties: List[str] = []
    hard_fail_reasons: List[str] = []

    scope_violation = bool(outcome.get("scope_violation"))
    patch_apply_failed = bool(outcome.get("patch_apply_failed"))
    tests_passed = bool(outcome.get("tests_passed"))
    lint_passed = bool(outcome.get("lint_passed", False))

    if scope_violation:
        penalties.append("hard_gate:scope_violation")
        hard_fail_reasons.append("scope_violation")
    if patch_apply_failed:
        penalties.append("hard_gate:patch_apply_failed")
        hard_fail_reasons.append("patch_apply_failed")
    if not tests_passed:
        penalties.append("hard_gate:tests_failed")
        hard_fail_reasons.append("tests_failed")

    # Component scores
    tests_score = 1.0 if tests_passed else 0.0
    lint_score = 1.0 if lint_passed else 0.0

    patch_lines = _as_int(outcome.get("patch_size_lines"))
    patch_score = _linear_score(
        patch_lines,
        max_value=int(limits["max_patch_lines"]),
        default_if_missing=1.0,
    )
    if patch_lines is not None:
        penalties.append(f"patch_size_penalty:lines={patch_lines}")

    repairs_used = _as_int(outcome.get("repairs_used"))
    repairs_score = _linear_score(
        repairs_used,
        max_value=int(limits["max_repairs"]),
        default_if_missing=1.0,
    )
    if repairs_used is not None:
        penalties.append(f"repairs_penalty:steps={repairs_used}")

    components = {
        "tests": tests_score,
        "lint": lint_score,
        "patch_size": patch_score,
        "repairs": repairs_score,
    }

    total = (
        components["tests"] * float(weights["tests"])
        + components["lint"] * float(weights["lint"])
        + components["patch_size"] * float(weights["patch_size"])
        + components["repairs"] * float(weights["repairs"])
    )

    hard_fail = bool(hard_fail_reasons)
    if hard_fail:
        total = 0.0

    explanations = explain_coding_penalties(penalties)

    return CodingScoreResult(
        total=round(float(total), 6),
        components=components,
        penalties=penalties,
        explanations=explanations,
        hard_fail=hard_fail,
        hard_fail_reasons=hard_fail_reasons,
    )


def explain_coding_penalties(penalties: List[str]) -> List[Dict[str, Any]]:
    explanations: List[Dict[str, Any]] = []
    for p in penalties:
        entry = _explain_penalty(str(p))
        if entry:
            explanations.append(entry)
    return explanations


def _explain_penalty(penalty: str) -> Dict[str, Any]:
    if penalty.startswith("hard_gate:scope_violation"):
        return _make(
            penalty,
            "scope",
            "Changes went outside the allowed scope.",
            "Restrict edits to paths in the task scope.",
        )
    if penalty.startswith("hard_gate:patch_apply_failed"):
        return _make(
            penalty,
            "apply",
            "Patch could not be applied cleanly.",
            "Rebase or regenerate the patch so it applies without conflicts.",
        )
    if penalty.startswith("hard_gate:tests_failed"):
        return _make(
            penalty,
            "tests",
            "Tests did not pass, which is a hard failure for coding tasks.",
            "Fix failing tests or update the changes to satisfy them.",
        )
    if penalty.startswith("patch_size_penalty:"):
        return _make(
            penalty,
            "patch_size",
            "Large patches increase risk and reduce reviewability.",
            "Prefer smaller, focused changes or split into multiple steps.",
        )
    if penalty.startswith("repairs_penalty:"):
        return _make(
            penalty,
            "repairs",
            "More repair iterations indicate instability or overshooting.",
            "Aim for a minimal change that passes tests in fewer repairs.",
        )
    return _make(
        penalty,
        "other",
        "Penalty applied by coding policy.",
        "Review coding policy and task constraints.",
    )


def _make(penalty: str, category: str, why: str, fix: str) -> Dict[str, Any]:
    return {
        "penalty": penalty,
        "category": category,
        "why_it_matters": why,
        "how_to_fix": fix,
    }


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _linear_score(value: int | None, *, max_value: int, default_if_missing: float) -> float:
    if value is None:
        return float(default_if_missing)
    if max_value <= 0:
        return 1.0
    ratio = min(1.0, max(0.0, float(value) / float(max_value)))
    return max(0.0, 1.0 - ratio)
