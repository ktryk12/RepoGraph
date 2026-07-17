#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p artifacts logs outbox

cleanup() {
  docker compose down -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

wait_for_running() {
  local service="$1"
  local timeout="${2:-180}"
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if docker compose ps --status running --services | grep -Fxq "$service"; then
      return 0
    fi
    sleep 2
  done
  echo "Smoke failed: service '$service' not running after ${timeout}s"
  return 1
}

wait_for_http() {
  local url="$1"
  local timeout="${2:-180}"
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Smoke failed: health endpoint not ready: $url"
  return 1
}

wait_for_worker_consumer() {
  local timeout="${1:-240}"
  local deadline=$((SECONDS + timeout))
  while (( SECONDS < deadline )); do
    if docker compose logs --no-color orchestrator-worker 2>/dev/null | grep -q "Consumer orchestrator-workers started"; then
      return 0
    fi
    sleep 3
  done
  echo "Smoke failed: orchestrator-worker did not start Kafka consumer"
  return 1
}

docker compose down -v >/dev/null 2>&1 || true
docker compose up -d redis kafka context-plane tool-runtime policy-validator artifact-writer orchestrator-worker

wait_for_running "redis" 120
wait_for_running "kafka" 180
wait_for_running "context-plane" 240
wait_for_running "tool-runtime" 240
wait_for_running "policy-validator" 240
wait_for_running "artifact-writer" 240
wait_for_running "orchestrator-worker" 240

wait_for_http "http://localhost:8092/health" 240
wait_for_http "http://localhost:8093/health" 240
wait_for_http "http://localhost:8095/health" 240
wait_for_http "http://localhost:8096/health" 240
wait_for_worker_consumer 300

SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-240}"
docker compose exec -T -e SMOKE_TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS}" orchestrator-worker python - <<'PY'
import json
import os
from pathlib import Path
import threading
import time
import uuid

from bus.event_schemas import ArtifactEvent, DecisionEvent, DecisionStatus, SCHEMA_VERSION, now_iso
from bus.kafka_events import KafkaEventBus
from storage.artifact_store import FileArtifactStore

timeout = int(os.getenv("SMOKE_TIMEOUT_SECONDS", "240"))
run_id = uuid.uuid4().hex[:10]

task_paths = [
    Path("eval/tasks/EVAL-001.json"),
    Path("eval/tasks/EVAL-002.json"),
    Path("eval/tasks/EVAL-003.json"),
]

store = FileArtifactStore(root="artifacts")
event_bus = KafkaEventBus(
    config_path=os.getenv("KAFKA_CONFIG_PATH", "config/kafka_config.yaml"),
    environment=os.getenv("ENVIRONMENT", "development"),
)

requested_events: list[DecisionEvent] = []
for idx, task_path in enumerate(task_paths, start=1):
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task_id = str(task.get("task_id") or f"SMOKE-{idx}")
    task_ref = store.put(
        json.dumps(task, ensure_ascii=True, sort_keys=True).encode("utf-8"),
        context_id=f"smoke-{run_id}",
        name=f"task:{task_id}",
        metadata={"type": "task", "smoke_run_id": run_id},
    ).ref
    decision_id = f"smoke-{run_id}-{idx}"
    requested_events.append(
        DecisionEvent(
            schema_version=SCHEMA_VERSION,
            decision_id=decision_id,
            context_id=f"smoke-ctx-{run_id}-{idx}",
            status=DecisionStatus.REQUESTED,
            timestamp=now_iso(),
            task_ref=task_ref,
            truth_pack_ref="v1",
            truth_pack_version="v1",
            metadata={"trace_id": f"smoke-trace-{run_id}-{idx}"},
        )
    )

pending = {event.decision_id: None for event in requested_events}
context_to_decision = {event.context_id: event.decision_id for event in requested_events}
pending_lock = threading.Lock()
done = threading.Event()


def handler(msg, consumer) -> None:
    topic = msg.topic()
    payload = msg.value().decode("utf-8", errors="replace")
    with pending_lock:
        if topic == "decision.lifecycle":
            try:
                event = DecisionEvent.from_json(payload)
            except Exception:
                consumer.commit(message=msg, asynchronous=False)
                return
            if event.decision_id in pending and event.status in {DecisionStatus.COMPLETED, DecisionStatus.FAILED}:
                if pending[event.decision_id] is None:
                    pending[event.decision_id] = event.status.value
                    print(
                        f"smoke terminal event: decision_id={event.decision_id} status={event.status.value}",
                        flush=True,
                    )
        elif topic == "artifact.events":
            try:
                artifact_event = ArtifactEvent.from_json(payload)
            except Exception:
                consumer.commit(message=msg, asynchronous=False)
                return
            decision_id = context_to_decision.get(str(artifact_event.context_id))
            if decision_id and pending.get(decision_id) != "completed":
                pending[decision_id] = "artifact_seen"
                print(
                    f"smoke artifact event: decision_id={decision_id} artifact_ref={artifact_event.artifact_ref}",
                    flush=True,
                )
        if all(value in {"completed", "artifact_seen"} for value in pending.values()):
            done.set()
    consumer.commit(message=msg, asynchronous=False)


consumer = event_bus.create_consumer(
    topics=["decision.lifecycle", "artifact.events"],
    group_id=f"smoke-{run_id}",
    handler=handler,
)
consumer_thread = threading.Thread(target=consumer.start, daemon=True)
consumer_thread.start()

time.sleep(1.0)
for event in requested_events:
    event_bus.publish(
        topic="decision.lifecycle",
        key=event.decision_id,
        value=event.to_json(),
    )
event_bus.flush()
print(f"smoke published events={len(requested_events)} run_id={run_id}", flush=True)

success = done.wait(timeout=timeout)
consumer.stop()
consumer_thread.join(timeout=10)
event_bus.flush()

with pending_lock:
    final = dict(pending)
missing = sorted(
    [decision_id for decision_id, status in final.items() if status not in {"completed", "artifact_seen"}]
)
if (not success) or missing:
    raise SystemExit(
        "Smoke failed: missing EpisodeCompleted/artifact evidence for "
        f"decision_ids={','.join(missing)} statuses={final}"
    )

print(f"Smoke OK: completed run_id={run_id} statuses={final}", flush=True)
PY
