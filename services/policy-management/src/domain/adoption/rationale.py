# policy/rationale.py
from __future__ import annotations

from typing import Any, Dict, List

from policy.scorer import _get


def validate_rationale_has_strong_evidence(spec: Dict[str, Any], decision: Dict[str, Any]) -> List[str]:
    """
    Enforcer:
      - at least 2 rationale entries with evidence_path that resolves
      - and weight >= 0.7
    """
    summary = rationale_evidence_summary(spec, decision)
    return list(summary.get("errors") or [])


def rationale_evidence_summary(
    spec: Dict[str, Any],
    decision: Dict[str, Any],
    *,
    min_weight: float = 0.7,
    required_strong: int = 2,
) -> Dict[str, Any]:
    errors: List[str] = []
    rationale = decision.get("rationale", []) or []
    strong = 0

    for r in rationale:
        weight = float(r.get("weight", 0.0))
        ep = r.get("evidence_path")
        if ep and isinstance(ep, str):
            val = _get(spec, ep, default=None)
            if val is not None and weight >= float(min_weight):
                strong += 1

    if strong < int(required_strong):
        errors.append(
            f"Need >={int(required_strong)} strong rationale items (weight>={float(min_weight):.1f} + resolvable evidence_path). Got {strong}."
        )

    return {
        "strong_count": int(strong),
        "required_count": int(required_strong),
        "min_weight": float(min_weight),
        "errors": errors,
    }
