"""
tools/kafka_provisioner.py — idempotent Kafka topic and consumer-group provisioner.

All operations are safe to call multiple times (idempotent by design).
Uses confluent-kafka AdminClient exclusively — no kafka-python dependency.

Default topic settings match the BabyAI platform standard:
  acks=all, compression=snappy, retention 7 days, 3 partitions dev.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

_BROKERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))

# Platform defaults — matches config/kafka_config.yaml
_DEFAULT_PARTITIONS   = 3
_DEFAULT_REPLICATION  = 1
_DEFAULT_TOPIC_CONFIG = {
    "cleanup.policy":  "delete",
    "retention.ms":    "604800000",   # 7 days
    "compression.type": "snappy",
}


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class TopicSpec:
    """Specification for a Kafka topic to provision."""

    name:           str
    num_partitions: int          = _DEFAULT_PARTITIONS
    config:         Dict[str, str] = field(default_factory=dict)


@dataclass
class TopicResult:
    """Result of a single topic provision call."""

    topic:   str
    created: bool
    existed: bool
    error:   Optional[str] = None


@dataclass
class ConsumerGroupResult:
    """Result of a consumer-group registration call."""

    group_id:   str
    registered: bool
    error:      Optional[str] = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class KafkaProvisionError(Exception):
    """Raised on genuine, unrecoverable provisioning failures."""


# ---------------------------------------------------------------------------
# KafkaProvisioner
# ---------------------------------------------------------------------------

class KafkaProvisioner:
    """
    Idempotent Kafka topic and consumer-group provisioner.

    All public methods are safe to call multiple times and from multiple
    threads; each call creates its own short-lived AdminClient.

    Args:
        brokers:            comma-separated bootstrap broker list
        replication_factor: default replication factor for new topics
        admin_timeout:      seconds to wait for admin operations
    """

    def __init__(
        self,
        brokers:            str   = _BROKERS,
        replication_factor: int   = _DEFAULT_REPLICATION,
        admin_timeout:      float = 10.0,
    ) -> None:
        self._brokers            = str(brokers or _BROKERS).strip()
        self._replication_factor = int(replication_factor)
        self._admin_timeout      = float(admin_timeout)

    # ── Public API ────────────────────────────────────────────────────────────

    def ensure_topic(
        self,
        topic_name:         str,
        num_partitions:     int              = _DEFAULT_PARTITIONS,
        replication_factor: Optional[int]    = None,
        config:             Optional[Dict[str, str]] = None,
    ) -> TopicResult:
        """
        Create topic if it does not exist; return immediately if it does.

        Returns:
            TopicResult(created=True)  when the topic was newly created.
            TopicResult(existed=True)  when it already existed.

        Raises:
            KafkaProvisionError on genuine broker failures (not on topic-exists).
        """
        rf     = int(replication_factor) if replication_factor is not None else self._replication_factor
        merged = dict(_DEFAULT_TOPIC_CONFIG)
        if config:
            merged.update(config)

        spec = TopicSpec(name=topic_name, num_partitions=num_partitions, config=merged)
        results = self.ensure_topics([spec])
        return results[0]

    def ensure_topics(self, topic_specs: List[TopicSpec]) -> List[TopicResult]:
        """
        Idempotent batch topic creation.

        Continues on per-topic errors and collects all results.
        Never raises; per-topic errors appear as TopicResult.error.
        """
        if not topic_specs:
            return []

        admin = self._build_admin()
        results: List[TopicResult] = []

        # Fetch existing topics once for the whole batch
        try:
            metadata   = admin.list_topics(timeout=self._admin_timeout)
            existing   = set(metadata.topics.keys())
        except Exception as exc:
            _log.error("kafka_list_topics_failed error=%s", exc)
            return [
                TopicResult(topic=s.name, created=False, existed=False, error=str(exc))
                for s in topic_specs
            ]

        to_create = [s for s in topic_specs if s.name not in existing]
        already   = [s for s in topic_specs if s.name in existing]

        # Topics that already exist
        for spec in already:
            _log.debug("kafka_topic_exists topic=%s", spec.name)
            results.append(TopicResult(topic=spec.name, created=False, existed=True))

        if not to_create:
            return results

        # Build NewTopic objects
        try:
            from confluent_kafka.admin import NewTopic
        except ImportError as exc:
            _log.error("confluent_kafka_not_installed error=%s", exc)
            for spec in to_create:
                results.append(TopicResult(topic=spec.name, created=False, existed=False, error=str(exc)))
            return results

        new_topics = [
            NewTopic(
                topic               = spec.name,
                num_partitions      = spec.num_partitions,
                replication_factor  = self._replication_factor,
                config              = {k: str(v) for k, v in (spec.config or {}).items()},
            )
            for spec in to_create
        ]

        fs = admin.create_topics(new_topics)
        for spec in to_create:
            future = fs.get(spec.name)
            if future is None:
                results.append(TopicResult(topic=spec.name, created=False, existed=False, error="no_future_returned"))
                continue
            try:
                future.result()
                _log.info("kafka_topic_created topic=%s partitions=%d", spec.name, spec.num_partitions)
                results.append(TopicResult(topic=spec.name, created=True, existed=False))
            except Exception as exc:
                err_str = str(exc)
                # Topic already exists — idempotent success
                if "already exists" in err_str or "TOPIC_ALREADY_EXISTS" in err_str:
                    _log.debug("kafka_topic_race_already_exists topic=%s", spec.name)
                    results.append(TopicResult(topic=spec.name, created=False, existed=True))
                else:
                    _log.warning("kafka_topic_create_failed topic=%s error=%s", spec.name, exc)
                    results.append(TopicResult(topic=spec.name, created=False, existed=False, error=err_str))

        return results

    def ensure_consumer_group(
        self,
        group_id: str,
        topics:   List[str],
    ) -> ConsumerGroupResult:
        """
        Register a consumer group by subscribing and immediately closing.

        This is idempotent — re-registering an existing group is harmless.

        Args:
            group_id: Kafka consumer group id
            topics:   topics the group should be subscribed to (for registration)
        """
        if not topics:
            return ConsumerGroupResult(group_id=group_id, registered=False, error="no_topics_provided")

        try:
            from confluent_kafka import Consumer, KafkaException
        except ImportError as exc:
            return ConsumerGroupResult(group_id=group_id, registered=False, error=str(exc))

        consumer: Any = None
        try:
            consumer = Consumer({
                "bootstrap.servers":  self._brokers,
                "group.id":           group_id,
                "auto.offset.reset":  "latest",
                "enable.auto.commit": False,
                "session.timeout.ms": 6000,
            })
            consumer.subscribe(topics[:1])   # subscribe to at least one topic
            consumer.poll(timeout=0.5)       # triggers group coordination
            _log.info("kafka_consumer_group_registered group_id=%s", group_id)
            return ConsumerGroupResult(group_id=group_id, registered=True)
        except Exception as exc:
            _log.warning("kafka_consumer_group_failed group_id=%s error=%s", group_id, exc)
            return ConsumerGroupResult(group_id=group_id, registered=False, error=str(exc))
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:
                    pass

    def topic_exists(self, topic_name: str) -> bool:
        """
        Check whether a topic exists without any side effects.

        Returns False on any error (including broker unavailability).
        """
        try:
            admin    = self._build_admin()
            metadata = admin.list_topics(timeout=self._admin_timeout)
            return topic_name in metadata.topics
        except Exception as exc:
            _log.debug("kafka_topic_exists_check_failed topic=%s error=%s", topic_name, exc)
            return False

    def list_consumer_groups(self) -> List[str]:
        """
        Return all registered consumer group ids.

        Returns empty list on any error.
        """
        try:
            admin = self._build_admin()
            # confluent_kafka >= 2.2 supports list_consumer_groups()
            result = admin.list_consumer_groups()
            return [g.group_id for g in result.valid]
        except AttributeError:
            # Fall back to describe_groups for older confluent_kafka versions
            return self._list_groups_fallback()
        except Exception as exc:
            _log.warning("kafka_list_groups_failed error=%s", exc)
            return []

    def get_topic_lag(self, group_id: str, topic: str) -> Dict[int, int]:
        """
        Return {partition: lag} for monitoring / GapDetectorAgent.

        Lag = high_watermark - committed_offset for each partition.
        Returns empty dict on any error.
        """
        try:
            from confluent_kafka import Consumer, TopicPartition
        except ImportError:
            return {}

        consumer: Any = None
        try:
            consumer = Consumer({
                "bootstrap.servers":  self._brokers,
                "group.id":           group_id,
                "enable.auto.commit": False,
            })
            metadata = consumer.list_topics(topic=topic, timeout=self._admin_timeout)
            if topic not in metadata.topics:
                return {}

            partitions = [
                TopicPartition(topic, p)
                for p in metadata.topics[topic].partitions
            ]

            committed  = consumer.committed(partitions, timeout=self._admin_timeout)
            lags: Dict[int, int] = {}
            for tp in committed:
                lo, hi = consumer.get_watermark_offsets(tp, timeout=self._admin_timeout)
                offset  = tp.offset if tp.offset >= 0 else hi
                lags[tp.partition] = max(0, hi - offset)

            _log.debug(
                "kafka_lag_computed group=%s topic=%s lags=%s", group_id, topic, lags
            )
            return lags
        except Exception as exc:
            _log.warning("kafka_get_lag_failed group=%s topic=%s error=%s", group_id, topic, exc)
            return {}
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:
                    pass

    # ── Internal ──────────────────────────────────────────────────────────────

    def _build_admin(self) -> Any:
        from confluent_kafka.admin import AdminClient
        return AdminClient({
            "bootstrap.servers":       self._brokers,
            "socket.timeout.ms":       int(self._admin_timeout * 1000),
            "request.timeout.ms":      int(self._admin_timeout * 1000),
        })

    def _list_groups_fallback(self) -> List[str]:
        """Fallback for older confluent_kafka that lacks list_consumer_groups()."""
        try:
            from confluent_kafka.admin import AdminClient
            admin    = self._build_admin()
            result   = admin.list_groups(timeout=self._admin_timeout)
            return [g.id for g in result if g.id]
        except Exception as exc:
            _log.warning("kafka_list_groups_fallback_failed error=%s", exc)
            return []
