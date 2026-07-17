from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Mapping

import logging
from babyai_shared.core.logging_milestones import log_milestone
from truthpack_conversation.application.ports import DlqPublisher, QuestionsPublisher, ReadyPublisher

try:
    from confluent_kafka import Producer
except Exception:  # pragma: no cover - optional dependency
    Producer = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
_SERVICE_NAME = "truthpack-conversation"


def _require_producer() -> Any:
    if Producer is None:
        raise ImportError("confluent-kafka is required for truthpack_conversation publishers")
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
        body = json.dumps(dict(payload or {}), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self._producer.produce(topic=self._topic, key=str(key).encode("utf-8"), value=body)
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


class KafkaQuestionsPublisher(_BaseKafkaPublisher, QuestionsPublisher):
    def __init__(self, *, bootstrap_servers: str, topic: str = "decision.truthpack.questions") -> None:
        super().__init__(bootstrap_servers=bootstrap_servers, topic=topic, client_id="truthpack-questions")

    def publish_questions(self, payload: Mapping[str, Any]) -> None:
        key = str(payload.get("decision_id") or "question")
        self._publish(key=key, payload=payload)


class KafkaReadyPublisher(_BaseKafkaPublisher, ReadyPublisher):
    def __init__(self, *, bootstrap_servers: str, topic: str = "decision.truthpack.ready") -> None:
        super().__init__(bootstrap_servers=bootstrap_servers, topic=topic, client_id="truthpack-ready")

    def publish_ready(self, payload: Mapping[str, Any]) -> None:
        key = str(payload.get("decision_id") or "ready")
        self._publish(key=key, payload=payload)


class KafkaDlqPublisher(_BaseKafkaPublisher, DlqPublisher):
    def __init__(self, *, bootstrap_servers: str, topic: str = "decision.truthpack.dlq") -> None:
        super().__init__(bootstrap_servers=bootstrap_servers, topic=topic, client_id="truthpack-dlq")

    def publish_dlq(self, *, reason_code: str, message: str, payload: Mapping[str, Any]) -> None:
        body = {
            "reason_code": str(reason_code),
            "message": str(message),
            "payload": dict(payload or {}),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._publish(key=str(reason_code), payload=body)
