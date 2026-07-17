from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from application.use_cases import ValidateAndEnqueueDecisionRequest
from application.ports import PolicyValidatorResult
from infrastructure.dedupe_store import RedisDedupeStore
from infrastructure.kafka_consumer import KafkaDecisionRequestedConsumer
from infrastructure.kafka_publishers import KafkaDlqPublisher, KafkaLifecyclePublisher

try:
    from confluent_kafka import Consumer, KafkaError, Producer
except Exception:  # pragma: no cover - optional dependency
    Consumer = None  # type: ignore[assignment]
    KafkaError = None  # type: ignore[assignment]
    Producer = None  # type: ignore[assignment]


def _is_in_container() -> bool:
    return Path("/.dockerenv").exists()


def _bootstrap_server() -> str:
    configured = str(os.getenv("KAFKA_BOOTSTRAP_SERVERS", "")).strip()
    if configured:
        return configured
    legacy = str(os.getenv("KAFKA_BROKERS", "")).strip()
    if legacy:
        return legacy
    if _is_in_container():
        return "kafka:9092"
    return "localhost:29092"


def _skip_if_kafka_unavailable(bootstrap_server: str) -> None:
    probe = Producer(  # type: ignore[misc]
        {
            "bootstrap.servers": bootstrap_server,
            "socket.timeout.ms": 1000,
            "message.timeout.ms": 1000,
        }
    )
    try:
        probe.list_topics(timeout=2.0)
    except Exception as exc:
        pytest.skip(f"Kafka broker unavailable at {bootstrap_server}: {exc}")


def _wait_for_assignment(consumer: Consumer, *, topic: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        consumer.poll(0.25)
        if consumer.assignment():
            return
    raise AssertionError(f"Consumer assignment timed out for topic {topic}")


def _is_partition_eof(msg: Any) -> bool:
    if KafkaError is None or msg is None or not msg.error():
        return False
    return msg.error().code() == KafkaError._PARTITION_EOF


class _AllowPolicyValidator:
    def validate_request(self, request: Any) -> PolicyValidatorResult:
        _ = request
        return PolicyValidatorResult(
            allowed=True,
            reason_code=None,
            message=None,
            metadata={"source": "integration-test"},
        )


@pytest.mark.integration
def test_kafka_consumer_adapter_enqueues_requested_event() -> None:
    if Producer is None or Consumer is None:
        pytest.skip("confluent-kafka is not installed")

    bootstrap_server = _bootstrap_server()
    _skip_if_kafka_unavailable(bootstrap_server)

    run_id = f"req-gate-it-{time.time_ns()}"
    requested_topic = f"decision.requested.{run_id}"
    lifecycle_topic = f"decision.lifecycle.{run_id}"
    dlq_topic = f"decision.requested.dlq.{run_id}"
    decision_id = f"decision-{run_id}"
    producer = Producer({"bootstrap.servers": bootstrap_server, "message.timeout.ms": 5000})

    # Force topic creation before subscriptions to avoid assignment races.
    producer.produce(topic=lifecycle_topic, key=b"bootstrap", value=b"{}")
    assert producer.flush(10.0) == 0

    lifecycle_consumer = Consumer(
        {
            "bootstrap.servers": bootstrap_server,
            "group.id": f"request-gate-lifecycle-{run_id}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        }
    )
    lifecycle_consumer.subscribe([lifecycle_topic])
    _wait_for_assignment(lifecycle_consumer, topic=lifecycle_topic)

    lifecycle_publisher = KafkaLifecyclePublisher(
        bootstrap_servers=bootstrap_server,
        topic=lifecycle_topic,
    )
    dlq_publisher = KafkaDlqPublisher(
        bootstrap_servers=bootstrap_server,
        topic=dlq_topic,
    )
    use_case = ValidateAndEnqueueDecisionRequest(
        dedupe_store=RedisDedupeStore(redis_url=None, allow_in_memory_fallback=True),
        lifecycle_publisher=lifecycle_publisher,
        dlq_publisher=dlq_publisher,
        policy_validator=_AllowPolicyValidator(),
    )
    adapter = KafkaDecisionRequestedConsumer(
        bootstrap_servers=bootstrap_server,
        topic=requested_topic,
        group_id=f"request-gate-consumer-{run_id}",
        use_case=use_case,
        dlq_publisher=dlq_publisher,
        poll_timeout_seconds=0.5,
    )

    request_payload = {
        "decision_id": decision_id,
        "context_id": f"context-{run_id}",
        "task_ref": f"artifact:task:{run_id}",
        "truth_pack_ref": "v1",
        "truth_pack_version": 1,
        "policy_contract": {
            "policy_id": "allow-default",
            "allow_enqueue": True,
            "constraints": {"scope": "integration-test"},
        },
        "metadata": {"trace_id": f"trace-{run_id}"},
    }
    producer.produce(
        topic=requested_topic,
        key=decision_id.encode("utf-8"),
        value=json.dumps(request_payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )
    assert producer.flush(10.0) == 0

    try:
        processed = False
        process_deadline = time.monotonic() + 20.0
        while time.monotonic() < process_deadline:
            if adapter.run_once() > 0:
                processed = True
                break
        assert processed, "request-gate consumer did not process any message"

        found = None
        consume_deadline = time.monotonic() + 20.0
        while time.monotonic() < consume_deadline and found is None:
            msg = lifecycle_consumer.poll(0.5)
            if msg is None:
                continue
            if msg.error():
                if _is_partition_eof(msg):
                    continue
                raise AssertionError(f"Kafka consume error: {msg.error()}")
            raw = msg.value()
            if raw is None:
                continue
            decoded = json.loads(raw.decode("utf-8"))
            if decoded.get("decision_id") != decision_id:
                continue
            found = decoded

        assert isinstance(found, dict), "missing lifecycle requested event"
        assert found.get("status") == "requested"
        assert isinstance(found.get("truth_pack_version"), int)
        metadata = found.get("metadata")
        assert isinstance(metadata, dict)
        assert str(metadata.get("request_fingerprint", "")).strip()
        assert str(metadata.get("event_fingerprint", "")).strip()
        assert str(metadata.get("policy_fingerprint", "")).strip()
        assert isinstance(metadata.get("policy_explanation"), dict)
        effective_policy = metadata.get("effective_policy")
        assert isinstance(effective_policy, dict)
        assert str((effective_policy.get("write_scope") or {}).get("type") or "").strip()
    finally:
        adapter.close()
        lifecycle_publisher.close()
        dlq_publisher.close()
        lifecycle_consumer.close()
