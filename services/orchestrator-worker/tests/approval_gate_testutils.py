from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import Any

import yaml

import pytest

from bus.kafka_events import KafkaEventBus

try:
    from confluent_kafka import Consumer, KafkaError, Producer
except Exception:  # pragma: no cover - optional dependency
    Consumer = None  # type: ignore[assignment]
    KafkaError = None  # type: ignore[assignment]
    Producer = None  # type: ignore[assignment]


def bootstrap_server() -> str:
    configured = str(os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")).strip()
    if configured:
        return configured
    return "localhost:29092"


def require_kafka_or_skip() -> None:
    if Producer is None or Consumer is None:
        pytest.skip("confluent-kafka is not installed")
    probe = Producer(  # type: ignore[misc]
        {
            "bootstrap.servers": bootstrap_server(),
            "socket.timeout.ms": 1000,
            "message.timeout.ms": 1000,
        }
    )
    try:
        probe.list_topics(timeout=2.0)
    except Exception as exc:
        pytest.skip(f"Kafka broker unavailable at {bootstrap_server()}: {exc}")


def create_event_bus_with_topics(*, run_id: str) -> tuple[KafkaEventBus, str]:
    topics = {
        "decision_requested": f"decision.requested.{run_id}",
        "decision_lifecycle": f"decision.lifecycle.{run_id}",
        "decision_approval": f"decision.approval.{run_id}",
        "decision_lifecycle_dlq": f"decision.lifecycle.dlq.{run_id}",
        "eval_results": f"eval.results.{run_id}",
        "tool_events": f"tool.events.{run_id}",
        "artifact_events": f"artifact.events.{run_id}",
    }
    config = {
        "brokers": bootstrap_server(),
        "client_id": f"approval-gate-tests-{run_id}",
        "allow_auto_create_topics": True,
        "default_partitions": 1,
        "default_replication": 1,
        "topics": topics,
        "producer": {"acks": "all", "compression_type": "snappy", "linger_ms": 0},
        "consumer": {"enable_auto_commit": False, "session_timeout_ms": 10000, "retry_max_attempts": 1, "retry_backoff_seconds": 0},
        "dedupe": {"running_ttl_seconds": 30, "final_ttl_seconds": 120},
    }
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    with handle:
        yaml.safe_dump(config, handle)
    return KafkaEventBus(config_path=handle.name, environment="development"), handle.name


def wait_for_assignment(consumer: Any, *, topic: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        consumer.poll(0.25)
        if consumer.assignment():
            return
    raise AssertionError(f"Consumer assignment timed out for topic {topic}")


def is_partition_eof(msg: Any) -> bool:
    if KafkaError is None or msg is None or not msg.error():
        return False
    return msg.error().code() == KafkaError._PARTITION_EOF


def seed_topics(*, topic_names: list[str]) -> None:
    producer = Producer({"bootstrap.servers": bootstrap_server(), "message.timeout.ms": 5000})  # type: ignore[misc]
    for topic in topic_names:
        producer.produce(topic=topic, key=b"bootstrap", value=b"{}")
    remaining = producer.flush(10.0)
    if remaining > 0:
        raise RuntimeError(f"kafka seed topics flush timeout remaining={remaining}")


def cleanup_config(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        return

