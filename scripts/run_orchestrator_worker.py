from __future__ import annotations

import logging
import os
from typing import Any, Optional

from aesa.bootstrap.orchestrator_wiring import (
    build_run_episode_use_case,
    orchestrator_runtime_mode,
)
from aesa.bootstrap.config_guard import validate_startup_config
from bus.kafka_events import KafkaEventBus
from bus.orchestrator_worker import OrchestratorWorker
from babyai_shared.storage.decision_status_store import RedisDecisionStatusStore
from babyai_shared.storage.idempotency import IdempotencyLock
from babyai_shared.storage.redis_context_store import RedisContextStore


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
        logging.warning("redis package not installed; running without shared Redis stores")
        return None

    try:
        client = redis.Redis.from_url(redis_url)
        client.ping()
        return client
    except Exception as exc:
        logging.warning("Redis unavailable (%s); running without shared Redis stores", exc)
        return None


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logging.warning(
        "DEPRECATED entrypoint: use `python main.py` (Golden Runner) instead of scripts/run_orchestrator_worker.py"
    )
    validate_startup_config(entrypoint="orchestrator_worker")

    mode = orchestrator_runtime_mode(env=os.environ)
    if mode == "in_process":
        logging.info(
            "MODE=%s selected; dev path enabled (in-process wiring), Kafka worker not started.",
            "in_process",
        )
        _ = build_run_episode_use_case(env=os.environ)
        return 0
    if mode != "kafka":
        raise RuntimeError(f"unsupported_runtime_mode:{mode}")

    config_path = os.getenv("KAFKA_CONFIG_PATH", "config/kafka_config.yaml")
    environment = os.getenv("ENVIRONMENT", "development")
    redis_url = os.getenv("REDIS_URL")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
