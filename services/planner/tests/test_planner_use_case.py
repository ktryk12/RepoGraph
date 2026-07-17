from __future__ import annotations

from typing import Any, Mapping

from planner.application.use_cases import PlannerService


class _FakeTaskStore:
    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    def store(self, *, task_spec: Mapping[str, Any]) -> str:
        self.saved.append(dict(task_spec))
        return "artifact:sha256:task-1"


class _FakeRequestedPublisher:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def publish(self, payload: Mapping[str, Any]) -> None:
        self.payloads.append(dict(payload))


class _FakeDlqPublisher:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def publish_dlq(self, *, reason_code: str, message: str, payload: Mapping[str, Any]) -> None:
        self.payloads.append({"reason_code": reason_code, "message": message, "payload": dict(payload)})


def test_planner_emits_decision_requested_from_intent_and_ready() -> None:
    task_store = _FakeTaskStore()
    requested = _FakeRequestedPublisher()
    dlq = _FakeDlqPublisher()
    service = PlannerService(
        task_store=task_store,
        decision_requested_publisher=requested,
        dlq_publisher=dlq,
    )

    service.handle_intent(
        {
            "decision_id": "dec-1",
            "context_id": "ctx-1",
            "policy_preset": "restricted",
            "user_prompt": "What is the capital of France?",
        }
    )
    assert task_store.saved == []
    assert requested.payloads == []
    service.handle_ready(
        {
            "decision_id": "dec-1",
            "context_id": "ctx-1",
            "policy_preset": "restricted",
            "truth_pack_alias": "layered_default",
            "user_override_ref": "artifacts/truth_overrides/abc.yaml",
            "override_hash": "abc",
            "explanation_text": "ready",
        }
    )

    assert len(task_store.saved) == 1
    task = task_store.saved[0]
    assert task["template"] == "auto"
    assert task["prompt"] == "What is the capital of France?"
    assert task["prompt"] != 'Return ONLY valid JSON: {"hello":"world"}.'
    assert len(requested.payloads) == 1
    emitted = requested.payloads[0]
    assert emitted["task_ref"] == "artifact:sha256:task-1"
    assert emitted["truth_pack_ref"] == "layered_default"
    assert emitted["metadata"]["truth_override_ref"] == "artifacts/truth_overrides/abc.yaml"
    assert emitted["metadata"]["task_template_id"] == "auto"
    assert emitted["policy_contract"]["policy_id"] == "restricted"
    assert bool(emitted["policy_contract"]["constraints"]["approval_required"]) is True
    assert dlq.payloads == []


def test_planner_governance_template_sets_task_and_requires_approval() -> None:
    task_store = _FakeTaskStore()
    requested = _FakeRequestedPublisher()
    dlq = _FakeDlqPublisher()
    service = PlannerService(
        task_store=task_store,
        decision_requested_publisher=requested,
        dlq_publisher=dlq,
    )
    service.handle_intent(
        {
            "decision_id": "dec-gov-1",
            "context_id": "dev",
            "policy_preset": "dev",
            "user_prompt": "governance smoke",
            "template_id": "governance_hello_world.v1",
        }
    )
    service.handle_ready(
        {
            "decision_id": "dec-gov-1",
            "context_id": "dev",
            "policy_preset": "dev",
            "truth_pack_alias": "layered_default",
            "user_override_ref": "artifacts/truth_overrides/gov.yaml",
            "override_hash": "govhash",
            "explanation_text": "ready",
        }
    )
    assert len(task_store.saved) == 1
    task = task_store.saved[0]
    assert task["template"] == "governance_hello_world.v1"
    assert task["prompt"] == 'Return ONLY valid JSON: {"hello":"world"}.'
    emitted = requested.payloads[0]
    assert emitted["metadata"]["task_template_id"] == "governance_hello_world.v1"
    assert bool(emitted["policy_contract"]["constraints"]["approval_required"]) is True
