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


def test_governance_template_writes_artifact_and_publishes_non_empty_components(
    monkeypatch: Any,
    caplog: Any,
    tmp_path: Path,
) -> None:
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
        json.dumps(
            {
                "schema_version": 1,
                "template": "governance_hello_world.v1",
                "task_id": "governance-123",
                "prompt": 'Return ONLY valid JSON: {"hello":"world"}.',
            },
            ensure_ascii=True,
            sort_keys=True,
        ).encode("utf-8"),
        context_id="dev",
        name="task:governance-123",
        metadata={"type": "task"},
    ).ref

    def _fake_run_episode(task: dict[str, Any], truth_pack: dict[str, Any], knobs: dict[str, Any] | None = None, skill_bundle: Any | None = None) -> _FakeEpisode:
        _ = (task, truth_pack, knobs, skill_bundle)
        return _FakeEpisode(
            decision={"decision_id": "dec-governance-1", "generated_output": {"text": '{"hello":"world"}'}},
            eval_result={"passed": False, "scores": None, "errors": []},
            telemetry={"repairs_used": 0, "runner_used": "fake", "tokens_used": 1, "latency_ms": 1.0},
        )

    monkeypatch.setattr("orchestrator_worker.load_truth_pack", lambda ref: {"version": str(ref)})
    monkeypatch.setattr("orchestrator_worker.run_episode", _fake_run_episode)
    caplog.set_level("INFO", logger="orchestrator_worker")

    event = DecisionEvent(
        schema_version=SCHEMA_VERSION,
        decision_id="dec-governance-1",
        context_id="dev",
        status=DecisionStatus.REQUESTED,
        timestamp=now_iso(),
        task_ref=task_ref,
        truth_pack_ref="layered_default",
        truth_pack_version=1,
        metadata={
            "effective_policy": {"write_scope": {"type": "policy_service"}},
            "policy_fingerprint": "c" * 64,
            "execution_permit": {
                "decision_id": "dec-governance-1",
                "policy_fingerprint": "c" * 64,
                "approved_by": "tester",
                "approved_at": "2026-01-01T00:00:00Z",
            },
        },
    )
    worker._process_episode(event)

    index = artifact_store.list_context_index("dev")
    governance_row = index.get("governance_smoke.v1")
    assert isinstance(governance_row, dict)
    governance_ref = str(governance_row.get("ref") or "")
    assert governance_ref.startswith("artifact:sha256:")

    governance_payload_raw = artifact_store.get(governance_ref)
    assert governance_payload_raw is not None
    governance_payload = json.loads(governance_payload_raw.decode("utf-8"))
    assert governance_payload["payload"] == {"hello": "world"}

    eval_rows = [json.loads(row["value"]) for row in bus.published if row["topic"] == "eval.results"]
    assert len(eval_rows) == 1
    eval_payload = eval_rows[0]
    assert bool(eval_payload["passed"]) is True
    assert isinstance(eval_payload.get("components"), dict)
    assert eval_payload["components"] != {}
    assert set(eval_payload["components"].keys()) >= {"functional", "security", "architecture_fit"}

    milestone_rows = [str(record.getMessage()) for record in caplog.records if '"milestone":"artifact_written"' in str(record.getMessage())]
    assert any("governance_smoke.v1" in row for row in milestone_rows)
