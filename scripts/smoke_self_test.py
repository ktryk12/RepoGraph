from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


DEFAULT_SERVICES = [
    "redis",
    "kafka",
    "request-gate",
    "context-plane",
    "tool-runtime",
    "policy-validator",
    "artifact-writer",
    "orchestrator-worker",
]
DEFAULT_HEALTH_ENDPOINTS = [
    "http://localhost:8097/health",
    "http://localhost:8092/health",
    "http://localhost:8093/health",
    "http://localhost:8095/health",
    "http://localhost:8096/health",
]
DEFAULT_DIAGNOSTIC_SERVICES = [
    "kafka",
    "redis",
    "orchestrator-worker",
    "request-gate",
    "context-plane",
    "tool-runtime",
    "policy-validator",
    "artifact-writer",
    "telemetry-consumer",
]
DEFAULT_WORKER_READY_TOKEN = "Consumer orchestrator-workers started"
SUMMARY_PREFIX = "SELFTEST_SUMMARY="

IN_CONTAINER_RUNNER = r"""
import json
import os
from pathlib import Path
import threading
import time
import uuid

from bus.event_schemas import ArtifactEvent, DecisionEvent, DecisionStatus
from bus.kafka_events import KafkaEventBus
from storage.artifact_store import FileArtifactStore

timeout = int(os.getenv("SELFTEST_TIMEOUT_SECONDS", "240"))
task_path = Path(os.environ["SELFTEST_TASK_PATH"])
run_id = str(os.getenv("SELFTEST_RUN_ID", "")).strip() or uuid.uuid4().hex[:10]
decision_id = f"selftest-{run_id}"
context_id = f"selftest-ctx-{run_id}"

task = json.loads(task_path.read_text(encoding="utf-8"))

store = FileArtifactStore(root="artifacts")
event_bus = KafkaEventBus(
    config_path=os.getenv("KAFKA_CONFIG_PATH", "config/kafka_config.yaml"),
    environment=os.getenv("ENVIRONMENT", "development"),
)

task_ref = store.put(
    json.dumps(task, ensure_ascii=True, sort_keys=True).encode("utf-8"),
    context_id=context_id,
    name="task:selftest",
    metadata={"type": "task", "self_test_run_id": run_id},
).ref

done = threading.Event()
state = {
    "started_seen": False,
    "terminal_status": None,
    "artifact_ref": None,
    "dlq_seen": False,
}
lock = threading.Lock()


def handler(msg, consumer):
    topic = msg.topic()
    payload = msg.value().decode("utf-8", errors="replace")
    with lock:
        if topic == "decision.lifecycle":
            try:
                event = DecisionEvent.from_json(payload)
            except Exception:
                consumer.commit(message=msg, asynchronous=False)
                return
            if event.decision_id == decision_id:
                if event.status in {DecisionStatus.STARTED, DecisionStatus.GENERATING, DecisionStatus.EVALUATING, DecisionStatus.EVALUATED}:
                    state["started_seen"] = True
                if event.status in {DecisionStatus.COMPLETED, DecisionStatus.FAILED}:
                    state["terminal_status"] = event.status.value
        elif topic == "artifact.events":
            try:
                event = ArtifactEvent.from_json(payload)
            except Exception:
                consumer.commit(message=msg, asynchronous=False)
                return
            if str(event.context_id) == context_id:
                state["artifact_ref"] = event.artifact_ref
        elif topic == "decision.lifecycle.dlq":
            try:
                decoded = json.loads(payload)
            except Exception:
                decoded = {}
            if isinstance(decoded, dict) and str(decoded.get("decision_id") or "") == decision_id:
                state["dlq_seen"] = True
        if state["started_seen"] and (state["terminal_status"] or state["dlq_seen"]):
            done.set()
    consumer.commit(message=msg, asynchronous=False)


consumer = event_bus.create_consumer(
    topics=["decision.lifecycle", "artifact.events", "decision.lifecycle.dlq"],
    group_id=f"selftest-{run_id}",
    handler=handler,
)
consumer_thread = threading.Thread(target=consumer.start, daemon=True)
consumer_thread.start()

request_payload = {
    "decision_id": decision_id,
    "context_id": context_id,
    "task_ref": task_ref,
    "truth_pack_ref": "v1",
    "truth_pack_version": 1,
    "policy_contract": {
        "policy_id": "dev",
        "allow_enqueue": True,
        "constraints": {"write_scope": {"type": "none"}},
    },
    "metadata": {"trace_id": f"selftest-trace-{run_id}", "policy_preset": "dev"},
}
event_bus.publish(
    topic="decision.requested",
    key=decision_id,
    value=json.dumps(request_payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
)
event_bus.flush()

ok = done.wait(timeout=timeout)
consumer.stop()
consumer_thread.join(timeout=10)
event_bus.flush()

if (not ok) or (not state["started_seen"]) or (not (state["terminal_status"] or state["dlq_seen"])):
    raise SystemExit(
        "self_test_failed "
        f"decision_id={decision_id} "
        f"started_seen={state['started_seen']} "
        f"terminal_status={state['terminal_status']} "
        f"artifact_ref={state['artifact_ref']} "
        f"dlq_seen={state['dlq_seen']}"
    )

summary = {
    "decision_id": decision_id,
    "context_id": context_id,
    "started_seen": str(state["started_seen"]).lower(),
    "terminal_status": state["terminal_status"],
    "artifact_ref": state["artifact_ref"],
    "dlq_seen": str(state["dlq_seen"]).lower(),
    "task_ref": task_ref,
}
print("SELFTEST_SUMMARY=" + json.dumps(summary, ensure_ascii=True, sort_keys=True), flush=True)
"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
    )


def _docker_compose_cmd(compose_file: str, *parts: str) -> list[str]:
    return ["docker", "compose", "-f", compose_file, *parts]


def _ensure_docker_available(root: Path, compose_file: str) -> None:
    docker = _run(["docker", "--version"], cwd=root)
    if docker.returncode != 0:
        raise RuntimeError(f"docker unavailable: {docker.stderr.strip() or docker.stdout.strip()}")
    compose = _run(_docker_compose_cmd(compose_file, "version"), cwd=root)
    if compose.returncode != 0:
        raise RuntimeError(
            f"docker compose unavailable: {compose.stderr.strip() or compose.stdout.strip()}"
        )


def _wait_services_running(root: Path, compose_file: str, services: list[str], timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        ps = _run(
            _docker_compose_cmd(compose_file, "ps", "--status", "running", "--services"),
            cwd=root,
        )
        running = {line.strip() for line in ps.stdout.splitlines() if line.strip()}
        if all(service in running for service in services):
            return
        time.sleep(2.0)
    raise RuntimeError(f"services_not_running_after_timeout services={services}")


def _wait_http(url: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                if response.getcode() == 200:
                    return
        except Exception:
            pass
        time.sleep(2.0)
    raise RuntimeError(f"health_endpoint_not_ready url={url}")


def _wait_worker_log(
    root: Path,
    compose_file: str,
    *,
    service: str,
    token: str,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        logs = _run(_docker_compose_cmd(compose_file, "logs", "--no-color", service), cwd=root)
        if token in logs.stdout:
            return
        time.sleep(3.0)
    raise RuntimeError(f"worker_ready_token_missing service={service} token={token}")


def _collect_logs(root: Path, compose_file: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    logs = _run(_docker_compose_cmd(compose_file, "logs", "--no-color"), cwd=root)
    out_path.write_text(logs.stdout + "\n" + logs.stderr, encoding="utf-8")


def _collect_failure_diagnostics(
    root: Path,
    compose_file: str,
    services: list[str],
    health_endpoints: list[str],
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    diagnostic_services: list[str] = []
    seen: set[str] = set()
    for candidate in [*services, *DEFAULT_DIAGNOSTIC_SERVICES]:
        service = str(candidate).strip()
        if not service or service in seen:
            continue
        seen.add(service)
        diagnostic_services.append(service)

    lines: list[str] = []
    lines.append("== docker compose ps -a ==")
    ps = _run(_docker_compose_cmd(compose_file, "ps", "-a"), cwd=root)
    lines.append(ps.stdout.strip())
    if ps.stderr.strip():
        lines.append(ps.stderr.strip())
    lines.append("")

    lines.append("== health endpoint probes ==")
    for url in health_endpoints:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                lines.append(f"{url} status={response.getcode()}")
        except Exception as exc:
            lines.append(f"{url} error={exc}")
    lines.append("")

    for service in diagnostic_services:
        lines.append(f"== logs tail {service} ==")
        svc = _run(
            _docker_compose_cmd(compose_file, "logs", "--no-color", "--tail", "200", service),
            cwd=root,
        )
        lines.append(svc.stdout.strip())
        if svc.stderr.strip():
            lines.append(svc.stderr.strip())
        lines.append("")

    lines.append("== kafka topics (best effort) ==")
    topics = _run(
        _docker_compose_cmd(
            compose_file,
            "exec",
            "-T",
            "kafka",
            "bash",
            "-lc",
            "kafka-topics.sh --bootstrap-server kafka:9092 --list",
        ),
        cwd=root,
        timeout_seconds=30,
    )
    lines.append(topics.stdout.strip())
    if topics.stderr.strip():
        lines.append(topics.stderr.strip())
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")


def _parse_summary(output: str) -> dict[str, str]:
    for line in output.splitlines():
        if line.startswith(SUMMARY_PREFIX):
            payload = line[len(SUMMARY_PREFIX) :].strip()
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return {str(k): str(v) for k, v in parsed.items()}
    raise RuntimeError("missing_selftest_summary_output")


def _write_artifact_writer_probe(
    *,
    base_url: str,
    api_key: str,
    episode_id: str,
    run_id: str,
    timeout_seconds: int,
) -> str:
    tool_result = {
        "schema_version": 1,
        "tool_id": "selftest.tool",
        "ok": True,
        "output": {},
        "run_ref": {
            "tool_id": "selftest.tool",
            "artifact_ref": "artifact:sha256:" + ("a" * 64),
            "manifest_ref": None,
        },
        "timing": {
            "started_at": "2026-01-01T00:00:00Z",
            "finished_at": "2026-01-01T00:00:00Z",
            "duration_ms": 0.0,
        },
        "warnings": [],
        "cost": {},
        "error": None,
        "backend": "selftest",
    }
    payload = {
        "episode_id": episode_id,
        "task_id": "selftest-task",
        "tool_results": [tool_result],
        "knobs": {
            "artifact_root": "artifacts",
            "trace_id": f"selftest-trace-{run_id}",
        },
    }
    body = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
    req = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/v1/artifacts/tool-evidence",
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "x-api-key": api_key,
        },
    )
    with urllib.request.urlopen(req, timeout=float(max(3, timeout_seconds))) as response:
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise RuntimeError("artifact_writer_probe_invalid_response")
    ref = str(parsed.get("tool_evidence_ref") or "").strip()
    if not ref:
        raise RuntimeError(f"artifact_writer_probe_missing_ref response={parsed}")
    return ref


def _load_config(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"config must be a JSON object: {path}")
    task_path = str(payload.get("task_path", "")).strip()
    if not task_path:
        raise ValueError("config.task_path is required")
    return payload


def run_self_test(config_path: Path, *, keep_up: bool = False) -> dict[str, str]:
    root = _repo_root()
    payload = _load_config(config_path)

    compose_file = str(payload.get("compose_file", "docker-compose.yml")).strip() or "docker-compose.yml"
    services = list(payload.get("services") or DEFAULT_SERVICES)
    services = [str(s).strip() for s in services if str(s).strip()]
    health_endpoints = list(payload.get("health_endpoints") or DEFAULT_HEALTH_ENDPOINTS)
    health_endpoints = [str(url).strip() for url in health_endpoints if str(url).strip()]
    startup_timeout = int(payload.get("startup_timeout_seconds", 240))
    run_timeout = int(payload.get("run_timeout_seconds", 240))
    worker_ready_token = str(payload.get("worker_ready_token", DEFAULT_WORKER_READY_TOKEN))
    task_path = str(payload["task_path"])
    artifact_writer_base_url = str(payload.get("artifact_writer_base_url", "http://localhost:8096")).strip()
    artifact_writer_api_key = str(payload.get("artifact_writer_api_key", "dev-artifact-writer-key")).strip()

    diagnostics_path = root / "logs" / "smoke_self_test_compose.log"
    failure_diagnostics_path = root / "logs" / "smoke_self_test_failure_diagnostics.log"
    run_id = uuid.uuid4().hex[:10]
    summary: dict[str, str] = {}
    failure: Exception | None = None

    _ensure_docker_available(root, compose_file)
    try:
        _run(_docker_compose_cmd(compose_file, "down", "-v"), cwd=root)

        up = _run(_docker_compose_cmd(compose_file, "up", "-d", *services), cwd=root, timeout_seconds=900)
        if up.returncode != 0:
            raise RuntimeError(f"docker_compose_up_failed\n{up.stdout}\n{up.stderr}")

        _wait_services_running(root, compose_file, services, startup_timeout)
        for url in health_endpoints:
            _wait_http(url, startup_timeout)
        _wait_worker_log(
            root,
            compose_file,
            service="orchestrator-worker",
            token=worker_ready_token,
            timeout_seconds=startup_timeout,
        )

        exec_env = os.environ.copy()
        exec_proc = _run(
            _docker_compose_cmd(
                compose_file,
                "exec",
                "-T",
                "-e",
                f"SELFTEST_TASK_PATH={task_path}",
                "-e",
                f"SELFTEST_TIMEOUT_SECONDS={run_timeout}",
                "-e",
                f"SELFTEST_RUN_ID={run_id}",
                "orchestrator-worker",
                "python",
                "-c",
                IN_CONTAINER_RUNNER,
            ),
            cwd=root,
            env=exec_env,
            timeout_seconds=max(120, run_timeout + 120),
        )
        if exec_proc.returncode != 0:
            raise RuntimeError(f"selftest_exec_failed\n{exec_proc.stdout}\n{exec_proc.stderr}")
        summary = _parse_summary(exec_proc.stdout)

        decision_id = summary.get("decision_id", "").strip()
        if not decision_id:
            raise RuntimeError("missing_decision_id_in_summary")

        worker_logs = _run(
            _docker_compose_cmd(compose_file, "logs", "--no-color", "orchestrator-worker"),
            cwd=root,
        )
        if decision_id not in worker_logs.stdout:
            raise RuntimeError(f"decision_id_missing_from_worker_logs decision_id={decision_id}")

        probe_ref = _write_artifact_writer_probe(
            base_url=artifact_writer_base_url,
            api_key=artifact_writer_api_key,
            episode_id=decision_id,
            run_id=run_id,
            timeout_seconds=10,
        )
        probe_path = root / "artifacts" / "tools" / decision_id / "tool_evidence.json"
        if not probe_path.exists():
            raise RuntimeError(f"artifact_writer_probe_missing_file path={probe_path}")
        summary["artifact_writer_probe_ref"] = probe_ref

        return summary
    except Exception as exc:
        failure = exc
        raise
    finally:
        _collect_logs(root, compose_file, diagnostics_path)
        if failure is not None:
            try:
                _collect_failure_diagnostics(
                    root=root,
                    compose_file=compose_file,
                    services=services,
                    health_endpoints=health_endpoints,
                    out_path=failure_diagnostics_path,
                )
                print(
                    f"self_test_failure_diagnostics={failure_diagnostics_path}",
                    file=sys.stderr,
                    flush=True,
                )
            except Exception as diag_exc:
                print(
                    f"self_test_failure_diagnostics_error={diag_exc}",
                    file=sys.stderr,
                    flush=True,
                )
        if not keep_up:
            _run(_docker_compose_cmd(compose_file, "down", "-v"), cwd=root)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BabyAI stack self-test (compose + one Kafka episode).")
    parser.add_argument(
        "--config",
        default="config/dev_run_minimal.json",
        help="Path to JSON config file for the self-test.",
    )
    parser.add_argument(
        "--keep-up",
        action="store_true",
        help="Keep compose stack running after test (for debugging).",
    )
    args = parser.parse_args()

    config_path = (_repo_root() / args.config).resolve() if not Path(args.config).is_absolute() else Path(args.config)
    summary = run_self_test(config_path, keep_up=bool(args.keep_up))
    print(json.dumps({"ok": True, "summary": summary}, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
