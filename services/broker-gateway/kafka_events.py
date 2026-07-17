"""
Production-ready Kafka event bus.

Key features:
- Config-driven topic creation
- Safe defaults for dev/prod
- Manual commit consumers
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, Optional

import yaml

from babyai_shared.privacy.gateway import PrivacyGateway
try:
    from confluent_kafka import Consumer, Producer, KafkaError, KafkaException
    from confluent_kafka.admin import AdminClient, NewTopic
except Exception:  # pragma: no cover - optional dependency
    Consumer = None  # type: ignore
    Producer = None  # type: ignore
    KafkaError = None  # type: ignore
    KafkaException = Exception  # type: ignore
    AdminClient = None  # type: ignore
    NewTopic = None  # type: ignore

logger = logging.getLogger(__name__)


def _env_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


class KafkaEventBus:
    """Kafka event bus with safe defaults."""

    def __init__(
        self,
        config_path: str = "config/kafka_config.yaml",
        environment: str | None = None,
    ) -> None:
        if Producer is None or Consumer is None or AdminClient is None or NewTopic is None:
            raise ImportError("confluent-kafka is required for KafkaEventBus")
        self.config = self._load_config(config_path, environment)
        self.gateway = PrivacyGateway.default()

        producer_config = {
            "bootstrap.servers": self.config["brokers"],
            "client.id": self.config["client_id"],
            "acks": self.config["producer"]["acks"],
            "compression.type": self.config["producer"]["compression_type"],
            "linger.ms": self.config["producer"]["linger_ms"],
            "max.in.flight.requests.per.connection": self.config["producer"].get(
                "max_in_flight_requests_per_connection", 5
            ),
        }
        self.producer = Producer(producer_config)

        self.admin = AdminClient({
            "bootstrap.servers": self.config["brokers"],
        })

        if self.config.get("allow_auto_create_topics", False):
            self._ensure_topics()
        else:
            logger.info("Topic auto-creation disabled; topics must be pre-created.")

    def publish(
        self,
        topic: str,
        key: str,
        value: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        safe_value = self.gateway.scrub_json_string(value)
        kafka_headers = []
        if headers:
            kafka_headers = [
                (k, self.gateway.scrub_text(str(v)).encode("utf-8"))
                for k, v in headers.items()
            ]

        def delivery_callback(err, msg):
            if err:
                logger.error("Message delivery failed: %s", err)
            else:
                logger.debug(
                    "Delivered to %s[%s] @ %s", msg.topic(), msg.partition(), msg.offset()
                )

        self.producer.produce(
            topic=topic,
            key=key.encode("utf-8"),
            value=safe_value.encode("utf-8"),
            headers=kafka_headers,
            callback=delivery_callback,
        )
        self.producer.poll(0)

    def flush(self, timeout: float = 10.0) -> None:
        remaining = self.producer.flush(timeout)
        if remaining > 0:
            logger.warning("%s messages not delivered", remaining)

    def create_consumer(
        self,
        topics: list[str],
        group_id: str,
        handler: Callable[[Any, Consumer], None],
    ) -> "KafkaConsumer":
        return KafkaConsumer(
            topics=topics,
            group_id=group_id,
            handler=handler,
            config=self.config,
        )

    def shutdown(self) -> None:
        logger.info("Shutting down Kafka event bus...")
        self.flush(timeout=30.0)
        logger.info("Event bus shutdown complete")

    def _load_config(self, path: str, environment: str | None = None) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        if environment is None:
            environment = os.getenv("ENVIRONMENT", "development")

        envs = config.get("environments", {})
        if isinstance(envs, dict) and environment in envs:
            env_config = envs[environment] or {}
            if isinstance(env_config, dict):
                config.update(env_config)
            logger.info("Applied environment config: %s", environment)

        # Optional env overrides for containerized deployments.
        # KAFKA_BOOTSTRAP_SERVERS is the primary key; KAFKA_BROKERS is kept for backward compatibility.
        bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
        if bootstrap_servers:
            config["brokers"] = bootstrap_servers
        else:
            brokers = os.getenv("KAFKA_BROKERS")
            if brokers:
                config["brokers"] = brokers

        client_id = os.getenv("KAFKA_CLIENT_ID")
        if client_id:
            config["client_id"] = client_id

        auto_create = _env_bool("KAFKA_AUTO_CREATE_TOPICS")
        if auto_create is not None:
            config["allow_auto_create_topics"] = auto_create

        return config

    def _ensure_topics(self) -> None:
        required_topics = list((self.config.get("topics") or {}).values())
        if not required_topics:
            return

        metadata = self.admin.list_topics(timeout=10)
        existing = set(metadata.topics.keys())
        missing = [t for t in required_topics if t not in existing]

        if not missing:
            return

        new_topics = [
            NewTopic(
                topic=t,
                num_partitions=int(self.config.get("default_partitions", 1)),
                replication_factor=int(self.config.get("default_replication", 1)),
            )
            for t in missing
        ]

        fs = self.admin.create_topics(new_topics)
        for topic, f in fs.items():
            try:
                f.result()
                logger.info("Created topic: %s", topic)
            except Exception as e:
                logger.error("Failed to create %s: %s", topic, e)


class KafkaConsumer:
    """Consumer with manual commit for idempotency."""

    def __init__(
        self,
        topics: list[str],
        group_id: str,
        handler: Callable[[Any, Consumer], None],
        config: Dict[str, Any],
    ) -> None:
        self.topics = topics
        self.group_id = group_id
        self.handler = handler

        consumer_cfg = {
            "bootstrap.servers": config["brokers"],
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": config["consumer"]["enable_auto_commit"],
            "session.timeout.ms": config["consumer"]["session_timeout_ms"],
            "heartbeat.interval.ms": config["consumer"].get("heartbeat_interval_ms", 3000),
            "max.poll.interval.ms": config["consumer"].get("max_poll_interval_ms", 300000),
        }
        self.consumer = Consumer(consumer_cfg)
        self.consumer.subscribe(topics)
        self.running = False

    def start(self) -> None:
        self.running = True
        logger.info("Consumer %s started", self.group_id)
        try:
            while self.running:
                msg = self.consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    raise KafkaException(msg.error())

                try:
                    self.handler(msg, self.consumer)
                except Exception as e:
                    logger.error("Handler error: %s", e, exc_info=True)
        finally:
            self.consumer.close()
            logger.info("Consumer %s stopped", self.group_id)

    def stop(self) -> None:
        self.running = False
