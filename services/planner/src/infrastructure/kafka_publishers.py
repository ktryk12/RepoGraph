from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Mapping

import logging
from babyai_shared.core.logging_milestones import log_milestone
from planner.application.ports import DecisionRequestedPublisher, DlqPublisher

try:
    from confluent_kafka import Producer
except Exception:  # pragma: no cover - optional dependency
    Producer = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
_SERVICE_NAME = "planner"


def _require_producer() -> Any:
    if Producer is None:
        raise ImportError("confluent-kafka is required for planner publishers")
    return Producer


class _BaseKafkaPublisher:
    def __init__(self, *, bootstrap_servers: str, topic: str, client_id: str) -> None:
        producer_cls = _require_producer()
        self._topic = str(topic).strip()
        self._producer = producer_cls(
            {
                "bootstrap.servers": str(bootstrap_servers),
                "client.id": str(client_id),
            }
        )

    def _publish(self, *, key: str, payload: Mapping[str, Any]) -> None:
        raw = json.dumps(dict(payload or {}), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self._producer.produce(topic=self._topic, key=str(key).encode("utf-8"), value=raw)
        remaining = self._producer.flush(10.0)
        if remaining > 0:
            raise RuntimeError(f"kafka_flush_timeout topic={self._topic} remaining={remaining}")
        decision_id = str(payload.get("decision_id") or "")
        context_id = str(payload.get("context_id") or "")
        event_type = str(payload.get("event_type") or payload.get("status") or "")
        trace_id = str(payload.get("trace_id") or "")
        log_milestone(
            logger,
            "message_published",
            service_name=_SERVICE_NAME,
            component="infrastructure.kafka_publishers",
            decision_id=decision_id,
            context_id=context_id,
            episode_id=decision_id,
            event_type=event_type,
            topic=self._topic,
            event_id="",
            trace_id=trace_id,
        )

    def close(self) -> None:
        self._producer.flush(5.0)


class KafkaDecisionRequestedPublisher(_BaseKafkaPublisher, DecisionRequestedPublisher):
    def __init__(self, *, bootstrap_servers: str, topic: str = "decision.requested") -> None:
        super().__init__(bootstrap_servers=bootstrap_servers, topic=topic, client_id="planner-requested")

    def publish(self, payload: Mapping[str, Any]) -> None:
        self._publish(key=str(payload.get("decision_id") or "decision-requested"), payload=payload)


class KafkaDlqPublisher(_BaseKafkaPublisher, DlqPublisher):
    def __init__(self, *, bootstrap_servers: str, topic: str = "decision.planner.dlq") -> None:
        super().__init__(bootstrap_servers=bootstrap_servers, topic=topic, client_id="planner-dlq")

    def publish_dlq(self, *, reason_code: str, message: str, payload: Mapping[str, Any]) -> None:
        body = {
            "reason_code": str(reason_code),
            "message": str(message),
            "payload": dict(payload or {}),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._publish(key=str(reason_code), payload=body)
