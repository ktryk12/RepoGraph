from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import threading
from typing import Any, Optional

from services.aesa.bootstrap.config_guard import validate_startup_config
from services.aesa.bootstrap.orchestrator_wiring import (
    build_run_episode_use_case,
    orchestrator_runtime_mode,
)
from bus.kafka_events import KafkaEventBus
from orchestrator_worker import OrchestratorWorker
from babyai.security.runtime import SecurityRuntime
from babyai_shared.storage.decision_status_store import RedisDecisionStatusStore
from babyai_shared.storage.idempotency import IdempotencyLock
from babyai_shared.storage.redis_context_store import RedisContextStore


logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        body = b'{"ok":true,"service":"orchestrator-worker"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        _ = (format, args)
        return


def _int_env(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _maybe_redis_client(redis_url: str | None) -> Any | None:
    if not redis_url:
        return None
    try:
        import redis  # type: ignore
    except Exception:
        logger.warning("redis package not installed; running without shared Redis stores")
        return None

    try:
        client = redis.Redis.from_url(redis_url)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("Redis unavailable (%s); running without shared Redis stores", exc)
        return None


def _startup_summary(*, mode: str, environment: str, config_path: str, redis_url: str | None) -> None:
    logger.info(
        "startup_summary mode=%s environment=%s kafka_config=%s redis_enabled=%s",
        str(mode),
        str(environment),
        str(config_path),
        bool(redis_url and redis_url.strip()),
    )


def _start_health_server() -> None:
    port = int(os.getenv("ORCHESTRATOR_HEALTH_PORT", "8011"))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="orchestrator-health", daemon=True)
    thread.start()


def print_system_summary(*, mode: str, entrypoint: str = "main") -> None:
    failure_mode = str(os.getenv("FAILURE_MODE", "strict") or "strict").strip().lower() or "strict"
    writes_enabled = str(os.getenv("WRITES_ENABLED", "true") or "true").strip().lower() or "true"
    dedupe_store_type = _dedupe_store_type(mode=mode)
    logger.info(
        "telemetry=%s",
        json.dumps(
            {
                "event_type": "system_summary",
                "MODE": str(mode),
                "FAILURE_MODE": str(failure_mode),
                "WRITES_ENABLED": str(writes_enabled),
                "DEDUPE_STORE_TYPE": str(dedupe_store_type),
                "ENTRYPOINT": str(entrypoint),
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
    )


def _dedupe_store_type(*, mode: str) -> str:
    if str(mode) != "kafka":
        return "n/a"
    redis_url = str(os.getenv("REDIS_URL", "") or "").strip()
    if redis_url:
        return "redis"
    return "none"


def _run_in_process_mode() -> int:
    logger.info("MODE=in_process selected; dev in-process runner path started.")
    _ = build_run_episode_use_case(env=os.environ)
    return 0


def _run_kafka_mode() -> int:
    config_path = os.getenv("KAFKA_CONFIG_PATH", "config/kafka_config.yaml")
    environment = os.getenv("ENVIRONMENT", "development")
    redis_url = os.getenv("REDIS_URL")
    _startup_summary(
        mode="kafka",
        environment=environment,
        config_path=config_path,
        redis_url=redis_url,
    )
    _start_health_server()

    event_bus = KafkaEventBus(config_path=config_path, environment=environment)
    redis_client = _maybe_redis_client(redis_url)

    context_store = None
    status_store = None
    idempotency_lock = None

    if redis_client is not None:
        context_store = RedisContextStore(url=redis_url)
        status_store = RedisDecisionStatusStore(redis_client)
        idempotency_lock = IdempotencyLock(
            redis_client,
            ttl_seconds=int(os.getenv("LOCK_TTL_SECONDS", "900")),
        )
        _start_security_runtime(redis_client)

    worker = OrchestratorWorker(
        event_bus=event_bus,
        context_store=context_store,
        status_store=status_store,
        idempotency_lock=idempotency_lock,
        lock_renew_interval=_int_env("LOCK_RENEW_INTERVAL"),
        metrics_port=_int_env("METRICS_PORT"),
        failpoint=os.getenv("WORKER_FAILPOINT"),
        worker_id=os.getenv("WORKER_ID"),
    )
    worker.start()

    _maybe_start_voice_agent(event_bus)

    return 0


def _maybe_start_voice_agent(bus: Any) -> None:
    """Start VoiceIOAgent i baggrundstråd hvis VOICE_ENABLED=true."""
    if os.environ.get("VOICE_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    try:
        import asyncio
        from babyai.voice.voice_service_client import VoiceServiceClient
        from agents.voice_io_agent import VoiceIOAgent
        from agents.voice_input_router import VoiceInputRouter

        voice_client = VoiceServiceClient()
        agent = VoiceIOAgent(bus, voice_client)
        router = VoiceInputRouter(bus)

        def _run_voice() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            router.run_in_background()
            try:
                loop.run_until_complete(agent.run())
            except Exception as exc:
                logger.warning("voice_agent_stopped error=%s", exc)
            finally:
                loop.close()

        t = threading.Thread(target=_run_voice, name="voice-io-agent", daemon=True)
        t.start()
        logger.info("voice_agent_started VOICE_ENABLED=true")
    except Exception as exc:
        logger.warning("voice_agent_startup_failed error=%s — continuing without voice", exc)


def _start_security_runtime(redis_client: Any) -> SecurityRuntime | None:
    if redis_client is None:
        return None
    sqlite_path = _resolve_security_sqlite_path()
    raw_paths = str(os.getenv("SKILLS_BASE_PATHS", "")).strip()
    skill_paths = None
    if raw_paths:
        skill_paths = [Path(part.strip()) for part in raw_paths.split(",") if part.strip()]
    try:
        runtime = SecurityRuntime(
            redis_client=redis_client,
            sqlite_path=sqlite_path,
            skill_paths=skill_paths,
        )
        runtime.register_local_skills()
        runtime.start_background_tasks()
        logger.info("security_startup background_tasks_started=true sqlite_path=%s", sqlite_path)
        return runtime
    except Exception as exc:
        logger.warning("security_startup_failed error=%s", exc)
        return None


def _resolve_security_sqlite_path() -> str:
    explicit = str(os.getenv("SQLITE_PATH", "") or "").strip()
    if explicit:
        return explicit
    artifact_root = str(os.getenv("ARTIFACT_DIR", "") or "").strip()
    if artifact_root:
        return str(Path(artifact_root) / "security_events.sqlite")
    container_root = Path("/app/artifacts")
    if container_root.exists():
        return str(container_root / "security_events.sqlite")
    return "state/security_events.sqlite"


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    try:
        validate_startup_config(entrypoint="main")
        mode = orchestrator_runtime_mode(env=os.environ)
        print_system_summary(mode=mode, entrypoint="main")
        if mode == "in_process":
            return _run_in_process_mode()
        if mode == "kafka":
            return _run_kafka_mode()
        raise RuntimeError(f"unsupported_runtime_mode:{mode}")
    except Exception:
        logger.exception("startup_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
