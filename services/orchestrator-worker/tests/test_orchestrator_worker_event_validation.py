from __future__ import annotations

from typing import Any
import json

from bus import metrics
from bus.event_schemas import DecisionEvent, DecisionStatus, SCHEMA_VERSION, now_iso
from orchestrator_worker import OrchestratorWorker
from babyai_shared.storage.artifact_store import FileArtifactStore
from babyai_shared.storage.context_store import InMemoryContextStore
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
        self.published.append(
            {
                "topic": str(topic),
                "key": str(key),
                "value": str(value),
                "headers": dict(headers or {}),
            }
        )


class _FakeMessage:
    def __init__(self, *, topic: str, payload: str) -> None:
        self._topic = topic
        self._payload = payload

    def error(self) -> None:
        return None

    def topic(self) -> str:
        return self._topic

    def value(self) -> bytes:
        return self._payload.encode("utf-8")


class _FakeConsumer:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self, *, message: Any, asynchronous: bool) -> None:
        _ = (message, asynchronous)
        self.commits += 1


def test_invalid_episode_requested_event_goes_to_dlq_and_skips_processing(caplog, tmp_path) -> None:
    metrics._reset_local_counts_for_test()
    caplog.set_level("INFO")
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        artifact_store=FileArtifactStore(root=tmp_path / "artifacts"),
        context_store=InMemoryContextStore(),
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    calls: list[str] = []
    worker._process_episode = lambda event: calls.append(str(event.decision_id))  # type: ignore[method-assign]

    invalid_payload = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "decision_id": "",
            "context_id": "ctx-invalid-1",
            "status": "requested",
            "timestamp": now_iso(),
            "task_ref": "artifact:sha256:" + ("1" * 64),
            "truth_pack_ref": "default",
            "truth_pack_version": "v1",
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    msg = _FakeMessage(topic="decision.lifecycle", payload=invalid_payload)
    consumer = _FakeConsumer()

    worker._handle_message(msg, consumer)

    assert calls == []
    assert consumer.commits == 1
    assert len(bus.published) == 1
    row = bus.published[0]
    assert row["topic"] == "decision.lifecycle.dlq"
    payload = json.loads(row["value"])
    assert payload["event_type"] == "InvalidEvent"
    assert payload["violation_type"] == "PolicyViolation"
    assert "episode_requested_schema_invalid" in str(payload["reason"])
    assert metrics.snapshot_local_counts()["dlq_published"] == 1
    telemetry_rows = [str(row.message) for row in caplog.records if "telemetry=" in str(row.message)]
    assert any('"event_type": "dlq_publish"' in row for row in telemetry_rows)
    assert any('"episode_id": "unknown"' in row for row in telemetry_rows)
    assert any('"reason": "episode_requested_schema_invalid' in row for row in telemetry_rows)


def test_valid_episode_requested_event_starts_processing_with_correlation_telemetry(caplog, tmp_path) -> None:
    metrics._reset_local_counts_for_test()
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        artifact_store=FileArtifactStore(root=tmp_path / "artifacts"),
        context_store=InMemoryContextStore(),
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    calls: list[str] = []
    worker._process_episode = lambda event: calls.append(str(event.decision_id))  # type: ignore[method-assign]
    caplog.set_level("INFO")

    requested = DecisionEvent(
        schema_version=SCHEMA_VERSION,
        decision_id="ep-valid-1",
        context_id="ctx-valid-1",
        status=DecisionStatus.REQUESTED,
        timestamp=now_iso(),
        task_ref="artifact:sha256:" + ("2" * 64),
        truth_pack_ref="default",
        truth_pack_version="v1",
        metadata={"trace_id": "trace-valid-1"},
    )
    msg = _FakeMessage(topic="decision.lifecycle", payload=requested.to_json())
    consumer = _FakeConsumer()

    worker._handle_message(msg, consumer)

    assert calls == ["ep-valid-1"]
    assert consumer.commits == 1
    assert bus.published == []
    telemetry_rows = [str(row.message) for row in caplog.records if "telemetry=" in str(row.message)]
    assert any('"event_type": "orchestrator_worker.processing_started"' in row for row in telemetry_rows)
    assert any('"episode_id": "ep-valid-1"' in row for row in telemetry_rows)
    assert any('"fingerprint": "' in row for row in telemetry_rows)
    assert any('"trace_id": "trace-valid-1"' in row for row in telemetry_rows)
    assert metrics.snapshot_local_counts()["dlq_published"] == 0
