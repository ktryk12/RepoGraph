from __future__ import annotations

from domain import (
    DecisionRequest,
    PolicyContract,
    build_lifecycle_requested_event,
    canonicalize_request,
    compute_lifecycle_event_fingerprint,
    compute_request_fingerprint,
    validate_policy_contract,
)


def _request_with_metadata(metadata: dict) -> DecisionRequest:
    return DecisionRequest(
        decision_id="dec-1",
        context_id="ctx-1",
        task_ref="artifact:task-1",
        truth_pack_ref="v1",
        truth_pack_version=1,
        policy_contract=PolicyContract(
            policy_id="allow-default",
            allow_enqueue=True,
            constraints={"region": "eu"},
        ),
        metadata=metadata,
    )


def test_validate_policy_contract_rejects_missing_id() -> None:
    error = validate_policy_contract(
        PolicyContract(
            policy_id="",
            allow_enqueue=True,
            constraints={},
        )
    )
    assert error is not None
    assert error.code == "POLICY_ID_REQUIRED"


def test_validate_policy_contract_rejects_denied_enqueue() -> None:
    error = validate_policy_contract(
        PolicyContract(
            policy_id="allow-default",
            allow_enqueue=False,
            constraints={},
        )
    )
    assert error is not None
    assert error.code == "POLICY_ENQUEUE_DENIED"


def test_canonicalize_request_is_stable_for_key_order() -> None:
    request_a = _request_with_metadata({"z": 1, "a": {"c": 3, "b": 2}})
    request_b = _request_with_metadata({"a": {"b": 2, "c": 3}, "z": 1})

    assert canonicalize_request(request_a) == canonicalize_request(request_b)
    assert compute_request_fingerprint(request_a) == compute_request_fingerprint(request_b)


def test_build_lifecycle_requested_event_has_fingerprints() -> None:
    request = _request_with_metadata({"trace_id": "trace-1"})
    event = build_lifecycle_requested_event(request, timestamp="2026-01-01T00:00:00Z")
    metadata = event.metadata
    assert "request_fingerprint" in metadata
    assert "event_fingerprint" in metadata
    assert metadata["event_fingerprint"] == compute_lifecycle_event_fingerprint(event)
