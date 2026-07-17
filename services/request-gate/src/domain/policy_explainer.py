from __future__ import annotations

from typing import Any, Mapping


def build_policy_explanation(
    *,
    effective_policy: Mapping[str, Any],
    allowed: bool,
    reason_code: str | None,
    policy_preset: str | None,
    approval_required: bool,
) -> dict[str, Any]:
    policy_id = str(effective_policy.get("policy_id") or "").strip()
    preset = str(policy_preset or policy_id or "").strip()
    safety_profile = str(effective_policy.get("safety_profile") or "").strip()
    write_scope_obj = effective_policy.get("write_scope")
    write_scope = dict(write_scope_obj) if isinstance(write_scope_obj, Mapping) else {"type": ""}
    write_scope_type = str(write_scope.get("type") or "").strip().lower()

    why: list[str] = []
    if allowed:
        why.append("Policy validator allowed this request.")
    else:
        why.append("Policy validator denied this request.")
    if write_scope_type:
        why.append(f"Effective write scope is '{write_scope_type}'.")
    if approval_required:
        why.append("This request requires explicit approval before execution.")

    what_it_means = {
        "execution": "paused_for_approval" if approval_required else "can_start_immediately",
        "writes": "blocked_until_approval" if approval_required else "allowed_under_effective_policy",
        "tools": "blocked_until_approval" if approval_required else "allowed_under_effective_policy",
    }

    if_you_change = [
        {
            "field": "write_scope",
            "change": "NONE->REPO",
            "effect": "Expands write permissions to repository paths.",
            "risk": "Higher blast radius for unintended writes.",
            "requires": "approval",
        },
        {
            "field": "safety_profile",
            "change": "strict->balanced",
            "effect": "Allows broader execution latitude.",
            "risk": "Reduced guard strictness.",
            "requires": "approval",
        },
    ]

    return {
        "policy_id": policy_id,
        "preset": preset,
        "safety_profile": safety_profile,
        "write_scope": write_scope,
        "allowed": bool(allowed),
        "reason_code": str(reason_code or ""),
        "why": why,
        "what_it_means": what_it_means,
        "if_you_change": if_you_change,
    }

