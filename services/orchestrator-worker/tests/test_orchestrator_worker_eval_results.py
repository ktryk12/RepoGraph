from __future__ import annotations

import json
from typing import Any

from bus.event_schemas import DecisionEvent, DecisionStatus, SCHEMA_VERSION, now_iso
from orchestrator_worker import OrchestratorWorker
from babyai_shared.storage.decision_status_store import InMemoryDecisionStatusStore


class _FakeEventBus:
    def __init__(self) -> None:
        self.config = {
            "topics": {
                "decision_lifecycle": "decision.lifecycle",
                "eval_results": "eval.results",
                "artifact_events": "artifact.events",
                "decision_lifecycle_dlq": "decision.lifecycle.dlq",
            },
            "consumer": {"retry_max_attempts": 3, "retry_backoff_seconds": 0},
            "dedupe": {"running_ttl_seconds": 30, "final_ttl_seconds": 300},
        }
        self.published: list[dict[str, Any]] = []

    def publish(self, *, topic: str, key: str, value: str, headers: dict[str, str] | None = None) -> None:
        self.published.append({"topic": str(topic), "key": str(key), "value": str(value), "headers": dict(headers or {})})


def _event() -> DecisionEvent:
    return DecisionEvent(
        schema_version=SCHEMA_VERSION,
        decision_id="dec-eval-1",
        context_id="ctx-eval-1",
        status=DecisionStatus.REQUESTED,
        timestamp=now_iso(),
        task_ref="artifact:sha256:" + ("1" * 64),
        truth_pack_ref="layered_default",
        truth_pack_version=1,
    )


def test_eval_results_publish_keeps_non_empty_components() -> None:
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    event = _event()
    passed, score, components, reasons = worker._build_eval_payload(
        event=event,
        task={"task_id": "TASK-1", "spec": {}, "expected": {}},
        decision={"decision_id": "D-1", "chosen_style": "layered", "topology": {"separated_services": []}},
        decision_ref="artifact:sha256:" + ("2" * 64),
        eval_result={
            "passed": True,
            "scores": {
                "functional": 0.9,
                "security": 0.8,
                "architecture_fit": 0.7,
                "total": 0.83,
            },
            "errors": [],
        },
    )
    assert passed is True
    assert components != {}
    assert set(components.keys()) >= {"functional", "security", "architecture_fit", "total"}
    assert reasons == []
    worker._publish_eval_result(
        event=event,
        iteration=1,
        passed=passed,
        score=score,
        components=components,
        gate_results={"ops_readiness": True},
        penalties=[],
        failure_reasons=reasons,
        decision_ref="artifact:sha256:" + ("2" * 64),
    )
    rows = [json.loads(row["value"]) for row in bus.published if row["topic"] == "eval.results"]
    assert len(rows) == 1
    assert rows[0]["components"] != {}
    assert set(rows[0]["components"].keys()) >= {"functional", "security", "architecture_fit", "total"}


def test_failed_eval_has_non_empty_failure_reasons() -> None:
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    event = _event()

    passed_empty, _, _, reasons_empty = worker._build_eval_payload(
        event=event,
        task={"task_id": "TASK-2"},
        decision={},
        decision_ref="artifact:sha256:" + ("3" * 64),
        eval_result={"passed": False, "scores": None, "errors": []},
    )
    assert passed_empty is False
    assert reasons_empty == ["missing_eval_components"]

    passed_scored, _, _, reasons_scored = worker._build_eval_payload(
        event=event,
        task={"task_id": "TASK-3"},
        decision={"decision_id": "D-3"},
        decision_ref="artifact:sha256:" + ("4" * 64),
        eval_result={
            "passed": False,
            "scores": {"functional": 0.4, "security": 0.4, "architecture_fit": 0.4, "total": 0.4},
            "errors": [],
        },
    )
    assert passed_scored is False
    assert "unknown_failure_no_reason" in reasons_scored
    assert "score_below_threshold" in reasons_scored


