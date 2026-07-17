from __future__ import annotations

from typing import Any
import json

import pytest

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

    def publish(self, *args: Any, **kwargs: Any) -> None:
        _ = (args, kwargs)


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


def test_orchestrator_worker_dedupes_duplicate_requested_event(caplog, tmp_path) -> None:
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        artifact_store=FileArtifactStore(root=tmp_path / "artifacts"),
        context_store=InMemoryContextStore(),
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    calls: list[str] = []

    def _fake_process(event: DecisionEvent) -> None:
        calls.append(str(event.decision_id))

    worker._process_episode = _fake_process  # type: ignore[method-assign]
    caplog.set_level("INFO")

    requested = DecisionEvent(
        schema_version=SCHEMA_VERSION,
        decision_id="ep-dup-1",
        context_id="ctx-dup-1",
        status=DecisionStatus.REQUESTED,
        timestamp=now_iso(),
        task_ref="artifact:sha256:" + ("1" * 64),
        truth_pack_ref="default",
        truth_pack_version="v1",
    )
    payload = requested.to_json()
    msg1 = _FakeMessage(topic="decision.lifecycle", payload=payload)
    msg2 = _FakeMessage(topic="decision.lifecycle", payload=payload)
    consumer = _FakeConsumer()

    worker._handle_message(msg1, consumer)
    worker._handle_message(msg2, consumer)

    assert calls == ["ep-dup-1"]
    assert consumer.commits == 2
    assert any('"deduped": true' in str(row.message) for row in caplog.records)


def test_orchestrator_worker_drops_replay_after_completion(caplog, tmp_path) -> None:
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        artifact_store=FileArtifactStore(root=tmp_path / "artifacts"),
        context_store=InMemoryContextStore(),
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    calls: list[str] = []

    def _fake_process(event: DecisionEvent) -> None:
        calls.append(str(event.decision_id))
        worker.status_store.set_status(str(event.decision_id), "completed", ttl_seconds=worker._final_ttl())

    worker._process_episode = _fake_process  # type: ignore[method-assign]
    caplog.set_level("INFO")

    requested = DecisionEvent(
        schema_version=SCHEMA_VERSION,
        decision_id="ep-replay-1",
        context_id="ctx-replay-1",
        status=DecisionStatus.REQUESTED,
        timestamp=now_iso(),
        task_ref="artifact:sha256:" + ("2" * 64),
        truth_pack_ref="default",
        truth_pack_version="v1",
    )
    payload = requested.to_json()
    msg1 = _FakeMessage(topic="decision.lifecycle", payload=payload)
    msg2 = _FakeMessage(topic="decision.lifecycle", payload=payload)
    consumer = _FakeConsumer()

    worker._handle_message(msg1, consumer)
    worker._handle_message(msg2, consumer)

    assert calls == ["ep-replay-1"]
    assert consumer.commits == 2
    assert any('"deduped": true' in str(row.message) and "status_final" in str(row.message) for row in caplog.records)


def test_orchestrator_worker_requires_persistent_dedupe_in_production(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    bus = _FakeEventBus()
    with pytest.raises(RuntimeError, match="idempotency_persistent_store_required"):
        OrchestratorWorker(
            event_bus=bus,  # type: ignore[arg-type]
            artifact_store=FileArtifactStore(root=tmp_path / "artifacts"),
            context_store=InMemoryContextStore(),
            status_store=InMemoryDecisionStatusStore(),
            idempotency_lock=None,
        )


def test_orchestrator_worker_dedupes_by_canonical_episode_id(caplog, tmp_path) -> None:
    bus = _FakeEventBus()
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        artifact_store=FileArtifactStore(root=tmp_path / "artifacts"),
        context_store=InMemoryContextStore(),
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )
    calls: list[str] = []

    def _fake_process(event: DecisionEvent) -> None:
        calls.append(str(event.decision_id))

    worker._process_episode = _fake_process  # type: ignore[method-assign]
    caplog.set_level("INFO")

    episode_id = "ep-canonical-1"
    payload = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "episode_id": episode_id,
            "context_id": "ctx-canonical-1",
            "status": "requested",
            "timestamp": now_iso(),
            "task_ref": "artifact:sha256:" + ("3" * 64),
            "truth_pack_ref": "v1",
            "truth_pack_version": "v1",
        },
        ensure_ascii=True,
        sort_keys=True,
    )
    msg1 = _FakeMessage(topic="decision.lifecycle", payload=payload)
    msg2 = _FakeMessage(topic="decision.lifecycle", payload=payload)
    consumer = _FakeConsumer()

    worker._handle_message(msg1, consumer)
    worker._handle_message(msg2, consumer)

    assert calls == [episode_id]
    assert consumer.commits == 2
    assert any('"deduped": true' in str(row.message) for row in caplog.records)
