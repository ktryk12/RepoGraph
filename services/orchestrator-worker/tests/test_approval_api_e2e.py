from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from bus.event_schemas import ApprovalEvent, DecisionEvent, DecisionStatus, SCHEMA_VERSION, now_iso
from orchestrator_worker import OrchestratorWorker
from request_gate import main as request_gate_main
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
def test_waiting_for_approval_can_be_unblocked_via_request_gate_api(monkeypatch, tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")
    require_kafka_or_skip()

    run_id = f"approval-api-{time.time_ns()}"
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
        lambda task, truth_pack, knobs=None, skill_bundle=None: _FakeEpisode(
            decision={"hello": "world"},
            eval_result={"passed": True, "scores": {"total": 1.0}, "errors": []},
            telemetry={"repairs_used": 0, "runner_used": "fake", "tokens_used": 1, "latency_ms": 1.0},
        ),
    )

    worker = OrchestratorWorker(
        event_bus=event_bus,
        artifact_store=artifact_store,
        context_store=InMemoryContextStore(),
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )

    for name, value in {
        "REQUEST_GATE_BOOTSTRAP_SERVERS": bootstrap_server(),
        "REQUEST_GATE_INPUT_TOPIC": requested_topic,
        "REQUEST_GATE_LIFECYCLE_TOPIC": lifecycle_topic,
        "REQUEST_GATE_APPROVAL_TOPIC": approval_topic,
        "REQUEST_GATE_DLQ_TOPIC": f"decision.requested.dlq.{run_id}",
        "REQUEST_GATE_GROUP_ID": f"request-gate-{run_id}",
        "REQUEST_GATE_LIFECYCLE_OBSERVER_GROUP_ID": f"request-gate-approvals-{run_id}",
        "REQUEST_GATE_POLICY_VALIDATOR_ENABLED": "false",
        "REQUEST_GATE_REDIS_URL": "",
        "REQUEST_GATE_ALLOW_IN_MEMORY_DEDUPE": "true",
    }.items():
        monkeypatch.setenv(name, value)

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

    seed_topics(topic_names=[requested_topic, lifecycle_topic, approval_topic, artifact_events_topic, tool_events_topic])
    orchestrator_consumer.subscribe([lifecycle_topic, approval_topic])
    wait_for_assignment(orchestrator_consumer, topic=lifecycle_topic, timeout_seconds=15.0)
    observer.subscribe([lifecycle_topic])
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

    try:
        app = request_gate_main.create_app()
        with testclient.TestClient(app) as client:
            producer.produce(
                topic=requested_topic,
                key=decision_id.encode("utf-8"),
                value=json.dumps(request_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )
            assert producer.flush(10.0) == 0

            statuses: list[str] = []
            waiting_seen = False
            requested_event: dict[str, Any] | None = None
            deadline_waiting = time.monotonic() + 30.0
            while time.monotonic() < deadline_waiting and not waiting_seen:
                _drive_orchestrator_once()
                msg = observer.poll(0.5)
                if msg is None:
                    continue
                if msg.error():
                    if is_partition_eof(msg):
                        continue
                    raise AssertionError(f"Kafka consume error: {msg.error()}")
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
                    waiting_seen = True

            assert requested_event is not None
            requested_metadata = dict(requested_event.get("metadata") or {})
            expected_fingerprint = str(requested_metadata.get("policy_fingerprint") or "").strip()
            assert expected_fingerprint

            pending_payload = None
            deadline_pending = time.monotonic() + 20.0
            while time.monotonic() < deadline_pending:
                _drive_orchestrator_once()
                response = client.get("/approvals/pending")
                assert response.status_code == 200
                rows = response.json()
                if isinstance(rows, list):
                    for row in rows:
                        if str(row.get("decision_id") or "") == decision_id:
                            pending_payload = dict(row)
                            break
                if pending_payload is not None:
                    break
                time.sleep(0.5)
            assert pending_payload is not None, "missing pending approval in request-gate API"
            assert str(pending_payload.get("required_policy_fingerprint") or "") == expected_fingerprint

            approve_response = client.post(
                f"/approvals/{decision_id}/approve",
                json={"approved_by": "approval-api-e2e", "reason": "approved"},
            )
            assert approve_response.status_code == 200, approve_response.text
            approve_payload = approve_response.json()
            assert approve_payload.get("ok") is True
            assert str(approve_payload.get("policy_fingerprint") or "") == expected_fingerprint

            permit = {
                "decision_id": decision_id,
                "policy_fingerprint": expected_fingerprint,
                "approved_by": str(approve_payload.get("approved_by") or "approval-api-e2e"),
                "approved_at": str(approve_payload.get("approved_at") or now_iso()),
                "reason": str(approve_payload.get("reason") or "approved"),
            }
            worker._handle_approval_event(
                approval_event=ApprovalEvent(
                    schema_version=SCHEMA_VERSION,
                    decision_id=decision_id,
                    context_id=context_id,
                    approved=True,
                    policy_fingerprint=expected_fingerprint,
                    approved_by=str(permit["approved_by"]),
                    approved_at=str(permit["approved_at"]),
                    reason=str(permit["reason"]),
                )
            )
            requested_with_permit = DecisionEvent(
                schema_version=SCHEMA_VERSION,
                decision_id=decision_id,
                context_id=context_id,
                status=DecisionStatus.REQUESTED,
                timestamp=now_iso(),
                task_ref=str(request_payload["task_ref"]),
                truth_pack_ref=str(request_payload["truth_pack_ref"]),
                truth_pack_version=str(request_payload["truth_pack_version"]),
                metadata={
                    **requested_metadata,
                    "execution_permit": permit,
                    "approval_token": permit,
                    "approval_granted": True,
                    "approval_granted_at": permit["approved_at"],
                    "approval_granted_by": permit["approved_by"],
                    "policy_fingerprint": expected_fingerprint,
                },
            )

            worker._process_episode(requested_with_permit)
            event_bus.flush()

            terminal = ""
            deadline_terminal = time.monotonic() + 20.0
            while time.monotonic() < deadline_terminal:
                record = worker.status_store.get(decision_id)
                if record is not None and str(record.status or "") in {"completed", "failed"}:
                    terminal = str(record.status or "")
                    break
                time.sleep(0.2)

            assert terminal == "completed", f"unexpected terminal status={terminal}"
    finally:
        try:
            observer.close()
        except Exception:
            pass
        try:
            orchestrator_consumer.close()
        except Exception:
            pass
        event_bus.shutdown()
        cleanup_config(config_path)
