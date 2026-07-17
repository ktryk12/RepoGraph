from __future__ import annotations

import json
import logging
import time
from typing import Any, Mapping

from babyai_shared.core.logging_milestones import log_milestone
from truthpack_conversation.application.use_cases import TruthpackConversationService

try:
    from confluent_kafka import Consumer, KafkaError, KafkaException
except Exception:  # pragma: no cover - optional dependency
    Consumer = None  # type: ignore[assignment]
    KafkaError = None  # type: ignore[assignment]
    KafkaException = Exception  # type: ignore[assignment]


logger = logging.getLogger(__name__)
_SERVICE_NAME = "truthpack-conversation"
_COMPONENT = "infrastructure.kafka_consumer"


def _require_consumer() -> Any:
    if Consumer is None:
        raise ImportError("confluent-kafka is required for truthpack_conversation consumer")
    return Consumer


class KafkaTruthpackConversationConsumer:
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        intent_topic: str,
        answers_topic: str,
        group_id: str,
        service: TruthpackConversationService,
        poll_timeout_seconds: float = 1.0,
    ) -> None:
        consumer_cls = _require_consumer()
        self._intent_topic = str(intent_topic).strip() or "decision.intent"
        self._answers_topic = str(answers_topic).strip() or "decision.truthpack.answers"
        self._poll_timeout_seconds = float(poll_timeout_seconds)
        self._service = service
        self._consumer = consumer_cls(
            {
                "bootstrap.servers": str(bootstrap_servers),
                "group.id": str(group_id),
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        self._claim_topic = str(
            getattr(service, "_claim_topic", None) or "claim.detected"
        )
        self._consumer.subscribe(
            [self._intent_topic, self._answers_topic, self._claim_topic]
        )
        log_milestone(
            logger,
            "consumer_subscribe",
            service_name=_SERVICE_NAME,
            component=_COMPONENT,
            decision_id="",
            context_id="",
            episode_id="",
            event_type="",
            topic=",".join([self._intent_topic, self._answers_topic, self._claim_topic]),
            event_id="",
            trace_id="",
            topics=[self._intent_topic, self._answers_topic, self._claim_topic],
        )

    def run_once(self) -> int:
        log_milestone(
            logger,
            "consumer_poll",
            service_name=_SERVICE_NAME,
            component=_COMPONENT,
            decision_id="",
            context_id="",
            episode_id="",
            event_type="",
            topic="",
            event_id="",
            trace_id="",
        )
        msg = self._consumer.poll(timeout=self._poll_timeout_seconds)
        if msg is None:
            return 0
        if msg.error():
            if KafkaError is not None and msg.error().code() == KafkaError._PARTITION_EOF:
                return 0
            raise KafkaException(msg.error())

        raw = msg.value() or b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, Mapping):
            payload = {}

        topic = str(msg.topic() or "")
        decision_id = str(payload.get("decision_id") or "")
        context_id = str(payload.get("context_id") or "")
        key_raw = msg.key()
        key_text = key_raw.decode("utf-8", errors="replace") if isinstance(key_raw, (bytes, bytearray)) else str(key_raw or "")
        log_milestone(
            logger,
            "message_received",
            service_name=_SERVICE_NAME,
            component=_COMPONENT,
            decision_id=decision_id,
            context_id=context_id,
            episode_id=decision_id,
            event_type=topic,
            topic=topic,
            event_id="",
            trace_id=str(payload.get("trace_id") or ""),
            partition=int(msg.partition()),
            offset=int(msg.offset()),
            key=key_text,
        )
        if topic == self._intent_topic:
            self._service.handle_intent(payload)
        elif topic == self._answers_topic:
            self._service.handle_answers(payload)
        elif topic == self._claim_topic:
            self._service.handle_claim(payload)

        self._consumer.commit(message=msg, asynchronous=False)
        return 1

    def run_forever(self, *, stop_event: Any, idle_sleep_seconds: float = 0.2) -> None:
        while not stop_event.is_set():
            processed = self.run_once()
            if processed == 0:
                time.sleep(float(idle_sleep_seconds))

    def close(self) -> None:
        self._consumer.close()
