from __future__ import annotations

import logging
import os
import threading
from typing import Any

from babyai_shared.bus import kafka_retry
from babyai_shared.core.logging_milestones import log_milestone
from planner.application import PlannerService
from planner.infrastructure import (
    FileTaskSpecStore,
    KafkaDecisionRequestedPublisher,
    KafkaDlqPublisher,
    KafkaPlannerConsumer,
)

try:
    from fastapi import FastAPI, HTTPException
    import uvicorn
except Exception:  # pragma: no cover - optional dependency
    FastAPI = None  # type: ignore[assignment]
    HTTPException = RuntimeError  # type: ignore[assignment]
    uvicorn = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
_SERVICE_NAME = "planner"


class PlannerRuntime:
    def __init__(self) -> None:
        bootstrap_servers = str(
            os.getenv("PLANNER_BOOTSTRAP_SERVERS")
            or os.getenv("KAFKA_BOOTSTRAP_SERVERS")
            or os.getenv("KAFKA_BROKERS")
            or "kafka:9092"
        )
        intent_topic = str(os.getenv("PLANNER_INTENT_TOPIC", "decision.intent"))
        ready_topic = str(os.getenv("PLANNER_READY_TOPIC", "decision.truthpack.ready"))
        requested_topic = str(os.getenv("PLANNER_REQUESTED_TOPIC", "decision.requested"))
        dlq_topic = str(os.getenv("PLANNER_DLQ_TOPIC", "decision.planner.dlq"))
        group_id = str(os.getenv("PLANNER_GROUP_ID", "planner"))
        poll_timeout = float(os.getenv("PLANNER_POLL_TIMEOUT_SECONDS", "1.0"))
        self._idle_sleep_seconds = float(os.getenv("PLANNER_IDLE_SLEEP_SECONDS", "0.2"))
        self._retry_backoff_initial_seconds = float(os.getenv("PLANNER_KAFKA_RETRY_INITIAL_BACKOFF_SECONDS", "0.5"))
        self._retry_backoff_max_seconds = float(os.getenv("PLANNER_KAFKA_RETRY_MAX_BACKOFF_SECONDS", "10.0"))
        self._bootstrap_servers = bootstrap_servers

        service = PlannerService(
            task_store=FileTaskSpecStore(artifact_root=str(os.getenv("PLANNER_ARTIFACT_ROOT", "artifacts"))),
            decision_requested_publisher=KafkaDecisionRequestedPublisher(
                bootstrap_servers=bootstrap_servers,
                topic=requested_topic,
            ),
            dlq_publisher=KafkaDlqPublisher(bootstrap_servers=bootstrap_servers, topic=dlq_topic),
        )
        self._consumer = KafkaPlannerConsumer(
            bootstrap_servers=bootstrap_servers,
            intent_topic=intent_topic,
            ready_topic=ready_topic,
            group_id=group_id,
            poll_timeout_seconds=poll_timeout,
            service=service,
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._consumer_loop_ready = False

    def start(self) -> None:
        if self._started:
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="planner-consumer")
        self._thread.start()
        self._started = True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._consumer.close()
        self._started = False
        self._consumer_loop_ready = False

    def health(self) -> dict[str, Any]:
        thread_alive = bool(self._thread and self._thread.is_alive())
        return {
            "ok": bool(
                self._started
                and thread_alive
                and self._consumer_loop_ready
                and not self._stop_event.is_set()
            ),
            "service": "planner",
            "consumer_thread_alive": thread_alive,
            "consumer_loop_ready": bool(self._consumer_loop_ready),
        }

    def _run_loop(self) -> None:
        backoff_seconds = max(0.05, float(self._retry_backoff_initial_seconds))
        max_backoff_seconds = max(backoff_seconds, float(self._retry_backoff_max_seconds))
        while not self._stop_event.is_set():
            try:
                processed = self._consumer.run_once()
                self._consumer_loop_ready = True
                backoff_seconds = max(0.05, float(self._retry_backoff_initial_seconds))
                if processed == 0:
                    self._stop_event.wait(max(0.01, float(self._idle_sleep_seconds)))
            except Exception as exc:
                if kafka_retry.is_kafka_exception(exc) and kafka_retry.is_retryable_kafka_exception(exc):
                    code = kafka_retry.kafka_exception_code(exc)
                    log_milestone(
                        logger,
                        "kafka_error",
                        service_name=_SERVICE_NAME,
                        component="runtime.consumer_loop",
                        decision_id="",
                        context_id="",
                        episode_id="",
                        event_type="",
                        topic="",
                        event_id="",
                        trace_id="",
                        code=str(code),
                        error=str(exc),
                        bootstrap=str(self._bootstrap_servers),
                    )
                    logger.warning(
                        "planner waiting for Kafka topics; retrying code=%s backoff_seconds=%.2f error=%s",
                        code,
                        backoff_seconds,
                        exc,
                    )
                    self._consumer_loop_ready = False
                    if self._stop_event.wait(backoff_seconds):
                        break
                    backoff_seconds = min(max_backoff_seconds, backoff_seconds * 2.0)
                    continue
                logger.exception("planner_loop_failed")
                self._consumer_loop_ready = False
                break


def create_app() -> Any:
    if FastAPI is None:
        raise RuntimeError("FastAPI is required for planner service. Install: pip install fastapi uvicorn")
    runtime = PlannerRuntime()
    app = FastAPI(title="planner", version="1.0.0")
    app.state.runtime = runtime

    @app.on_event("startup")
    def _on_startup() -> None:
        runtime.start()

    @app.on_event("shutdown")
    def _on_shutdown() -> None:
        runtime.stop()

    @app.get("/health")
    def health() -> dict[str, Any]:
        status = runtime.health()
        if not bool(status.get("ok")):
            raise HTTPException(status_code=503, detail=status)
        return status

    return app


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    if uvicorn is None:
        raise RuntimeError("uvicorn is required for planner service. Install: pip install uvicorn")
    app = create_app()
    port = int(os.getenv("PLANNER_PORT", "8099"))
    uvicorn.run(app, host="0.0.0.0", port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())