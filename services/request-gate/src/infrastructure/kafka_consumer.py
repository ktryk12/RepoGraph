from __future__ import annotations

import json
import time
from typing import Any, Mapping

import logging
from babyai_shared.core.logging_milestones import log_milestone
from application.ports import DlqPublisher
from application.use_cases import (
    ValidateAndEnqueueDecisionRequest,
    ValidateAndEnqueueFailure,
)
from domain.models import DecisionRequest, PolicyContract

try:
    from confluent_kafka import Consumer, KafkaError, KafkaException
except Exception:  # pragma: no cover - optional dependency
    Consumer = None  # type: ignore[assignment]
    KafkaError = None  # type: ignore[assignment]
    KafkaException = Exception  # type: ignore[assignment]


logger = logging.getLogger(__name__)
_SERVICE_NAME = "request-gate"
_COMPONENT = "infrastructure.kafka_consumer"


def _require_consumer() -> Any:
    if Consumer is None:
        raise ImportError("confluent-kafka is required for request_gate Kafka consumer")
    return Consumer


class KafkaDecisionRequestedConsumer:
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str = "decision.requested",
        group_id: str = "request-gate",
        poll_timeout_seconds: float = 1.0,
        use_case: ValidateAndEnqueueDecisionRequest,
        dlq_publisher: DlqPublisher,
    ) -> None:
        consumer_cls = _require_consumer()
        self._topic = str(topic).strip() or "decision.requested"
        self._poll_timeout_seconds = float(poll_timeout_seconds)
        self._use_case = use_case
        self._dlq_publisher = dlq_publisher
        self._consumer = consumer_cls(
            {
                "bootstrap.servers": str(bootstrap_servers),
                "group.id": str(group_id),
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        self._consumer.subscribe([self._topic])

    def run_once(self) -> int:
        msg = self._consumer.poll(timeout=self._poll_timeout_seconds)
        if msg is None:
            return 0
        if msg.error():
            if KafkaError is not None and msg.error().code() == KafkaError._PARTITION_EOF:
                return 0
            if _is_retriable_consumer_error(msg.error()):
                return 0
            raise KafkaException(msg.error())

        raw = msg.value() or b"{}"
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            self._publish_decode_error(
                reason_code="INVALID_JSON",
                message=str(exc),
                raw_payload=raw,
            )
            self._consumer.commit(message=msg, asynchronous=False)
            return 1
        if not isinstance(decoded, dict):
            self._publish_decode_error(
                reason_code="INVALID_PAYLOAD_TYPE",
                message="decision.requested payload must be a JSON object",
                raw_payload=raw,
            )
            self._consumer.commit(message=msg, asynchronous=False)
            return 1

        request = _to_decision_request(decoded)
        if request is None:
            self._publish_decode_error(
                reason_code="INVALID_REQUEST_FIELDS",
                message="required request fields are missing or invalid",
                raw_payload=raw,
            )
            self._consumer.commit(message=msg, asynchronous=False)
            return 1

        raw_key = msg.key()
        key_text = raw_key.decode("utf-8", errors="replace") if isinstance(raw_key, (bytes, bytearray)) else str(raw_key or "")
        trace_id = ""
        metadata_raw = decoded.get("metadata")
        if isinstance(metadata_raw, dict):
            trace_id = str(metadata_raw.get("trace_id") or "")
        log_milestone(
            logger,
            "request_received",
            service_name=_SERVICE_NAME,
            component=_COMPONENT,
            decision_id=str(request.decision_id),
            context_id=str(request.context_id),
            episode_id=str(request.decision_id),
            event_type="decision.requested",
            topic=str(msg.topic() or self._topic),
            event_id="",
            trace_id=trace_id,
            key=key_text,
            partition=int(msg.partition()),
            offset=int(msg.offset()),
        )
        result = self._use_case.execute(request)
        if isinstance(result, ValidateAndEnqueueFailure):
            # Failure details are already emitted to DLQ by the use case.
            _ = result
        self._consumer.commit(message=msg, asynchronous=False)
        return 1

    def run_forever(self, *, stop_event: Any, idle_sleep_seconds: float = 0.2) -> None:
        while not stop_event.is_set():
            processed = self.run_once()
            if processed == 0:
                time.sleep(float(idle_sleep_seconds))

    def close(self) -> None:
        self._consumer.close()

    def _publish_decode_error(self, *, reason_code: str, message: str, raw_payload: bytes) -> None:
        self._dlq_publisher.publish(
            reason_code=str(reason_code),
            message=str(message),
            payload={
                "reason_code": str(reason_code),
                "message": str(message),
                "raw_payload": raw_payload.decode("utf-8", errors="replace"),
            },
        )


def _to_decision_request(payload: Mapping[str, Any]) -> DecisionRequest | None:
    decision_id = _required_text(payload, "decision_id")
    context_id = _required_text(payload, "context_id")
    task_ref = _required_text(payload, "task_ref")
    truth_pack_ref = _required_text(payload, "truth_pack_ref")
    truth_pack_version = _parse_truth_pack_version(payload.get("truth_pack_version"))
    policy_raw = payload.get("policy_contract")
    if not all((decision_id, context_id, task_ref, truth_pack_ref)) or truth_pack_version is None:
        return None
    if not isinstance(policy_raw, Mapping):
        return None

    policy_id = _required_text(policy_raw, "policy_id")
    allow_enqueue = policy_raw.get("allow_enqueue")
    constraints = policy_raw.get("constraints", {})
    if not policy_id or not isinstance(allow_enqueue, bool):
        return None
    if not isinstance(constraints, dict):
        return None

    metadata_raw = payload.get("metadata", {})
    metadata: dict[str, Any] = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
    timestamp = payload.get("timestamp")
    timestamp_text = str(timestamp).strip() if isinstance(timestamp, str) else None

    return DecisionRequest(
        decision_id=decision_id,
        context_id=context_id,
        task_ref=task_ref,
        truth_pack_ref=truth_pack_ref,
        truth_pack_version=int(truth_pack_version),
        policy_contract=PolicyContract(
            policy_id=policy_id,
            allow_enqueue=bool(allow_enqueue),
            constraints=dict(constraints),
        ),
        metadata=metadata,
        timestamp=timestamp_text or None,
    )


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return str(value).strip() if isinstance(value, str) else ""


def _parse_truth_pack_version(value: Any) -> int | None:
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return int(raw)
        except Exception:
            return None
    return None


def _is_retriable_consumer_error(error: Any) -> bool:
    try:
        if bool(error.retriable()):
            return True
    except Exception:
        pass
    if KafkaError is None:
        return False
    try:
        return error.code() in {
            KafkaError.UNKNOWN_TOPIC_OR_PART,
            KafkaError._TRANSPORT,
            KafkaError._ALL_BROKERS_DOWN,
        }
    except Exception:
        return False
