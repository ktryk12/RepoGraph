from __future__ import annotations

from dataclasses import dataclass
import json
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


def _milestone_rows(caplog: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in caplog.records:
        raw = str(record.getMessage() or "")
        if not raw.startswith("{"):
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict) and payload.get("service_name") == "orchestrator-worker":
            rows.append(payload)
    return rows


def test_orchestrator_milestones_log_eval_order(caplog: Any, monkeypatch: Any, tmp_path: Path) -> None:
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
        b'{"task_id":"TASK-MS-1","spec":{"goal":"x"},"expected":{},"write_scope":{"type":"policy_service"}}',
        context_id="ctx-ms-1",
        name="task:ms-1",
        metadata={"type": "task"},
    ).ref

    def _fake_run_episode(task: dict[str, Any], truth_pack: dict[str, Any], knobs: dict[str, Any] | None = None, skill_bundle: Any | None = None) -> _FakeEpisode:
        _ = (task, truth_pack, knobs, skill_bundle)
        return _FakeEpisode(
            decision={"decision_id": "arch-ms-1", "artifacts": [{"ref": "a"}], "output": {"text": "ok"}},
            eval_result={
                "passed": True,
                "scores": {"functional": 0.9, "security": 0.9, "architecture_fit": 0.9, "total": 0.9},
                "errors": [],
            },
            telemetry={"repairs_used": 0, "runner_used": "fake-runner", "tokens_used": 12, "latency_ms": 5.0},
        )

    monkeypatch.setattr("orchestrator_worker.load_truth_pack", lambda ref: {"version": ref})
    monkeypatch.setattr("orchestrator_worker.run_episode", _fake_run_episode)
    caplog.set_level("INFO", logger="orchestrator_worker")

    event = DecisionEvent(
        schema_version=SCHEMA_VERSION,
        decision_id="dec-ms-1",
        context_id="ctx-ms-1",
        status=DecisionStatus.REQUESTED,
        timestamp=now_iso(),
        task_ref=task_ref,
        truth_pack_ref="layered_default",
        truth_pack_version=1,
        metadata={
            "effective_policy": {"write_scope": {"type": "policy_service"}},
            "policy_fingerprint": "b" * 64,
            "execution_permit": {
                "decision_id": "dec-ms-1",
                "policy_fingerprint": "b" * 64,
                "approved_by": "tester",
                "approved_at": "2026-01-01T00:00:00Z",
            },
        },
    )
    worker._process_episode(event)

    rows = _milestone_rows(caplog)
    decision_rows = [row for row in rows if row.get("decision_id") == "dec-ms-1"]
    milestones = [str(row.get("milestone") or "") for row in decision_rows]
    assert "eval_started" in milestones
    assert "eval_done" in milestones
    assert milestones.index("eval_started") < milestones.index("eval_done")
