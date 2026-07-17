from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server
except Exception:  # pragma: no cover - optional dependency
    Counter = None  # type: ignore
    Gauge = None  # type: ignore
    Histogram = None  # type: ignore
    start_http_server = None  # type: ignore


DECISIONS_BY_STATUS = None
EVAL_SCORE = None
REPAIR_STEPS = None
CONSUMER_LAG = None
LOCK_CONTENTION = None
DLQ_PUBLISHED = None
_LOCAL_DLQ_PUBLISHED = 0


def _init_metrics() -> None:
    global DECISIONS_BY_STATUS, EVAL_SCORE, REPAIR_STEPS, CONSUMER_LAG, LOCK_CONTENTION, DLQ_PUBLISHED
    if Counter is None:
        return
    if DECISIONS_BY_STATUS is not None:
        return

    DECISIONS_BY_STATUS = Counter(
        "decisions_by_status_total", "Decisions by status", ["status"]
    )
    EVAL_SCORE = Histogram(
        "eval_score", "Evaluation score", buckets=(0.0, 0.5, 0.7, 0.85, 0.9, 0.95, 1.0)
    )
    REPAIR_STEPS = Histogram(
        "repair_steps", "Repair steps per episode", buckets=(0, 1, 2, 3, 4, 5)
    )
    CONSUMER_LAG = Gauge(
        "consumer_lag", "Kafka consumer lag (best-effort)", ["group", "topic"]
    )
    LOCK_CONTENTION = Counter(
        "lock_contention_total", "Lock contention events"
    )
    DLQ_PUBLISHED = Counter(
        "dlq_publish_total", "DLQ publish events", ["reason"]
    )


def start_metrics_server(port: int = 8000) -> None:
    if start_http_server is None:
        logger.info("prometheus_client not installed; metrics disabled")
        return
    _init_metrics()
    start_http_server(port)
    logger.info("Metrics server started on port %s", port)


def record_status(status: str) -> None:
    if Counter is None:
        return
    _init_metrics()
    DECISIONS_BY_STATUS.labels(status=status).inc()


def observe_eval_score(score: float) -> None:
    if Histogram is None:
        return
    _init_metrics()
    EVAL_SCORE.observe(float(score))


def observe_repairs(count: int) -> None:
    if Histogram is None:
        return
    _init_metrics()
    REPAIR_STEPS.observe(int(count))


def set_consumer_lag(group: str, topic: str, value: float) -> None:
    if Gauge is None:
        return
    _init_metrics()
    CONSUMER_LAG.labels(group=group, topic=topic).set(float(value))


def inc_lock_contention() -> None:
    if Counter is None:
        return
    _init_metrics()
    LOCK_CONTENTION.inc()


def inc_dlq_published(reason: str = "unknown") -> None:
    global _LOCAL_DLQ_PUBLISHED
    _LOCAL_DLQ_PUBLISHED += 1
    if Counter is None:
        return
    _init_metrics()
    DLQ_PUBLISHED.labels(reason=str(reason or "unknown")).inc()


def snapshot_local_counts() -> dict[str, int]:
    return {
        "dlq_published": int(_LOCAL_DLQ_PUBLISHED),
    }


def _reset_local_counts_for_test() -> None:
    global _LOCAL_DLQ_PUBLISHED
    _LOCAL_DLQ_PUBLISHED = 0
