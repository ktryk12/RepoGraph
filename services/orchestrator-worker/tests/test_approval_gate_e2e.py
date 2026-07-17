from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from orchestrator_worker import OrchestratorWorker
from request_gate.application.use_cases import ValidateAndEnqueueDecisionRequest
from request_gate.infrastructure.dedupe_store import RedisDedupeStore
from request_gate.infrastructure.kafka_consumer import KafkaDecisionRequestedConsumer
from request_gate.infrastructure.kafka_publishers import KafkaDlqPublisher, KafkaLifecyclePublisher
from babyai_shared.storage.artifact_store import FileArtifactStore
from babyai_shared.storage.context_store import InMemoryContextStore
from babyai_shared.storage.decision_status_store import InMemoryDecisionStatusStore
from approval_gate_testutils import (
    Consumer,
    Producer,
    bootstrap_server,
    cleanup_config,
    create_event_bus_with_topics,
    is_partition_eof,
    require_kafka_or_skip,
    seed_topics,
    wait_for_assignment,
)


@pytest.mark.integration
def test_approval_gate_blocks_execution_until_explicit_approval(monkeypatch, tmp_path: Path) -> None:
    require_kafka_or_skip()
    run_id = f"approval-gate-{time.time_ns()}"
    event_bus, config_path = create_event_bus_with_topics(run_id=run_id)
    topics = dict(event_bus.config.get("topics") or {})
    requested_topic = str(topics["decision_requested"])
    lifecycle_topic = str(topics["decision_lifecycle"])
    approval_topic = str(topics["decision_approval"])
    artifact_events_topic = str(topics["artifact_events"])
    tool_events_topic = str(topics["tool_events"])
    dlq_topic = str(topics["decision_lifecycle_dlq"])
    decision_id = f"dec-{run_id}"
    context_id = f"ctx-{run_id}"

    worker = OrchestratorWorker(
        event_bus=event_bus,
        artifact_store=FileArtifactStore(root=tmp_path / "artifacts"),
        context_store=InMemoryContextStore(),
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )

    def _forbidden_run_episode(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("run_episode must not run before approval")

    monkeypatch.setattr("orchestrator_worker.run_episode", _forbidden_run_episode)
    monkeypatch.setattr("orchestrator_worker.load_truth_pack", lambda ref: {"pack_hash": str(ref)})

    lifecycle_publisher = KafkaLifecyclePublisher(bootstrap_servers=bootstrap_server(), topic=lifecycle_topic)
    dlq_publisher = KafkaDlqPublisher(bootstrap_servers=bootstrap_server(), topic=f"decision.requested.dlq.{run_id}")
    adapter = KafkaDecisionRequestedConsumer(
        bootstrap_servers=bootstrap_server(),
        topic=requested_topic,
        group_id=f"request-gate-{run_id}",
        poll_timeout_seconds=0.25,
        use_case=ValidateAndEnqueueDecisionRequest(
            dedupe_store=RedisDedupeStore(redis_url=None, allow_in_memory_fallback=True),
            lifecycle_publisher=lifecycle_publisher,
            dlq_publisher=dlq_publisher,
            policy_validator=None,
        ),
        dlq_publisher=dlq_publisher,
    )

    observer = Consumer(  # type: ignore[misc]
        {
            "bootstrap.servers": bootstrap_server(),
            "group.id": f"observer-{run_id}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    orchestrator_consumer = Consumer(  # type: ignore[misc]
        {
            "bootstrap.servers": bootstrap_server(),
            "group.id": f"orchestrator-driver-{run_id}",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )

    def _drive_orchestrator_once() -> None:
        msg = orchestrator_consumer.poll(0.1)
        if msg is None:
            return
        if msg.error():
            return
        worker._handle_message(msg, orchestrator_consumer)
        event_bus.flush()

    seed_topics(topic_names=[requested_topic, lifecycle_topic, approval_topic, artifact_events_topic, tool_events_topic, dlq_topic])
    orchestrator_consumer.subscribe([lifecycle_topic, approval_topic])
    wait_for_assignment(orchestrator_consumer, topic=lifecycle_topic, timeout_seconds=15.0)
    observer.subscribe([lifecycle_topic, artifact_events_topic, tool_events_topic])
    wait_for_assignment(observer, topic=lifecycle_topic, timeout_seconds=15.0)

    producer = Producer({"bootstrap.servers": bootstrap_server(), "message.timeout.ms": 5000})  # type: ignore[misc]
    request_payload = {
        "decision_id": decision_id,
        "context_id": context_id,
        "task_ref": f"artifact:sha256:task-{run_id}",
        "truth_pack_ref": "layered_default",
        "truth_pack_version": 1,
        "policy_contract": {
            "policy_id": "restricted",
            "allow_enqueue": True,
            "constraints": {"approval_required": True, "visibility": "restricted"},
        },
        "metadata": {"user_prompt": '{"hello":"world"}', "policy_preset": "restricted"},
    }
    producer.produce(
        topic=requested_topic,
        key=decision_id.encode("utf-8"),
        value=json.dumps(request_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )
    assert producer.flush(10.0) == 0

    try:
        deadline_process = time.monotonic() + 20.0
        processed_count = 0
        while time.monotonic() < deadline_process:
            _drive_orchestrator_once()
            processed_count += adapter.run_once()
            if processed_count >= 2:
                break
        assert processed_count >= 2, "request-gate did not process bootstrap + decision.requested messages"

        requested_event: dict[str, Any] | None = None
        waiting_event: dict[str, Any] | None = None
        statuses: list[str] = []
        deadline_lifecycle = time.monotonic() + 30.0
        while time.monotonic() < deadline_lifecycle and (requested_event is None or waiting_event is None):
            _drive_orchestrator_once()
            msg = observer.poll(0.5)
            if msg is None:
                continue
            if msg.error():
                if is_partition_eof(msg):
                    continue
                raise AssertionError(f"Kafka consume error: {msg.error()}")
            if str(msg.topic() or "") != lifecycle_topic:
                continue
            raw = msg.value()
            if raw is None:
                continue
            payload = json.loads(raw.decode("utf-8"))
            if str(payload.get("decision_id") or "") != decision_id:
                continue
            status = str(payload.get("status") or "")
            if status:
                statuses.append(status)
            if status == "requested":
                requested_event = dict(payload)
            if status == "waiting_for_approval":
                waiting_event = dict(payload)

        assert requested_event is not None, f"missing lifecycle requested event statuses={statuses}"
        metadata = dict(requested_event.get("metadata") or {})
        assert isinstance(metadata.get("effective_policy"), dict)
        assert isinstance(metadata.get("policy_explanation"), dict)
        assert str(metadata.get("policy_fingerprint") or "").strip()
        if waiting_event is None:
            assert bool(metadata.get("approval_required")) is True

        guard_seconds = int(str(os.getenv("APPROVAL_GUARD_SECONDS", "20")))
        guard_deadline = time.monotonic() + float(guard_seconds)
        violating_statuses: list[str] = []
        write_events: list[dict[str, Any]] = []
        tool_events: list[dict[str, Any]] = []
        while time.monotonic() < guard_deadline:
            _drive_orchestrator_once()
            msg = observer.poll(0.5)
            if msg is None:
                continue
            if msg.error():
                if is_partition_eof(msg):
                    continue
                raise AssertionError(f"Kafka consume error in guard window: {msg.error()}")
            raw = msg.value()
            if raw is None:
                continue
            payload = json.loads(raw.decode("utf-8"))
            topic = str(msg.topic() or "")
            if topic == lifecycle_topic and str(payload.get("decision_id") or "") == decision_id:
                status = str(payload.get("status") or "")
                if status in {"started", "generating", "evaluating", "evaluated", "repairing", "completed", "failed"}:
                    violating_statuses.append(status)
            if topic == artifact_events_topic and str(payload.get("context_id") or "") == context_id:
                write_events.append(dict(payload))
            if topic == tool_events_topic and str(payload.get("decision_id") or "") == decision_id:
                tool_events.append(dict(payload))

        assert not violating_statuses, f"execution progressed before approval statuses={violating_statuses}"
        assert not write_events, f"unexpected artifact events before approval events={write_events}"
        assert not tool_events, f"unexpected tool events before approval events={tool_events}"
    finally:
        try:
            observer.close()
        except Exception:
            pass
        try:
            orchestrator_consumer.close()
        except Exception:
            pass
        adapter.close()
        lifecycle_publisher.close()
        dlq_publisher.close()
        event_bus.shutdown()
        cleanup_config(config_path)
