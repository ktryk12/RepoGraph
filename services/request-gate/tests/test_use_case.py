from __future__ import annotations

from pathlib import Path

from application.ports import PolicyValidatorResult
from application.use_cases import (
    ValidateAndEnqueueDecisionRequest,
    ValidateAndEnqueueFailure,
    ValidateAndEnqueueSuccess,
)
from domain.models import CanonicalLifecycleRequestedEvent, DecisionRequest, PolicyContract


class _FakeDedupeStore:
    def __init__(self, *, claim_result: bool) -> None:
        self.claim_result = bool(claim_result)
        self.claim_calls: list[tuple[str, int]] = []

    def claim(self, *, key: str, ttl_seconds: int) -> bool:
        self.claim_calls.append((str(key), int(ttl_seconds)))
        return self.claim_result


class _FakeLifecyclePublisher:
    def __init__(self) -> None:
        self.events: list[CanonicalLifecycleRequestedEvent] = []

    def publish(self, event: CanonicalLifecycleRequestedEvent) -> None:
        self.events.append(event)


class _FakeDlqPublisher:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def publish(self, *, reason_code: str, message: str, payload: dict) -> None:
        self.messages.append(
            {
                "reason_code": str(reason_code),
                "message": str(message),
                "payload": dict(payload),
            }
        )


class _DenyPolicyValidator:
    def validate_request(self, request: DecisionRequest) -> PolicyValidatorResult:
        _ = request
        return PolicyValidatorResult(
            allowed=False,
            reason_code="POLICY_REMOTE_DENIED",
            message="external deny",
            metadata={"source": "fake"},
        )


def _request() -> DecisionRequest:
    return DecisionRequest(
        decision_id="dec-uc-1",
        context_id="ctx-uc-1",
        task_ref="artifact:task-uc-1",
        truth_pack_ref="v1",
        truth_pack_version=1,
        policy_contract=PolicyContract(
            policy_id="allow-default",
            allow_enqueue=True,
            constraints={},
        ),
        metadata={"trace_id": "trace-uc-1"},
    )


def test_use_case_success_enqueues_lifecycle_event() -> None:
    dedupe = _FakeDedupeStore(claim_result=True)
    lifecycle = _FakeLifecyclePublisher()
    dlq = _FakeDlqPublisher()
    use_case = ValidateAndEnqueueDecisionRequest(
        dedupe_store=dedupe,
        lifecycle_publisher=lifecycle,
        dlq_publisher=dlq,
    )

    result = use_case.execute(_request())
    assert isinstance(result, ValidateAndEnqueueSuccess)
    assert result.code == "REQUEST_ENQUEUED"
    assert len(lifecycle.events) == 1
    event_metadata = lifecycle.events[0].metadata
    assert isinstance(event_metadata.get("effective_policy"), dict)
    assert str(event_metadata["effective_policy"]["write_scope"]["type"]).strip()
    assert str(event_metadata.get("policy_fingerprint", "")).strip()
    assert isinstance(event_metadata.get("policy_explanation"), dict)
    assert "approval_required" in event_metadata
    assert not dlq.messages


def test_use_case_duplicate_request_goes_to_dlq() -> None:
    dedupe = _FakeDedupeStore(claim_result=False)
    lifecycle = _FakeLifecyclePublisher()
    dlq = _FakeDlqPublisher()
    use_case = ValidateAndEnqueueDecisionRequest(
        dedupe_store=dedupe,
        lifecycle_publisher=lifecycle,
        dlq_publisher=dlq,
    )

    result = use_case.execute(_request())
    assert isinstance(result, ValidateAndEnqueueFailure)
    assert result.code == "DUPLICATE_REQUEST"
    assert len(dlq.messages) == 1
    assert not lifecycle.events


def test_use_case_policy_validator_denial_returns_failure() -> None:
    dedupe = _FakeDedupeStore(claim_result=True)
    lifecycle = _FakeLifecyclePublisher()
    dlq = _FakeDlqPublisher()
    use_case = ValidateAndEnqueueDecisionRequest(
        dedupe_store=dedupe,
        lifecycle_publisher=lifecycle,
        dlq_publisher=dlq,
        policy_validator=_DenyPolicyValidator(),
    )

    result = use_case.execute(_request())
    assert isinstance(result, ValidateAndEnqueueFailure)
    assert result.code == "POLICY_REMOTE_DENIED"
    assert len(dlq.messages) == 1
    assert not lifecycle.events


def test_domain_and_application_layers_do_not_import_confluent_kafka() -> None:
    for layer in ("domain", "application"):
        layer_path = Path("services/request-gate/src") / layer
        for py_file in layer_path.rglob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            assert "confluent_kafka" not in content, f"Kafka import leak in {py_file}"
