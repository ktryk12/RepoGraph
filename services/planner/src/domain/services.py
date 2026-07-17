from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from babyai_shared.policy.governance_smoke import (
    GOVERNANCE_HELLO_WORLD_TEMPLATE_ID,
    governance_task_spec,
)
from .models import IntentRecord, ReadyRecord


def build_policy_contract(policy_preset: str) -> dict[str, Any]:
    preset = _normalize_policy(policy_preset)
    if preset == "public":
        return {
            "policy_id": "public",
            "allow_enqueue": True,
            "constraints": {"visibility": "public", "safety_mode": "strict"},
        }
    if preset == "restricted":
        return {
            "policy_id": "restricted",
            "allow_enqueue": True,
            "constraints": {"visibility": "restricted", "approval_required": True},
        }
    return {
        "policy_id": "dev",
        "allow_enqueue": True,
        "constraints": {"visibility": "internal", "safety_mode": "relaxed"},
    }


def build_task_spec(*, intent: IntentRecord, ready: ReadyRecord) -> dict[str, Any]:
    if str(intent.template_id) == GOVERNANCE_HELLO_WORLD_TEMPLATE_ID:
        task_id = f"governance-{stable_task_hash(intent=intent, ready=ready)[:12]}"
        return _canonicalize(
            governance_task_spec(
                task_id=task_id,
                context_id=str(intent.context_id),
                user_prompt=str(intent.user_prompt),
                truth_pack_alias=str(ready.truth_pack_alias),
                truth_override_ref=str(ready.user_override_ref),
                override_hash=str(ready.override_hash),
                policy_preset=str(intent.policy_preset),
            )
        )

    payload = {
        "schema_version": 1,
        "template": "auto",
        "task_id": f"auto-{stable_task_hash(intent=intent, ready=ready)[:12]}",
        "title": "Auto-generated task",
        "prompt": str(intent.user_prompt),
        "context_id": str(intent.context_id),
        "inputs": {
            "truth_pack_alias": str(ready.truth_pack_alias),
            "truth_override_ref": str(ready.user_override_ref),
            "override_hash": str(ready.override_hash),
            "policy_preset": str(intent.policy_preset),
        },
        "acceptance": [
            "Run completes with deterministic truth override",
            "Decision lifecycle reaches terminal status",
        ],
    }
    return _canonicalize(payload)


def stable_task_hash(*, intent: IntentRecord, ready: ReadyRecord) -> str:
    seed = {
        "context_id": str(intent.context_id),
        "policy_preset": str(intent.policy_preset),
        "user_prompt": str(intent.user_prompt),
        "template_id": str(intent.template_id or "auto"),
        "truth_pack_alias": str(ready.truth_pack_alias),
        "user_override_ref": str(ready.user_override_ref),
        "override_hash": str(ready.override_hash),
    }
    raw = json.dumps(_canonicalize(seed), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def build_decision_requested(
    *,
    intent: IntentRecord,
    ready: ReadyRecord,
    task_ref: str,
) -> dict[str, Any]:
    return {
        "decision_id": str(intent.decision_id),
        "context_id": str(intent.context_id),
        "task_ref": str(task_ref),
        "truth_pack_ref": str(ready.truth_pack_alias),
        "truth_pack_version": 1,
        "policy_contract": _policy_contract_for_intent(intent=intent),
        "metadata": {
            "truth_override_ref": str(ready.user_override_ref),
            "override_hash": str(ready.override_hash),
            "policy_preset": str(intent.policy_preset),
            "task_template_id": str(intent.template_id or "auto"),
        },
    }


def parse_intent(payload: Mapping[str, Any]) -> IntentRecord:
    decision_id = _required_text(payload, "decision_id")
    user_prompt = _required_text(payload, "user_prompt")
    context_id = _required_text(payload, "context_id") or "dev"
    policy_preset = _required_text(payload, "policy_preset") or "dev"
    template_id = _required_text(payload, "template_id") or "auto"
    if not decision_id:
        raise ValueError("decision_id is required")
    if not user_prompt:
        raise ValueError("user_prompt is required")
    return IntentRecord(
        decision_id=decision_id,
        context_id=context_id,
        policy_preset=_normalize_policy(policy_preset),
        user_prompt=user_prompt,
        template_id=_normalize_template_id(template_id),
    )


def parse_ready(payload: Mapping[str, Any]) -> ReadyRecord:
    decision_id = _required_text(payload, "decision_id")
    context_id = _required_text(payload, "context_id") or "dev"
    policy_preset = _required_text(payload, "policy_preset") or "dev"
    truth_pack_alias = _required_text(payload, "truth_pack_alias") or "layered_default"
    user_override_ref = _required_text(payload, "user_override_ref")
    override_hash = _required_text(payload, "override_hash")
    explanation_text = _required_text(payload, "explanation_text")
    if not decision_id:
        raise ValueError("decision_id is required")
    if not user_override_ref:
        raise ValueError("user_override_ref is required")
    if not override_hash:
        raise ValueError("override_hash is required")
    return ReadyRecord(
        decision_id=decision_id,
        context_id=context_id,
        policy_preset=_normalize_policy(policy_preset),
        truth_pack_alias=truth_pack_alias,
        user_override_ref=user_override_ref,
        explanation_text=explanation_text,
        override_hash=override_hash,
    )


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return str(value).strip() if isinstance(value, str) else ""


def _normalize_policy(value: str) -> str:
    preset = str(value or "").strip().lower() or "dev"
    if preset not in {"public", "dev", "restricted"}:
        raise ValueError(f"unsupported policy_preset: {value}")
    return preset


def _policy_contract_for_intent(*, intent: IntentRecord) -> dict[str, Any]:
    contract = build_policy_contract(intent.policy_preset)
    if str(intent.template_id) == GOVERNANCE_HELLO_WORLD_TEMPLATE_ID:
        constraints = contract.get("constraints")
        merged = dict(constraints) if isinstance(constraints, dict) else {}
        merged["approval_required"] = True
        contract["constraints"] = merged
    return contract


def _normalize_template_id(value: str) -> str:
    text = str(value or "").strip() or "auto"
    if text in {"auto", GOVERNANCE_HELLO_WORLD_TEMPLATE_ID}:
        return text
    return "auto"


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    return value
