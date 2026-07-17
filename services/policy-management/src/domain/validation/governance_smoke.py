from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping


GOVERNANCE_HELLO_WORLD_TEMPLATE_ID = "governance_hello_world.v1"
GOVERNANCE_EXPECTED_PAYLOAD: dict[str, str] = {"hello": "world"}


def is_governance_hello_world_task(task: Mapping[str, Any]) -> bool:
    template_id = str(task.get("template") or task.get("template_id") or task.get("task_template_id") or "").strip()
    return template_id == GOVERNANCE_HELLO_WORLD_TEMPLATE_ID


def expected_payload() -> dict[str, str]:
    return dict(GOVERNANCE_EXPECTED_PAYLOAD)


def extract_model_json_payload(decision: Mapping[str, Any]) -> dict[str, Any] | None:
    candidates: list[Any] = []
    candidates.append(decision.get("generated_output"))
    candidates.append(decision.get("output"))
    for key in ("generated_output_json", "output_json", "response_json"):
        candidates.append(decision.get(key))

    for raw in candidates:
        parsed = _normalize_json_candidate(raw)
        if parsed is not None:
            return parsed
    return None


def build_governance_artifact_payload(
    *,
    decision_id: str,
    context_id: str,
    json_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_payload = _normalize_payload_map(json_payload) if isinstance(json_payload, Mapping) else expected_payload()
    canonical = json.dumps(normalized_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return {
        "decision_id": str(decision_id),
        "context_id": str(context_id),
        "artifact_name": "governance_smoke.v1",
        "content_hash": sha256(canonical.encode("utf-8")).hexdigest(),
        "payload": normalized_payload,
    }


def evaluate_governance_hello_world_artifact(artifact_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(artifact_payload, Mapping):
        return {
            "passed": False,
            "score": 0.0,
            "components": {
                "functional": 0.0,
                "security": 0.0,
                "architecture_fit": 0.0,
            },
            "failure_reasons": ["governance_artifact_missing"],
        }

    payload = artifact_payload.get("payload")
    if not isinstance(payload, Mapping):
        return {
            "passed": False,
            "score": 0.0,
            "components": {
                "functional": 0.0,
                "security": 0.0,
                "architecture_fit": 0.0,
            },
            "failure_reasons": ["governance_payload_missing"],
        }

    normalized = _normalize_payload_map(payload)
    if normalized == expected_payload():
        return {
            "passed": True,
            "score": 1.0,
            "components": {
                "functional": 1.0,
                "security": 1.0,
                "architecture_fit": 1.0,
            },
            "failure_reasons": [],
        }

    return {
        "passed": False,
        "score": 0.0,
        "components": {
            "functional": 0.0,
            "security": 0.0,
            "architecture_fit": 0.0,
        },
        "failure_reasons": ["governance_payload_mismatch"],
    }


def governance_task_spec(
    *,
    task_id: str,
    context_id: str,
    user_prompt: str,
    truth_pack_alias: str,
    truth_override_ref: str,
    override_hash: str,
    policy_preset: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "template": GOVERNANCE_HELLO_WORLD_TEMPLATE_ID,
        "task_id": str(task_id),
        "title": "Governance Hello World",
        "prompt": 'Return ONLY valid JSON: {"hello":"world"}.',
        "context_id": str(context_id),
        "inputs": {
            "truth_pack_alias": str(truth_pack_alias),
            "truth_override_ref": str(truth_override_ref),
            "override_hash": str(override_hash),
            "policy_preset": str(policy_preset),
            "user_prompt": str(user_prompt),
        },
        "acceptance": [
            "Decision lifecycle reaches terminal status",
            "eval.results exists for the decision",
            'Artifact governance_smoke.v1 exists and payload equals {"hello":"world"}',
        ],
        "constraints": {
            "repo_writes": "forbidden",
            "external_network": "forbidden",
            "allowed_runtime": ["internal_model_runner", "artifact_writer"],
        },
    }


def _normalize_payload_map(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): payload[key] for key in sorted(payload.keys(), key=str)}


def _normalize_json_candidate(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, Mapping):
        return _normalize_payload_map(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            decoded = json.loads(text)
        except Exception:
            return None
        if isinstance(decoded, Mapping):
            return _normalize_payload_map(decoded)
        return None
    return None
