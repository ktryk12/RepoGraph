from __future__ import annotations

from domain.policy_explainer import build_policy_explanation


def test_policy_explainer_returns_deterministic_structured_payload() -> None:
    effective_policy = {
        "policy_id": "restricted",
        "safety_profile": "strict",
        "write_scope": {"type": "policy_service"},
    }
    first = build_policy_explanation(
        effective_policy=effective_policy,
        allowed=True,
        reason_code="OK",
        policy_preset="restricted",
        approval_required=True,
    )
    second = build_policy_explanation(
        effective_policy=effective_policy,
        allowed=True,
        reason_code="OK",
        policy_preset="restricted",
        approval_required=True,
    )
    assert first == second
    assert first["policy_id"] == "restricted"
    assert first["write_scope"] == {"type": "policy_service"}
    assert first["what_it_means"]["execution"] == "paused_for_approval"

