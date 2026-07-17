from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Mapping

from babyai_shared.bus.event_schemas import ApprovalEvent
import logging
from babyai_shared.core.logging_milestones import log_milestone
from application.ports import DlqPublisher, LifecyclePublisher
from domain.models import CanonicalLifecycleRequestedEvent
from domain.services import canonicalize_lifecycle_requested_event

try:
    from confluent_kafka import Producer
except Exception:  # pragma: no cover - optional dependency
    Producer = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
_SERVICE_NAME = "request-gate"


def _require_producer() -> Any:
    if Producer is None:
        raise ImportError("confluent-kafka is required for request_gate Kafka publishers")
    return Producer


class KafkaLifecyclePublisher(LifecyclePublisher):
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str = "decision.lifecycle",
        client_id: str = "request-gate-lifecycle",
    ) -> None:
        producer_cls = _require_producer()
        self._topic = str(topic).strip() or "decision.lifecycle"
        self._producer = producer_cls(
            {
                "bootstrap.servers": str(bootstrap_servers),
                "client.id": str(client_id),
            }
        )

    def publish(self, event: CanonicalLifecycleRequestedEvent) -> None:
        payload = canonicalize_lifecycle_requested_event(event)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self._producer.produce(
            topic=self._topic,
            key=str(event.decision_id).encode("utf-8"),
            value=raw,
        )
        remaining = self._producer.flush(10.0)
        if remaining > 0:
            raise RuntimeError(f"lifecycle_publish_flush_timeout remaining={remaining}")
        metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
        trace_id = str(metadata.get("trace_id") or "") if isinstance(metadata, dict) else ""
        log_milestone(
            logger,
            "message_published",
            service_name=_SERVICE_NAME,
            component="infrastructure.kafka_publishers.lifecycle",
            decision_id=str(event.decision_id),
            context_id=str(event.context_id),
            episode_id=str(event.decision_id),
            event_type=str(event.status),
            topic=self._topic,
            fingerprint=str(metadata.get("event_fingerprint") or ""),
            event_id=str(metadata.get("event_id") or ""),
            trace_id=trace_id,
        )

    def close(self) -> None:
        self._producer.flush(5.0)


class KafkaDlqPublisher(DlqPublisher):
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str = "decision.requested.dlq",
        client_id: str = "request-gate-dlq",
    ) -> None:
        producer_cls = _require_producer()
        self._topic = str(topic).strip() or "decision.requested.dlq"
        self._producer = producer_cls(
            {
                "bootstrap.servers": str(bootstrap_servers),
                "client.id": str(client_id),
            }
        )

    def publish(self, *, reason_code: str, message: str, payload: Mapping[str, Any]) -> None:
        body = {
            "reason_code": str(reason_code),
            "message": str(message),
            "payload": dict(payload or {}),
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        raw = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self._producer.produce(
            topic=self._topic,
            key=str(reason_code).encode("utf-8"),
            value=raw,
        )
        self._producer.flush(10.0)
        req = payload.get("request", {}) if isinstance(payload, Mapping) else {}
        decision_id = str(req.get("decision_id") or "")
        context_id = str(req.get("context_id") or "")
        trace_id = ""
        metadata = req.get("metadata")
        if isinstance(metadata, dict):
            trace_id = str(metadata.get("trace_id") or "")
        log_milestone(
            logger,
            "message_published",
            service_name=_SERVICE_NAME,
            component="infrastructure.kafka_publishers.dlq",
            decision_id=decision_id,
            context_id=context_id,
            episode_id=decision_id,
            event_type="dlq",
            topic=self._topic,
            trace_id=trace_id,
            reason_code=str(reason_code),
        )

    def close(self) -> None:
        self._producer.flush(5.0)


class KafkaApprovalPublisher:
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str = "decision.approval",
        client_id: str = "request-gate-approval",
    ) -> None:
        producer_cls = _require_producer()
        self._topic = str(topic).strip() or "decision.approval"
        self._producer = producer_cls(
            {
                "bootstrap.servers": str(bootstrap_servers),
                "client.id": str(client_id),
            }
        )

    def publish(self, event: ApprovalEvent) -> None:
        raw = event.to_json().encode("utf-8")
        self._producer.produce(
            topic=self._topic,
            key=str(event.decision_id).encode("utf-8"),
            value=raw,
        )
        remaining = self._producer.flush(10.0)
        if remaining > 0:
            raise RuntimeError(f"approval_publish_flush_timeout remaining={remaining}")
        log_milestone(
            logger,
            "message_published",
            service_name=_SERVICE_NAME,
            component="infrastructure.kafka_publishers.approval",
            decision_id=str(event.decision_id),
            context_id=str(event.context_id or ""),
            episode_id=str(event.decision_id),
            event_type="decision.approval",
            topic=self._topic,
            fingerprint=str(event.policy_fingerprint),
            event_id=str(getattr(event, "event_id", "") or getattr(event, "content_hash", "")),
            trace_id="",
            approved=bool(event.approved),
        )

    def close(self) -> None:
        self._producer.flush(5.0)
