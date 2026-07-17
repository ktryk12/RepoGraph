from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Iterable, Mapping


def compute_policy_fingerprint(effective_policy: Mapping[str, Any]) -> str:
    payload = _canonicalize(effective_policy)
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def approval_required(
    *,
    effective_policy: Mapping[str, Any] | None,
    policy_constraints: Mapping[str, Any] | None = None,
    policy_preset: str | None = None,
    required_policy_ids: Iterable[str] | None = None,
    required_safety_profiles: Iterable[str] | None = None,
) -> bool:
    policy_obj = dict(effective_policy or {})
    constraints = {}
    if isinstance(policy_obj.get("constraints"), Mapping):
        constraints = dict(policy_obj.get("constraints") or {})
    if isinstance(policy_constraints, Mapping):
        constraints.update(dict(policy_constraints))

    write_scope = policy_obj.get("write_scope")
    write_scope_type = ""
    if isinstance(write_scope, Mapping):
        write_scope_type = str(write_scope.get("type") or "").strip().lower()
    if write_scope_type and write_scope_type not in {"none", "readonly", "read_only", "no_write"}:
        return True

    if bool(constraints.get("approval_required")):
        return True

    policy_id = str(policy_obj.get("policy_id") or policy_preset or "").strip().lower()
    configured_policy_ids = {
        str(item).strip().lower()
        for item in (required_policy_ids or ("restricted",))
        if str(item).strip()
    }
    if policy_id and policy_id in configured_policy_ids:
        return True

    safety_profile = str(policy_obj.get("safety_profile") or "").strip().lower()
    configured_profiles = {
        str(item).strip().lower()
        for item in (required_safety_profiles or ())
        if str(item).strip()
    }
    if safety_profile and safety_profile in configured_profiles:
        return True

    return False


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    return value

