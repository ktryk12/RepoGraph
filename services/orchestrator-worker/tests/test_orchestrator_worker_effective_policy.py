from __future__ import annotations

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


def test_orchestrator_worker_passes_effective_policy_into_run_episode(monkeypatch, tmp_path: Path) -> None:
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
        b'{"task_id":"TASK-1","write_scope":{"type":"policy_service"}}',
        context_id="ctx-1",
        name="task:1",
        metadata={"type": "task"},
    ).ref

    captured_knobs: dict[str, Any] = {}

    def _fake_run_episode(task: dict[str, Any], truth_pack: dict[str, Any], knobs: dict[str, Any] | None = None, skill_bundle: Any | None = None) -> _FakeEpisode:
        _ = (task, truth_pack, skill_bundle)
        captured_knobs.update(dict(knobs or {}))
        return _FakeEpisode(
            decision={"decision_id": "d-1"},
            eval_result={"passed": True, "scores": {"total": 1.0}, "errors": []},
            telemetry={"repairs_used": 0},
        )

    monkeypatch.setattr("orchestrator_worker.load_truth_pack", lambda ref: {"version": ref, "pack_hash": "abc"})
    monkeypatch.setattr("orchestrator_worker.run_episode", _fake_run_episode)

    event = DecisionEvent(
        schema_version=SCHEMA_VERSION,
        decision_id="dec-1",
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
                "decision_id": "dec-1",
                "policy_fingerprint": "a" * 64,
                "approved_by": "tester",
                "approved_at": "2026-01-01T00:00:00Z",
            },
        },
    )

    worker._process_episode(event)

    effective_policy = captured_knobs.get("effective_policy")
    assert isinstance(effective_policy, dict)
    assert (effective_policy.get("write_scope") or {}).get("type") == "policy_service"
