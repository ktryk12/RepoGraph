from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        self.published.append({"topic": topic, "key": key, "value": value, "headers": dict(headers or {})})


@dataclass
class _FakeEpisode:
    decision: dict[str, Any]
    eval_result: dict[str, Any]
    telemetry: dict[str, Any]


def test_orchestrator_worker_emits_generation_metrics_and_non_empty_decision(monkeypatch, tmp_path: Path) -> None:
    bus = _FakeEventBus()
    artifact_store = FileArtifactStore(root=tmp_path / "artifacts")
    worker = OrchestratorWorker(
        event_bus=bus,  # type: ignore[arg-type]
        artifact_store=artifact_store,
        context_store=InMemoryContextStore(),
        status_store=InMemoryDecisionStatusStore(),
        idempotency_lock=None,
    )

    task_ref = artifact_store.put(
        b'{"task_id":"TASK-42","prompt":"Build a deterministic API","write_scope":{"type":"policy_service"}}',
        context_id="ctx-1",
        name="task:42",
        metadata={"type": "task"},
    ).ref

    def _fake_run_episode(task: dict[str, Any], truth_pack: dict[str, Any], knobs: dict[str, Any] | None = None, skill_bundle: Any | None = None) -> _FakeEpisode:
        _ = (task, truth_pack, knobs, skill_bundle)
        return _FakeEpisode(
            decision={
                "decision_id": "arch-TASK-42",
                "generated_output": {
                    "text": "Deterministic generated architecture output.",
                    "model_ref": "mamba-gpt-7b-q2",
                    "runner_ref": "llama.cpp",
                },
            },
            eval_result={"passed": True, "scores": {"total": 1.0}, "errors": []},
            telemetry={"repairs_used": 0, "runner_used": "llama.cpp", "tokens_used": 77, "latency_ms": 12.4},
        )

    monkeypatch.setattr("orchestrator_worker.load_truth_pack", lambda ref: {"version": ref, "pack_hash": "abc"})
    monkeypatch.setattr("orchestrator_worker.run_episode", _fake_run_episode)

    event = DecisionEvent(
        schema_version=SCHEMA_VERSION,
        decision_id="dec-42",
        context_id="ctx-1",
        status=DecisionStatus.REQUESTED,
        timestamp=now_iso(),
        task_ref=task_ref,
        truth_pack_ref="layered_default",
        truth_pack_version=1,
        metadata={
            "effective_policy": {"write_scope": {"type": "policy_service"}},
            "policy_fingerprint": "a" * 64,
            "execution_permit": {
                "decision_id": "dec-42",
                "policy_fingerprint": "a" * 64,
                "approved_by": "tester",
                "approved_at": "2026-01-01T00:00:00Z",
            },
        },
    )
    worker._process_episode(event)

    eval_rows = [
        json.loads(row["value"])
        for row in bus.published
        if row["topic"] == "eval.results"
    ]
    assert len(eval_rows) == 1
    eval_payload = eval_rows[0]
    assert eval_payload["runner_used"] == "llama.cpp"
    assert eval_payload["tokens_used"] == 77
    assert eval_payload["latency_ms"] == 12.4

    lifecycle_rows = [
        json.loads(row["value"])
        for row in bus.published
        if row["topic"] == "decision.lifecycle"
    ]
    evaluated = [row for row in lifecycle_rows if row.get("status") == "evaluated"]
    assert evaluated, "expected evaluated lifecycle event"
    decision_ref = str((evaluated[-1].get("metadata") or {}).get("decision_ref") or "").strip()
    assert decision_ref

    stored_payload = artifact_store.get(decision_ref)
    assert stored_payload is not None
    parsed = json.loads(stored_payload.decode("utf-8"))
    assert parsed != {}
    assert (parsed.get("generated_output") or {}).get("text")
