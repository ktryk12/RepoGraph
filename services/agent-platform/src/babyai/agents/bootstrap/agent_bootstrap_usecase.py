"""
babyai/agents/bootstrap/agent_bootstrap_usecase.py

AgentBootstrapUseCase — atomically provisions Kafka topics and consumer
groups for a new agent, publishes approval, and writes a structured
audit log entry.

Design principles (same as ApproveUseCase / DestillationUseCase):
  - Constructor takes all dependencies (testable via injection).
  - execute() NEVER raises — always returns BootstrapResult.
  - Rollback is logged to DLQ on step 2/3 failure.
  - Cannot roll back Kafka topic creation (topics are idempotent/harmless).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.kafka_provisioner import (
    KafkaProvisioner,
    TopicResult,
    TopicSpec,
)

_log = logging.getLogger(__name__)

# Kafka topic constants — mirrors bus/topics.py (inline to avoid bus __init__ chain)
_POLICY_APPROVED       = "policy.approved"
_POLICY_BOOTSTRAP_DLQ  = "policy.bootstrap.dlq"
_SIGNAL_INFRA_BOOTSTRAP_COMPLETE = "signal.infra.bootstrap.complete"
_SIGNAL_INFRA_BOOTSTRAP_FAILED   = "signal.infra.bootstrap.failed"

# Audit log location (relative to repo root, override via AGENT_BOOTSTRAP_LOG)
_DEFAULT_LOG_PATH = "logs/agent_bootstrap.log"


# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

@dataclass
class AgentSpec:
    """
    Full specification for bootstrapping a new agent.

    Fields
    ------
    agent_id           unique identifier, e.g. "crypto-intel-001"
    agent_class        dotted module path, e.g. "agents.crypto_intel_agent.CryptoIntelAgent"
    topics_to_consume  TopicSpecs this agent will consume
    topics_to_produce  TopicSpecs this agent will produce
    consumer_group_id  Kafka consumer group id for this agent
    policy_ref         reference to the policy that approved this deployment
    approved_by        "human" | "policy_pipeline"
    approved_at        ISO8601 timestamp of approval
    """

    agent_id:           str
    agent_class:        str
    topics_to_consume:  List[TopicSpec]     = field(default_factory=list)
    topics_to_produce:  List[TopicSpec]     = field(default_factory=list)
    consumer_group_id:  str                 = ""
    policy_ref:         str                 = ""
    approved_by:        str                 = "policy_pipeline"
    approved_at:        str                 = ""

    def __post_init__(self) -> None:
        if not self.approved_at:
            self.approved_at = datetime.now(timezone.utc).isoformat()
        if not self.consumer_group_id:
            self.consumer_group_id = f"{self.agent_id}-group"


@dataclass
class BootstrapResult:
    """
    Outcome of AgentBootstrapUseCase.execute().

    Never None — execute() guarantees a result even on total failure.
    """

    success:                    bool
    agent_spec:                 AgentSpec
    topics_created:             List[TopicResult]   = field(default_factory=list)
    consumer_group_registered:  bool                = False
    rollback_performed:         bool                = False
    error:                      Optional[str]       = None


# ---------------------------------------------------------------------------
# AgentBootstrapUseCase
# ---------------------------------------------------------------------------

class AgentBootstrapUseCase:
    """
    Atomically bootstrap a new agent's Kafka infrastructure.

    Steps (with rollback semantics):
      1. ensure_topics(consume + produce)
      2. ensure_consumer_group(consumer_group_id, consume_topics)
      3. Publish AgentSpec to policy.approved topic
      4. Write structured audit log entry

    Rollback: on step 2 or 3 failure, log rollback_performed=True and
    publish failure event to policy.bootstrap.dlq.
    (Kafka topics are NOT rolled back — creation is idempotent/harmless.)

    Usage::

        provisioner = KafkaProvisioner(brokers="localhost:9092")
        use_case = AgentBootstrapUseCase(provisioner=provisioner)
        result = use_case.execute(agent_spec)
        assert result.success
    """

    def __init__(
        self,
        provisioner:    KafkaProvisioner     | None = None,
        brokers:        str                        = "",
        log_path:       str                        = "",
        _producer:      Any                  | None = None,   # injected in tests
    ) -> None:
        """
        Args:
            provisioner: KafkaProvisioner instance (created if None)
            brokers:     Kafka bootstrap servers (used if provisioner is None)
            log_path:    path for audit log (default: logs/agent_bootstrap.log)
            _producer:   confluent_kafka.Producer for testing injection
        """
        _brokers = str(brokers or os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"))
        self._provisioner = provisioner or KafkaProvisioner(brokers=_brokers)
        self._log_path    = Path(str(log_path or os.getenv("AGENT_BOOTSTRAP_LOG", _DEFAULT_LOG_PATH)))
        self._brokers     = _brokers
        self._producer    = _producer   # None = built lazily

    # ── Public API ────────────────────────────────────────────────────────────

    def execute(self, agent_spec: AgentSpec) -> BootstrapResult:
        """
        Execute the bootstrap sequence.

        Never raises.  Returns BootstrapResult.success=False on any failure.
        """
        _log.info(
            "agent_bootstrap_start agent_id=%s agent_class=%s",
            agent_spec.agent_id, agent_spec.agent_class,
        )

        # -- Step 1: ensure Kafka topics ──────────────────────────────────────
        all_specs  = list(agent_spec.topics_to_consume) + list(agent_spec.topics_to_produce)
        topics_created: List[TopicResult] = []

        if all_specs:
            try:
                topics_created = self._provisioner.ensure_topics(all_specs)
            except Exception as exc:
                _log.error("agent_bootstrap_topic_provision_failed agent_id=%s error=%s", agent_spec.agent_id, exc)
                result = BootstrapResult(
                    success=False,
                    agent_spec=agent_spec,
                    topics_created=[],
                    rollback_performed=True,
                    error=f"topic_provision_failed: {exc}",
                )
                self._publish_dlq(agent_spec, result)
                self._write_audit_log(agent_spec, result)
                return result

        # -- Step 2: register consumer group ──────────────────────────────────
        consume_topic_names = [s.name for s in agent_spec.topics_to_consume]
        consumer_group_registered = False

        if consume_topic_names and agent_spec.consumer_group_id:
            cg_result = self._provisioner.ensure_consumer_group(
                group_id=agent_spec.consumer_group_id,
                topics=consume_topic_names,
            )
            consumer_group_registered = cg_result.registered
            if not cg_result.registered:
                error_msg = f"consumer_group_failed: {cg_result.error}"
                result = BootstrapResult(
                    success=False,
                    agent_spec=agent_spec,
                    topics_created=topics_created,
                    consumer_group_registered=False,
                    rollback_performed=True,
                    error=error_msg,
                )
                self._publish_dlq(agent_spec, result)
                self._write_audit_log(agent_spec, result)
                return result

        # -- Step 3: publish approval event ───────────────────────────────────
        try:
            self._publish_approved(agent_spec)
        except Exception as exc:
            error_msg = f"approval_publish_failed: {exc}"
            _log.error("agent_bootstrap_publish_failed agent_id=%s error=%s", agent_spec.agent_id, exc)
            result = BootstrapResult(
                success=False,
                agent_spec=agent_spec,
                topics_created=topics_created,
                consumer_group_registered=consumer_group_registered,
                rollback_performed=True,
                error=error_msg,
            )
            self._publish_dlq(agent_spec, result)
            self._write_audit_log(agent_spec, result)
            return result

        # -- Step 4: write audit log ───────────────────────────────────────────
        result = BootstrapResult(
            success=True,
            agent_spec=agent_spec,
            topics_created=topics_created,
            consumer_group_registered=consumer_group_registered,
            rollback_performed=False,
        )
        self._write_audit_log(agent_spec, result)
        _log.info(
            "agent_bootstrap_complete agent_id=%s topics_created=%d",
            agent_spec.agent_id,
            sum(1 for t in topics_created if t.created),
        )
        return result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _publish_approved(self, spec: AgentSpec) -> None:
        """Publish AgentSpec to policy.approved topic."""
        payload = {
            "event":        "agent_approved",
            "agent_id":     spec.agent_id,
            "agent_class":  spec.agent_class,
            "policy_ref":   spec.policy_ref,
            "approved_by":  spec.approved_by,
            "approved_at":  spec.approved_at,
            "consumer_group_id": spec.consumer_group_id,
            "topics_to_consume": [s.name for s in spec.topics_to_consume],
            "topics_to_produce": [s.name for s in spec.topics_to_produce],
        }
        self._kafka_publish(_POLICY_APPROVED, key=spec.agent_id, payload=payload)
        self._kafka_publish(
            _SIGNAL_INFRA_BOOTSTRAP_COMPLETE,
            key=spec.agent_id,
            payload={**payload, "event": "agent_bootstrap_complete"},
        )

    def _publish_dlq(self, spec: AgentSpec, result: BootstrapResult) -> None:
        """Publish failure event to DLQ."""
        payload = {
            "event":              "agent_bootstrap_failed",
            "agent_id":           spec.agent_id,
            "agent_class":        spec.agent_class,
            "error":              result.error,
            "rollback_performed": result.rollback_performed,
            "timestamp":          datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._kafka_publish(_POLICY_BOOTSTRAP_DLQ, key=spec.agent_id, payload=payload)
            self._kafka_publish(
                _SIGNAL_INFRA_BOOTSTRAP_FAILED,
                key=spec.agent_id,
                payload=payload,
            )
        except Exception as exc:
            _log.error("agent_bootstrap_dlq_publish_failed agent_id=%s error=%s", spec.agent_id, exc)

    def _kafka_publish(self, topic: str, key: str, payload: Dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        producer = self._producer or self._build_producer()
        if producer is None:
            _log.warning("kafka_publish_skipped_no_producer topic=%s key=%s", topic, key)
            return
        self._producer = producer   # cache after first successful build
        producer.produce(
            topic=topic,
            key=key.encode("utf-8"),
            value=raw,
        )
        remaining = producer.flush(10.0)
        if remaining > 0:
            raise RuntimeError(f"kafka_publish_timeout remaining={remaining} topic={topic}")

    def _build_producer(self) -> Optional[Any]:
        try:
            from confluent_kafka import Producer
            return Producer({
                "bootstrap.servers": self._brokers,
                "acks":              "all",
            })
        except Exception as exc:
            _log.warning("kafka_producer_build_failed error=%s", exc)
            return None

    def _write_audit_log(self, spec: AgentSpec, result: BootstrapResult) -> None:
        """Append one JSON-lines entry to the audit log."""
        entry = {
            "event":                    "agent_bootstrap",
            "timestamp":                datetime.now(timezone.utc).isoformat(),
            "agent_id":                 spec.agent_id,
            "agent_class":              spec.agent_class,
            "policy_ref":               spec.policy_ref,
            "approved_by":              spec.approved_by,
            "topics_created":           [
                {"topic": t.topic, "created": t.created, "existed": t.existed}
                for t in result.topics_created
            ],
            "consumer_group_registered": result.consumer_group_registered,
            "rollback_performed":        result.rollback_performed,
            "success":                   result.success,
            "error":                     result.error,
        }
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")
        except Exception as exc:
            _log.error("agent_bootstrap_audit_log_failed path=%s error=%s", self._log_path, exc)
