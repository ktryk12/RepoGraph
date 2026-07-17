from __future__ import annotations

import argparse
import json
import logging
import random
import tempfile
import time
from pathlib import Path
from typing import Any

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


class _TelemetryCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(str(record.getMessage()))
        except Exception:
            self.lines.append(str(record.msg))


def _build_valid_event(*, decision_id: str, task_ref: str, context_id: str) -> str:
    event = DecisionEvent(
        schema_version=SCHEMA_VERSION,
        decision_id=decision_id,
        context_id=context_id,
        status=DecisionStatus.REQUESTED,
        timestamp=now_iso(),
        task_ref=task_ref,
        truth_pack_ref="v1",
        truth_pack_version="v1",
        metadata={"trace_id": f"trace-{decision_id}"},
    )
    return event.to_json()


def _build_invalid_event(*, idx: int, task_ref: str) -> str:
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "decision_id": "",
            "context_id": f"ctx-invalid-{idx}",
            "status": "requested",
            "timestamp": now_iso(),
            "task_ref": task_ref,
            "truth_pack_ref": "v1",
            "truth_pack_version": "v1",
        },
        ensure_ascii=True,
        sort_keys=True,
    )


def run_soak(*, total_events: int = 500, seed: int = 7) -> dict[str, Any]:
    if total_events < 100:
        raise ValueError("total_events must be >= 100")

    duplicates_count = int(total_events * 0.10)
    invalid_count = int(total_events * 0.05)
    policy_fail_count = int(total_events * 0.05)
    unique_valid_count = total_events - duplicates_count - invalid_count
    expected_completed = unique_valid_count - policy_fail_count

    if expected_completed <= 0:
        raise ValueError("invalid soak distribution; expected_completed must be positive")

    randomizer = random.Random(seed)
    metrics._reset_local_counts_for_test()

    with tempfile.TemporaryDirectory(prefix="worker-soak-") as tmp:
        root = Path(tmp)
        artifacts_root = root / "artifacts"
        soak_outputs_root = root / "soak_outputs"
        soak_outputs_root.mkdir(parents=True, exist_ok=True)

        bus = _FakeEventBus()
        worker = OrchestratorWorker(
            event_bus=bus,  # type: ignore[arg-type]
            artifact_store=FileArtifactStore(root=artifacts_root),
            context_store=InMemoryContextStore(),
            status_store=InMemoryDecisionStatusStore(),
            idempotency_lock=None,
        )
        worker._retry_policy = lambda event: (3, 0)  # type: ignore[method-assign]
        consumer = _FakeConsumer()

        task_ref = worker.artifact_store.put(
            json.dumps({"task_id": "SOAK-001", "spec": {"title": "soak task"}}, ensure_ascii=True).encode("utf-8"),
            context_id="soak",
            name="task:soak",
            metadata={"type": "task"},
        ).ref

        success_ids = [f"ep-success-{idx:04d}" for idx in range(expected_completed)]
        policy_fail_ids = [f"ep-policy-fail-{idx:04d}" for idx in range(policy_fail_count)]
        duplicate_targets = randomizer.sample(success_ids, k=duplicates_count)

        payloads: list[str] = []
        for idx, decision_id in enumerate(success_ids):
            payloads.append(
                _build_valid_event(
                    decision_id=decision_id,
                    task_ref=task_ref,
                    context_id=f"ctx-success-{idx:04d}",
                )
            )
        for idx, decision_id in enumerate(policy_fail_ids):
            payloads.append(
                _build_valid_event(
                    decision_id=decision_id,
                    task_ref=task_ref,
                    context_id=f"ctx-policy-{idx:04d}",
                )
            )
        for idx in range(invalid_count):
            payloads.append(_build_invalid_event(idx=idx, task_ref=task_ref))
        for idx, target in enumerate(duplicate_targets):
            payloads.append(
                _build_valid_event(
                    decision_id=target,
                    task_ref=task_ref,
                    context_id=f"ctx-dup-{idx:04d}",
                )
            )
        randomizer.shuffle(payloads)

        invoked: list[str] = []
        completed: set[str] = set()
        policy_fail_set = set(policy_fail_ids)

        def _fake_process(event: DecisionEvent) -> None:
            decision_id = str(event.decision_id)
            invoked.append(decision_id)
            if decision_id in policy_fail_set:
                raise RuntimeError("policy_fail_simulated")
            marker = soak_outputs_root / f"{decision_id}.json"
            marker.write_text(
                json.dumps({"decision_id": decision_id, "status": "completed"}, ensure_ascii=True, sort_keys=True),
                encoding="utf-8",
            )
            completed.add(decision_id)
            worker.status_store.set_status(decision_id, "completed", ttl_seconds=worker._final_ttl())

        worker._process_episode = _fake_process  # type: ignore[method-assign]

        logger = logging.getLogger("orchestrator_worker")
        capture = _TelemetryCapture()
        logger.addHandler(capture)
        logger.setLevel(logging.INFO)

        started = time.perf_counter()
        try:
            for payload in payloads:
                worker._handle_message(
                    _FakeMessage(topic="decision.lifecycle", payload=payload),
                    consumer,
                )
        finally:
            logger.removeHandler(capture)
        elapsed_seconds = round(time.perf_counter() - started, 3)

        invalid_dlq_rows = []
        for row in bus.published:
            if row["topic"] != "decision.lifecycle.dlq":
                continue
            try:
                payload = json.loads(row["value"])
            except Exception:
                continue
            if payload.get("event_type") == "InvalidEvent":
                invalid_dlq_rows.append(payload)

        deduped_lines = [line for line in capture.lines if '"deduped": true' in line]
        policy_fail_artifacts = [
            decision_id for decision_id in policy_fail_ids if (soak_outputs_root / f"{decision_id}.json").exists()
        ]
        completed_count = len(completed)
        expected_invocations = unique_valid_count

        assert len(payloads) == total_events
        assert consumer.commits == total_events
        assert len(invoked) == expected_invocations
        assert len(set(invoked)) == expected_invocations
        assert len(deduped_lines) >= duplicates_count
        assert len(invalid_dlq_rows) == invalid_count
        assert not policy_fail_artifacts
        assert completed_count >= expected_completed

        return {
            "total_events": total_events,
            "duplicates_injected": duplicates_count,
            "invalid_injected": invalid_count,
            "policy_fail_injected": policy_fail_count,
            "process_invocations": len(invoked),
            "deduped_observed": len(deduped_lines),
            "invalid_dlq_observed": len(invalid_dlq_rows),
            "policy_fail_artifacts_observed": len(policy_fail_artifacts),
            "completed_observed": completed_count,
            "completed_expected_min": expected_completed,
            "elapsed_seconds": elapsed_seconds,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker stability soak test (test-only).")
    parser.add_argument("--total-events", type=int, default=500, help="Total events to run (default: 500).")
    parser.add_argument("--seed", type=int, default=7, help="Deterministic shuffle seed.")
    args = parser.parse_args()

    summary = run_soak(total_events=int(args.total_events), seed=int(args.seed))
    print("SOAK_OK " + json.dumps(summary, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
