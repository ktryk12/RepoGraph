from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping

from .errors import DomainError
from .models import CanonicalLifecycleRequestedEvent, DecisionRequest, PolicyContract


def validate_policy_contract(policy: PolicyContract) -> DomainError | None:
    policy_id = str(policy.policy_id or "").strip()
    if not policy_id:
        return DomainError(
            code="POLICY_ID_REQUIRED",
            message="policy_contract.policy_id is required",
            field="policy_contract.policy_id",
        )
    if not bool(policy.allow_enqueue):
        return DomainError(
            code="POLICY_ENQUEUE_DENIED",
            message="policy_contract.allow_enqueue must be true",
            field="policy_contract.allow_enqueue",
        )
    if not isinstance(policy.constraints, dict):
        return DomainError(
            code="POLICY_CONSTRAINTS_INVALID",
            message="policy_contract.constraints must be an object",
            field="policy_contract.constraints",
        )
    return None


def canonicalize_request(request: DecisionRequest) -> dict[str, Any]:
    return _canonicalize_value(
        {
            "decision_id": str(request.decision_id),
            "context_id": str(request.context_id),
            "task_ref": str(request.task_ref),
            "truth_pack_ref": str(request.truth_pack_ref),
            "truth_pack_version": int(request.truth_pack_version),
            "policy_contract": {
                "policy_id": str(request.policy_contract.policy_id),
                "allow_enqueue": bool(request.policy_contract.allow_enqueue),
                "constraints": dict(request.policy_contract.constraints or {}),
            },
            "metadata": dict(request.metadata or {}),
        }
    )


def compute_request_fingerprint(request: DecisionRequest) -> str:
    canonical = canonicalize_request(request)
    return sha256(_canonical_json_bytes(canonical)).hexdigest()


def build_lifecycle_requested_event(
    request: DecisionRequest,
    *,
    timestamp: str | None = None,
) -> CanonicalLifecycleRequestedEvent:
    ts = str(timestamp or "").strip() or _now_iso()
    request_fingerprint = compute_request_fingerprint(request)
    metadata = dict(request.metadata or {})
    metadata["request_fingerprint"] = request_fingerprint
    event = CanonicalLifecycleRequestedEvent(
        schema_version=1,
        decision_id=str(request.decision_id),
        context_id=str(request.context_id),
        status="requested",
        timestamp=ts,
        task_ref=str(request.task_ref),
        truth_pack_ref=str(request.truth_pack_ref),
        truth_pack_version=int(request.truth_pack_version),
        metadata=metadata,
    )
    event_fingerprint = compute_lifecycle_event_fingerprint(event)
    metadata_with_fp = dict(metadata)
    metadata_with_fp["event_fingerprint"] = event_fingerprint
    return replace(event, metadata=metadata_with_fp)


def compute_lifecycle_event_fingerprint(event: CanonicalLifecycleRequestedEvent) -> str:
    payload = canonicalize_lifecycle_requested_event(event)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        metadata_no_event_fp = dict(metadata)
        metadata_no_event_fp.pop("event_fingerprint", None)
        payload["metadata"] = metadata_no_event_fp
    return sha256(_canonical_json_bytes(payload)).hexdigest()


def canonicalize_lifecycle_requested_event(
    event: CanonicalLifecycleRequestedEvent,
) -> dict[str, Any]:
    return _canonicalize_value(asdict(event))


def _canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _canonicalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _canonicalize_value(value[k]) for k in sorted(value.keys(), key=str)}
    if isinstance(value, list):
        return [_canonicalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize_value(item) for item in value]
    return value


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
