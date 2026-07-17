from __future__ import annotations

import json
from typing import Any, Dict, Optional

from babyai_shared.bus.protocol import Message, MessageType
from bus.interfaces import MessageBus, MessageHandler

try:
    from confluent_kafka import Consumer, Producer, KafkaException, KafkaError
except Exception:  # pragma: no cover - optional dependency
    Consumer = None
    Producer = None
    KafkaException = Exception
    KafkaError = None


class KafkaBus(MessageBus):
    """
    Kafka-backed MessageBus.

    Topics:
    - agent.messages.v1
    - agent.deadletter.v1

    Key: context_id
    Value: JSON-serialized Message (ASCII-only)
    Headers: message_id, message_type, from_agent, to_agent (+ trace_id if present)
    """

    topic_main = "agent.messages.v1"
    topic_dlq = "agent.deadletter.v1"

    def __init__(
        self,
        *,
        bootstrap_servers: str = "localhost:9092",
        group_id: Optional[str] = None,
        topic_main: Optional[str] = None,
        topic_dlq: Optional[str] = None,
        poll_timeout: float = 1.0,
        producer_config: Optional[Dict[str, Any]] = None,
        consumer_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        if Producer is None or Consumer is None:
            raise ImportError("confluent-kafka is required for KafkaBus")

        self.topic_main = topic_main or self.topic_main
        self.topic_dlq = topic_dlq or self.topic_dlq
        self._poll_timeout = poll_timeout

        prod_conf = {"bootstrap.servers": bootstrap_servers}
        if producer_config:
            prod_conf.update(producer_config)
        self._producer = Producer(prod_conf)

        self._consumer = None
        if group_id:
            cons_conf = {
                "bootstrap.servers": bootstrap_servers,
                "group.id": group_id,
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
            }
            if consumer_config:
                cons_conf.update(consumer_config)
            self._consumer = Consumer(cons_conf)
            self._consumer.subscribe([self.topic_main])

    def publish(self, message: Message) -> None:
        payload = self._serialize_message(message)
        headers = self._build_headers(message)
        self._producer.produce(
            self.topic_main,
            key=str(message.context_id),
            value=payload,
            headers=headers,
        )
        self._producer.flush()

    def subscribe(self, handler: MessageHandler, max_messages: int | None = None) -> int:
        if self._consumer is None:
            raise RuntimeError("KafkaBus consumer not configured (group_id required)")

        processed = 0
        limit = max_messages if max_messages is not None else float("inf")

        while processed < limit:
            msg = self._consumer.poll(self._poll_timeout)
            if msg is None:
                break
            if msg.error():
                if KafkaError is not None and msg.error().code() == KafkaError._PARTITION_EOF:
                    break
                raise KafkaException(msg.error())

            try:
                message = self._deserialize_message(msg.value())
                handler(message)
                self._consumer.commit(message=msg, asynchronous=False)
            except Exception as e:
                self._publish_dlq(msg, e)
                self._consumer.commit(message=msg, asynchronous=False)
            processed += 1

        return processed

    def close(self) -> None:
        if self._consumer is not None:
            self._consumer.close()
        self._producer.flush()

    @staticmethod
    def _serialize_message(message: Message) -> bytes:
        data = {
            "message_id": message.message_id,
            "from_agent": message.from_agent,
            "to_agent": message.to_agent,
            "message_type": message.message_type.value
            if isinstance(message.message_type, MessageType)
            else str(message.message_type),
            "payload": message.payload,
            "context_id": message.context_id,
            "timestamp": message.timestamp,
        }
        return json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _deserialize_message(raw: bytes | None) -> Message:
        if raw is None:
            raise ValueError("Kafka message value is empty")
        data = json.loads(raw.decode("utf-8"))
        msg_type = MessageType(data["message_type"])
        return Message(
            message_id=str(data["message_id"]),
            from_agent=str(data["from_agent"]),
            to_agent=str(data["to_agent"]),
            message_type=msg_type,
            payload=dict(data.get("payload") or {}),
            context_id=str(data["context_id"]),
            timestamp=str(data["timestamp"]),
        )

    def _build_headers(self, message: Message) -> list[tuple[str, str]]:
        headers = [
            ("message_id", str(message.message_id)),
            ("message_type", message.message_type.value),
            ("from_agent", str(message.from_agent)),
            ("to_agent", str(message.to_agent)),
        ]
        trace_id = message.payload.get("trace_id")
        if trace_id:
            headers.append(("trace_id", str(trace_id)))
        return headers

    def _publish_dlq(self, msg, error: Exception) -> None:
        try:
            original_value = msg.value().decode("utf-8") if msg.value() else None
        except Exception:
            original_value = None

        payload = {
            "error": str(error),
            "topic": msg.topic(),
            "partition": msg.partition(),
            "offset": msg.offset(),
            "key": msg.key().decode("utf-8") if msg.key() else None,
            "value": original_value,
        }

        self._producer.produce(
            self.topic_dlq,
            key=msg.key(),
            value=json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
            headers=[("error", str(error))],
        )
        self._producer.flush()
