"""
KafkaConsumerMixin — Kafka message-loop for OrchestratorWorker.

Handles: start/stop consumer, route messages by topic.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from bus.event_schemas import ApprovalEvent, DecisionEvent, DecisionStatus
from bus.kafka_retry import is_retryable_kafka_exception

logger = logging.getLogger("orchestrator_worker")


class KafkaConsumerMixin:
    """
    Kafka consumer loop.  Requires the following attributes on self:
      event_bus, _shutdown_event, _consumer_handle
    and the methods: _handle_message (defined here), _topic_name (defined here),
    _handle_policy_approved_event, _handle_approval_event, _handle_requested_event,
    _publish_invalid_event_dlq, _validate_episode_requested_v1.
    """

    def start(self) -> None:
        def handle_message(msg, consumer) -> None:
            self._handle_message(msg, consumer)

        self._shutdown_event.clear()
        decision_topic = self._topic_name("decision_lifecycle", "decision.lifecycle")
        approval_topic = self._topic_name("decision_approval", "decision.approval")
        policy_approved_topic = self._topic_name(
            "policy_approved",
            str(os.getenv("POLICY_APPROVED_TOPIC", "policy.approved")),
        )
        backoff_seconds = 1.0
        while not self._shutdown_event.is_set():
            consumer = self.event_bus.create_consumer(
                topics=[decision_topic, approval_topic, policy_approved_topic],
                group_id="orchestrator-workers",
                handler=handle_message,
            )
            self._consumer_handle = consumer
            try:
                consumer.start()
                backoff_seconds = 1.0
                return
            except Exception as exc:
                if self._shutdown_event.is_set():
                    return
                if is_retryable_kafka_exception(exc):
                    logger.warning(
                        "orchestrator_waiting_for_kafka_topics topics=%s backoff_seconds=%.1f error=%s",
                        [decision_topic, approval_topic, policy_approved_topic],
                        backoff_seconds,
                        exc,
                    )
                    time.sleep(backoff_seconds)
                    backoff_seconds = min(backoff_seconds * 2.0, 15.0)
                    continue
                raise

    def stop(self) -> None:
        self._shutdown_event.set()
        consumer = self._consumer_handle
        if consumer is None:
            return
        try:
            consumer.stop()
        except Exception:
            return

    def _handle_message(self, msg: Any, consumer: Any) -> None:
        if msg is None:
            return
        if msg.error():
            return

        topic = msg.topic()
        decision_topic = self._topic_name("decision_lifecycle", "decision.lifecycle")
        approval_topic = self._topic_name("decision_approval", "decision.approval")
        policy_approved_topic = self._topic_name(
            "policy_approved",
            str(os.getenv("POLICY_APPROVED_TOPIC", "policy.approved")),
        )
        if topic == policy_approved_topic:
            raw_payload = msg.value().decode("utf-8", errors="replace")
            try:
                policy_event = json.loads(raw_payload)
                if not isinstance(policy_event, dict):
                    raise ValueError("policy_approved_event_not_object")
            except Exception as exc:
                self._publish_invalid_event_dlq(
                    source_topic=topic,
                    raw_payload=raw_payload,
                    reason=f"policy_approved_parse_invalid:{exc}",
                )
                consumer.commit(message=msg, asynchronous=False)
                return
            self._handle_policy_approved_event(event=policy_event)
            consumer.commit(message=msg, asynchronous=False)
            return
        if topic == approval_topic:
            raw_payload = msg.value().decode("utf-8", errors="replace")
            try:
                approval_event = ApprovalEvent.from_json(raw_payload)
            except Exception as exc:
                self._publish_invalid_event_dlq(
                    source_topic=topic,
                    raw_payload=raw_payload,
                    reason=f"approval_event_parse_invalid:{exc}",
                )
                consumer.commit(message=msg, asynchronous=False)
                return
            self._handle_approval_event(approval_event=approval_event)
            consumer.commit(message=msg, asynchronous=False)
            return
        if topic != decision_topic:
            consumer.commit(message=msg, asynchronous=False)
            return

        raw_payload = msg.value().decode("utf-8", errors="replace")
        try:
            event = DecisionEvent.from_json(raw_payload)
        except Exception as exc:
            self._publish_invalid_event_dlq(
                source_topic=topic,
                raw_payload=raw_payload,
                reason=f"episode_requested_parse_invalid:{exc}",
            )
            consumer.commit(message=msg, asynchronous=False)
            return
        if event.status != DecisionStatus.REQUESTED:
            consumer.commit(message=msg, asynchronous=False)
            return
        try:
            self._validate_episode_requested_v1(event)
        except Exception as exc:
            self._publish_invalid_event_dlq(
                source_topic=topic,
                raw_payload=raw_payload,
                reason=f"episode_requested_schema_invalid:{exc}",
                decision_id=event.decision_id,
                context_id=event.context_id,
            )
            consumer.commit(message=msg, asynchronous=False)
            return
        self._handle_requested_event(event=event, topic=topic, msg=msg, consumer=consumer)

    def _topic_name(self, key: str, default: str) -> str:
        topics = self.event_bus.config.get("topics")
        if isinstance(topics, dict) and key in topics:
            return str(topics[key])
        return default
