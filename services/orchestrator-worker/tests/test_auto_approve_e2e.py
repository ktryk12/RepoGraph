from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from bus.event_schemas import ApprovalEvent, SCHEMA_VERSION, now_iso
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


@dataclass
class _FakeEpisode:
    decision: dict[str, Any]
    eval_result: dict[str, Any]
    telemetry: dict[str, Any]


@pytest.mark.integration
def test_auto_approve_event_unblocks_and_completes(monkeypatch, tmp_path: Path) -> None:
    if str(os.getenv("AUTO_APPROVE", "")).strip().lower() not in {"1", "true", "yes"}:
        pytest.skip("set AUTO_APPROVE=true to run approval-unblock e2e")
    require_kafka_or_skip()

    run_id = f"auto-approve-{time.time_ns()}"
    event_bus, config_path = create_event_bus_with_topics(run_id=run_id)
    topics = dict(event_bus.config.get("topics") or {})
    requested_topic = str(topics["decision_requested"])
    lifecycle_topic = str(topics["decision_lifecycle"])
    approval_topic = str(topics["decision_approval"])
    artifact_events_topic = str(topics["artifact_events"])
    tool_events_topic = str(topics["tool_events"])
    decision_id = f"dec-{run_id}"
    context_id = f"ctx-{run_id}"

    artifact_store = FileArtifactStore(root=tmp_path / "artifacts")
    worker = OrchestratorWorker(
        event_bus=event_bus,
        artifact_store=artifact_store,
        context_store=InMemoryContextStore(),
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )

    task_ref = artifact_store.put(
        json.dumps(
            {
                "task_id": f"TASK-{run_id}",
                "prompt": '{"hello":"world"}',
                "write_scope": {"type": "policy_service"},
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8"),
        context_id=context_id,
        name=f"task:{run_id}",
        metadata={"type": "task"},
    ).ref

    monkeypatch.setattr("orchestrator_worker.load_truth_pack", lambda ref: {"pack_hash": str(ref)})
    monkeypatch.setattr(
        "orchestrator_worker.run_episode",
        lambda task, truth_pack, knobs=None: _FakeEpisode(
            decision={"hello": "world"},
            eval_result={"passed": True, "scores": {"total": 1.0}, "errors": []},
            telemetry={"repairs_used": 0, "runner_used": "fake", "tokens_used": 1, "latency_ms": 1.0},
        ),
    )

    worker_thread = threading.Thread(target=worker.start, daemon=True)
    worker_thread.start()

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
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        }
    )
    seed_topics(topic_names=[requested_topic, lifecycle_topic, approval_topic, artifact_events_topic, tool_events_topic])
    observer.subscribe([lifecycle_topic, artifact_events_topic])
    wait_for_assignment(observer, topic=lifecycle_topic, timeout_seconds=15.0)

    producer = Producer({"bootstrap.servers": bootstrap_server(), "message.timeout.ms": 5000})  # type: ignore[misc]
    request_payload = {
        "decision_id": decision_id,
        "context_id": context_id,
        "task_ref": task_ref,
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
        processed = False
        while time.monotonic() < deadline_process:
            if adapter.run_once() > 0:
                processed = True
                break
        assert processed, "request-gate did not process decision.requested message"

        requested_payload_seen: dict[str, Any] | None = None
        waiting_seen = False
        deadline_waiting = time.monotonic() + 30.0
        while time.monotonic() < deadline_waiting and not waiting_seen:
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
            if str(payload.get("status") or "") == "requested":
                requested_payload_seen = dict(payload)
            if str(payload.get("status") or "") == "waiting_for_approval":
                waiting_seen = True

        assert waiting_seen, "expected waiting_for_approval before approval event"
        assert requested_payload_seen is not None, "missing requested event before approval"
        metadata = dict(requested_payload_seen.get("metadata") or {})
        policy_fingerprint = str(metadata.get("policy_fingerprint") or "").strip()
        assert policy_fingerprint

        approval_event = ApprovalEvent(
            schema_version=SCHEMA_VERSION,
            decision_id=decision_id,
            policy_fingerprint=policy_fingerprint,
            approved_by="auto-approve-test",
            approved_at=now_iso(),
            reason="AUTO_APPROVE",
        )
        producer.produce(
            topic=approval_topic,
            key=decision_id.encode("utf-8"),
            value=approval_event.to_json().encode("utf-8"),
        )
        assert producer.flush(10.0) == 0

        statuses: list[str] = []
        decision_ref = ""
        terminal = ""
        deadline_terminal = time.monotonic() + 40.0
        while time.monotonic() < deadline_terminal:
            msg = observer.poll(0.5)
            if msg is None:
                continue
            if msg.error():
                if is_partition_eof(msg):
                    continue
                raise AssertionError(f"Kafka consume error after approval: {msg.error()}")
            if str(msg.topic() or "") != lifecycle_topic:
                continue
            raw = msg.value()
            if raw is None:
                continue
            payload = json.loads(raw.decode("utf-8"))
            if str(payload.get("decision_id") or "") != decision_id:
                continue
            status = str(payload.get("status") or "")
            if status and (not statuses or statuses[-1] != status):
                statuses.append(status)
            metadata_out = payload.get("metadata")
            if isinstance(metadata_out, dict):
                candidate = str(metadata_out.get("decision_ref") or "").strip()
                if candidate:
                    decision_ref = candidate
            if status in {"completed", "failed"}:
                terminal = status
                break

        assert terminal == "completed", f"unexpected terminal status={terminal} statuses={statuses}"
        assert "started" in statuses and "generating" in statuses
        assert decision_ref
        raw_decision = artifact_store.get(decision_ref)
        assert raw_decision is not None
        parsed_decision = json.loads(raw_decision.decode("utf-8"))
        assert parsed_decision == {"hello": "world"}
    finally:
        try:
            observer.close()
        except Exception:
            pass
        adapter.close()
        lifecycle_publisher.close()
        dlq_publisher.close()
        worker.stop()
        worker_thread.join(timeout=5.0)
        event_bus.shutdown()
        cleanup_config(config_path)