def test_auto_template_with_generated_output_is_scored_without_architecture_fallback(
    monkeypatch,
) -> None:
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    event = _event()

    def _should_not_be_called(*_args, **_kwargs):
        raise AssertionError("architecture fallback should not run for template=auto")

    monkeypatch.setattr(worker, "_score_from_architecture", _should_not_be_called)

    passed, score, components, reasons = worker._build_eval_payload(
        event=event,
        task={"template": "auto", "prompt": "Hvad er hovedstaden i Frankrig?"},
        decision={"generated_output": {"text": "Paris"}},
        decision_ref="artifact:sha256:" + ("5" * 64),
        eval_result={"passed": False, "scores": None, "errors": []},
    )

    assert passed is True
    assert score == 1.0
    assert components != {}
    assert components["answer_present"] == 1.0
    assert components["answer_not_repetitive"] == 1.0
    assert components["answer_not_meta"] == 1.0
    assert components["answer_relevant_to_prompt"] == 1.0
    assert components["total"] == 1.0
    assert "missing_eval_components" not in reasons
    assert reasons == []


def test_auto_template_with_empty_generated_output_is_deterministic_without_architecture_fallback(
    monkeypatch,
) -> None:
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    event = _event()

    def _should_not_be_called(*_args, **_kwargs):
        raise AssertionError("architecture fallback should not run for template=auto")

    monkeypatch.setattr(worker, "_score_from_architecture", _should_not_be_called)

    passed, score, components, reasons = worker._build_eval_payload(
        event=event,
        task={"template": "auto", "prompt": "Hvad er hovedstaden i Frankrig?"},
        decision={"generated_output": {"text": ""}},
        decision_ref="artifact:sha256:" + ("6" * 64),
        eval_result={"passed": False, "scores": None, "errors": []},
    )

    assert passed is False
    assert score == 0.0
    assert components != {}
    assert components["answer_present"] == 0.0
    assert components["answer_not_repetitive"] == 0.0
    assert components["answer_not_meta"] == 0.0
    assert components["answer_relevant_to_prompt"] == 0.0
    assert components["total"] == 0.0
    assert "missing_eval_components" not in reasons
    assert reasons == ["Generated output text is empty for auto task."]


def test_auto_template_nonsense_answer_for_france_capital_fails(monkeypatch) -> None:
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    event = _event()

    def _should_not_be_called(*_args, **_kwargs):
        raise AssertionError("architecture fallback should not run for template=auto")

    monkeypatch.setattr(worker, "_score_from_architecture", _should_not_be_called)

    passed, score, components, reasons = worker._build_eval_payload(
        event=event,
        task={"template": "auto", "prompt": "Hvad er hovedstaden i Frankrig?"},
        decision={"generated_output": {"text": "Unterscheidung mellem hovedstaden og hovedstaden i Frankrig?"}},
        decision_ref="artifact:sha256:" + ("9" * 64),
        eval_result={"passed": False, "scores": None, "errors": []},
    )

    assert passed is False
    assert score < 1.0
    assert components["answer_present"] == 1.0
    assert components["answer_relevant_to_prompt"] == 0.0
    assert "Generated output does not include expected answer 'paris'." in reasons


def test_non_auto_template_without_scores_still_uses_architecture_fallback(monkeypatch) -> None:
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    event = _event()
    calls: dict[str, bool] = {"called": False}

    def _fake_architecture(*, event, task, decision, decision_ref):  # type: ignore[no-redef]
        calls["called"] = True
        return 0.91, {
            "functional": 0.9,
            "security": 0.9,
            "architecture_fit": 0.93,
            "total": 0.91,
        }

    monkeypatch.setattr(worker, "_score_from_architecture", _fake_architecture)

    passed, score, components, reasons = worker._build_eval_payload(
        event=event,
        task={"template": "architecture", "task_id": "TASK-ARCH-1"},
        decision={"decision_id": "D-ARCH-1"},
        decision_ref="artifact:sha256:" + ("7" * 64),
        eval_result={"passed": False, "scores": None, "errors": []},
    )

    assert calls["called"] is True
    assert passed is False
    assert score == 0.91
    assert components["total"] == 0.91
    assert "missing_eval_components" not in reasons


def test_eval_payload_emits_debug_observability_logs(caplog: Any) -> None:
    caplog.set_level("INFO", logger="orchestrator_worker")
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    event = _event()

    passed, score, components, reasons = worker._build_eval_payload(
        event=event,
        task={"template": "auto", "prompt": "Hvad er hovedstaden i Frankrig?"},
        decision={"generated_output": {"text": "Paris"}},
        decision_ref="artifact:sha256:" + ("8" * 64),
        eval_result={"passed": False, "scores": None, "errors": []},
    )

    assert passed is True
    assert score == 1.0
    assert components["answer_present"] == 1.0
    assert components["answer_relevant_to_prompt"] == 1.0
    assert reasons == []
    assert "debug_eval_input" in caplog.text
    assert "debug_eval_result" in caplog.text
