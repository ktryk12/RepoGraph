#!/usr/bin/env python3
"""
Strict smoke test: requires a successful terminal episode.

Pass criteria:
- no waiting_for_approval state
- lifecycle includes started
- terminal lifecycle state is completed
- eval.results exists and passed == true
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import string
import sys
import time
from typing import Any

from confluent_kafka import Consumer, Producer

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from babyai_shared.storage.artifact_store import FileArtifactStore


KAFKA_BROKER = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
DOMAIN = "live-discovery-e2e"
REQUESTED_TOPIC = "decision.requested"
LIFECYCLE_TOPIC = "decision.lifecycle"
EVAL_TOPIC = "eval.results"
TIMEOUT_SECONDS = 240.0


@dataclass
class StrictState:
    decision_id: str
    lifecycle_seen: list[str]
    saw_started: bool
    saw_waiting_for_approval: bool
    saw_terminal: bool
    terminal_status: str
    saw_eval: bool
    eval_passed: bool | None
    eval_failure_reasons: list[str]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _random_suffix(length: int = 8) -> str:
    alphabet = string.ascii_lowercase
    return "".join(random.choice(alphabet) for _ in range(length))


def _build_task_ref(*, decision_id: str) -> str:
    os.environ.setdefault("AESA_ALLOW_UNKNOWN_ENTRYPOINT_WRITES", "true")
    task = {
        "schema_version": 1,
        "template": "governance_hello_world.v1",
        "task_id": f"task-{decision_id}",
        "title": "Strict smoke governance run",
        "prompt": 'Return ONLY valid JSON: {"hello":"world"}.',
        "context_id": DOMAIN,
        "domain_name": DOMAIN,
        "inputs": {
            "objective": "strict smoke",
            "policy_preset": "dev",
            "mode": "governance_hello_world",
        },
        "acceptance": [
            "Episode reaches completed",
            "eval.results passed is true",
        ],
        "constraints": {
            "repo_writes": "forbidden",
            "external_network": "forbidden",
        },
    }
    raw = json.dumps(task, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return FileArtifactStore(root="artifacts").put(
        raw,
        context_id=DOMAIN,
        name=f"task:smoke:strict:{decision_id}",
        metadata={"type": "task"},
    ).ref


def _build_requested_event(*, decision_id: str, task_ref: str) -> dict[str, Any]:
    return {
        "decision_id": decision_id,
        "context_id": DOMAIN,
        "task_ref": task_ref,
        "truth_pack_ref": "layered_default",
        "truth_pack_version": 1,
        "policy_contract": {
            "policy_id": "dev",
            "allow_enqueue": True,
            "constraints": {
                "visibility": "internal",
                "safety_mode": "balanced",
                "write_scope": {"type": "none"},
                "write_scope_type": "none",
            },
        },
        "metadata": {
            "trace_id": f"trace-{decision_id}",
            "domain_name": DOMAIN,
            "policy_preset": "dev",
            "model_profile": "general",
            "generation_max_tokens": 32,
            "generation_temperature": 0.0,
            "user_prompt": 'Strict governance smoke: return only {"hello":"world"}.',
            "required_write_scope": "none",
        },
        "timestamp": _now_iso(),
    }


def _decode_json(msg_value: bytes) -> dict[str, Any] | None:
    try:
        obj = json.loads(msg_value.decode("utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _wait_for_assignment(consumer: Consumer, *, timeout_seconds: float = 10.0) -> None:
    deadline = time.time() + max(0.0, float(timeout_seconds))
    while time.time() < deadline:
        if consumer.assignment():
            return
        consumer.poll(0.2)


def main() -> int:
    decision_id = f"smoke-ok-{_random_suffix()}"
    task_ref = _build_task_ref(decision_id=decision_id)
    requested = _build_requested_event(decision_id=decision_id, task_ref=task_ref)

    producer = Producer({"bootstrap.servers": KAFKA_BROKER, "client.id": "smoke-test-strict-producer"})
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BROKER,
            "group.id": f"smoke-strict-{decision_id}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        }
    )
    consumer.subscribe([LIFECYCLE_TOPIC, EVAL_TOPIC])
    _wait_for_assignment(consumer)

    state = StrictState(
        decision_id=decision_id,
        lifecycle_seen=[],
        saw_started=False,
        saw_waiting_for_approval=False,
        saw_terminal=False,
        terminal_status="",
        saw_eval=False,
        eval_passed=None,
        eval_failure_reasons=[],
    )

    producer.produce(
        REQUESTED_TOPIC,
        key=decision_id.encode("utf-8"),
        value=json.dumps(requested, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8"),
    )
    undelivered = producer.flush(10.0)
    if undelivered > 0:
        print(f"Strict smoke FAILED: kafka publish timeout (undelivered={undelivered}) broker={KAFKA_BROKER}")
        return 1
    print(f"[{decision_id}] requested")

    deadline = time.time() + TIMEOUT_SECONDS
    try:
        while time.time() < deadline:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                continue

            payload = _decode_json(msg.value())
            if payload is None:
                continue
            if str(payload.get("decision_id") or "") != decision_id:
                continue

            topic = str(msg.topic() or "")
            if topic == LIFECYCLE_TOPIC:
                status = str(payload.get("status") or "").strip().lower()
                if not status:
                    continue
                if not state.lifecycle_seen or state.lifecycle_seen[-1] != status:
                    state.lifecycle_seen.append(status)
                    print(f"[{decision_id}] {status}")
                if status == "waiting_for_approval":
                    state.saw_waiting_for_approval = True
                if status == "started":
                    state.saw_started = True
                if status in {"completed", "failed"}:
                    state.saw_terminal = True
                    state.terminal_status = status
            elif topic == EVAL_TOPIC:
                state.saw_eval = True
                passed_raw = payload.get("passed")
                state.eval_passed = bool(passed_raw) if isinstance(passed_raw, bool) else None
                reasons = payload.get("failure_reasons")
                if isinstance(reasons, list):
                    state.eval_failure_reasons = [str(item) for item in reasons]
                print(f"[{decision_id}] eval passed={state.eval_passed}")

            if state.saw_started and state.saw_terminal and state.saw_eval:
                break
    finally:
        consumer.close()

    if state.saw_waiting_for_approval:
        print("Strict smoke FAILED: encountered waiting_for_approval (AUTO_APPROVE not effective)")
        return 1
    if not state.saw_started:
        print("Strict smoke FAILED: never reached started")
        return 1
    if not state.saw_terminal:
        print("Strict smoke FAILED: no terminal lifecycle state within timeout")
        return 1
    if not state.saw_eval:
        print("Strict smoke FAILED: no eval.results event observed")
        return 1
    if state.terminal_status != "completed":
        reason = ", ".join(state.eval_failure_reasons) if state.eval_failure_reasons else "unknown"
        print(f"Strict smoke FAILED: terminal={state.terminal_status} reason={reason}")
        return 1
    if state.eval_passed is not True:
        reason = ", ".join(state.eval_failure_reasons) if state.eval_failure_reasons else "unknown"
        print(f"Strict smoke FAILED: eval passed={state.eval_passed} reason={reason}")
        return 1

    print("Strict smoke PASSED (terminal=completed, eval.passed=true)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
