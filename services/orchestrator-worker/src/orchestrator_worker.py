"""
Orchestrator worker with idempotency and manual commit.

Processing model: at-least-once + idempotency lock.

This module is the coordinator that wires together three mixins:
  - KafkaConsumerMixin  (kafka_consumer)  — message loop
  - EpisodeExecutorMixin (episode_executor) — episode execution + eval
  - WorkerLifecycleMixin (worker_lifecycle) — approval, dedupe, policy cache

IMPORTANT: run_episode and load_truth_pack are kept as module-level names here so
that tests can monkeypatch them:
    monkeypatch.setattr("orchestrator_worker.run_episode", mock)
    monkeypatch.setattr("orchestrator_worker.load_truth_pack", mock)
The mixin methods that need these functions import this module lazily to access them.
"""
from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, Mapping, Optional
from uuid import uuid4

from jsonschema import Draft202012Validator
from services.aesa.domain.approval import ExecutionPermit, require_execution_permit_from_mapping
from bus.event_schemas import (
    ApprovalEvent,
    ArtifactEvent,
    DecisionEvent,
    DecisionStatus,
    EvalResultEvent,
    SCHEMA_VERSION,
    now_iso,
)
from bus.kafka_events import KafkaEventBus
from bus.kafka_retry import is_retryable_kafka_exception
from bus import metrics
from babyai_shared.core.logging_milestones import log_milestone
from babyai_shared.core.orchestrator import run_episode  # MODULE-LEVEL: tests patch "bus.orchestrator_worker.run_episode"
from policy.approval_gate import approval_required, compute_policy_fingerprint
from policy.governance_smoke import (
    build_governance_artifact_payload,
    evaluate_governance_hello_world_artifact,
    expected_payload as governance_expected_payload,
    extract_model_json_payload,
    is_governance_hello_world_task,
)
from policy.ops_readiness import ops_readiness_status
from policy.scorer import load_rules, score_architecture
from babyai_shared.storage.artifact_store import FileArtifactStore
from babyai_shared.storage.context_store import ContextStore, InMemoryContextStore
from babyai_shared.storage.decision_status_store import DecisionStatusStore, InMemoryDecisionStatusStore
from babyai_shared.storage.idempotency import IdempotencyLock
from babyai_shared.storage.outbox_store import OutboxRecord, OutboxStore
from babyai_shared.bus.protocol import Context
from babyai.skills.registry import SkillBundle, SkillRegistry
from babyai.skills.router import SkillRouter
from babyai.skills.loader import SkillBootstrapper
from agents.registry import AgentRegistry
from agents.video_pipeline_bootstrap import bootstrap_video_pipeline
from babyai_shared.truth.loader import load_truth_pack  # MODULE-LEVEL: tests patch "bus.orchestrator_worker.load_truth_pack"
from babyai_shared.privacy.gateway import install_logging_filter

from kafka_consumer import KafkaConsumerMixin
from episode_executor import EpisodeExecutorMixin
from worker_lifecycle import WorkerLifecycleMixin, _load_episode_requested_v1_validator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph_orchestrator_worker import GraphOrchestratorWorker
from graph_orchestrator_worker import GraphOrchestratorWorker

logger = logging.getLogger(__name__)
_SERVICE_NAME = "orchestrator-worker"
_EPISODE_REQUESTED_V1_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "docs" / "schemas" / "episode_requested.v1.schema.json"


class ApprovalMissingError(RuntimeError):
    pass


class OrchestratorWorker(KafkaConsumerMixin, EpisodeExecutorMixin, WorkerLifecycleMixin):
    """
    Orchestrator with manual commit and idempotency lock.

    Coordinates:
      - Kafka consumer loop        (KafkaConsumerMixin)
      - Episode execution + eval   (EpisodeExecutorMixin)
      - Approval / dedupe          (WorkerLifecycleMixin)
    """

    def __init__(
        self,
        *,
        event_bus: KafkaEventBus,
        artifact_store: Optional[FileArtifactStore] = None,
        context_store: Optional[ContextStore] = None,
        status_store: Optional[DecisionStatusStore] = None,
        outbox_store: Optional[OutboxStore] = None,
        idempotency_lock: Optional[IdempotencyLock] = None,
        lock_renew_interval: Optional[int] = None,
        metrics_port: Optional[int] = None,
        failpoint: Optional[str] = None,
        worker_id: Optional[str] = None,
    ) -> None:
        self.event_bus = event_bus
        self.artifact_store = artifact_store or FileArtifactStore(root=_resolve_artifact_root())
        self.context_store = context_store or InMemoryContextStore()
        self.status_store = status_store or InMemoryDecisionStatusStore()
        self.outbox_store = outbox_store or OutboxStore(root=_resolve_outbox_root())
        self.idempotency_lock = idempotency_lock
        self.lock_renew_interval = lock_renew_interval
        self.failpoint = failpoint
        self._failpoint_triggered = False
        self.worker_id = worker_id or f"orchestrator-{uuid4().hex[:8]}"
        self._rules_cache: Optional[Dict[str, Any]] = None
        self._dedupe_cache: Dict[str, float] = {}
        self._dedupe_cache_lock = threading.Lock()
        self._dedupe_redis = self._resolve_dedupe_redis()
        self._policy_cache_redis = self._resolve_policy_cache_redis()
        self._skill_router = self._build_skill_router()
        self._agent_registry = AgentRegistry()
        bootstrap_video_pipeline(self._agent_registry)
        self._allow_in_memory_dedupe = _allow_in_memory_dedupe_for_env(os.getenv("ENVIRONMENT"))
        if self._dedupe_redis is None and not self._allow_in_memory_dedupe:
            raise RuntimeError("idempotency_persistent_store_required")
        self._episode_requested_v1_validator = _load_episode_requested_v1_validator()
        self._approval_tokens: Dict[str, ExecutionPermit] = {}
        self._required_policy_fingerprints: Dict[str, str] = {}
        self._pending_requested_events: Dict[str, DecisionEvent] = {}
        self._waiting_emitted: Dict[str, str] = {}
        self._decision_contexts: Dict[str, str] = {}
        self._approval_required_policy_ids = _csv_set(os.getenv("APPROVAL_REQUIRED_POLICY_IDS", "restricted"))
        self._approval_required_safety_profiles = _csv_set(os.getenv("APPROVAL_REQUIRED_SAFETY_PROFILES", ""))
        self._approval_state_lock = threading.Lock()
        self._consumer_handle: Any | None = None
        self._shutdown_event = threading.Event()
        if metrics_port is not None:
            metrics.start_metrics_server(metrics_port)
        install_logging_filter()

        # Initialize graph runtime orchestrator
        self._graph_worker = self._initialize_graph_worker()
        self._use_graph_runtime = os.getenv("USE_GRAPH_RUNTIME", "false").lower() in ("true", "1", "yes")

    def _initialize_graph_worker(self) -> Optional['GraphOrchestratorWorker']:
        """Initialize graph runtime worker if enabled"""
        try:
            from graph_orchestrator_worker import GraphOrchestratorWorker
            return GraphOrchestratorWorker(
                context_store=self.context_store,
                status_store=self.status_store,
                artifact_store=self.artifact_store,
                event_bus=self.event_bus,
                worker_id=self.worker_id
            )
        except Exception as e:
            logger.warning(f"Failed to initialize graph runtime: {e}")
            return None

    def _handle_requested_event(self, *, event: DecisionEvent, topic: str, msg: Any, consumer: Any) -> None:
        decision_id = event.decision_id
        fingerprint = self._event_fingerprint(event)
        dedupe_key = self._dedupe_key(decision_id=decision_id, fingerprint=fingerprint)
        backoff_seconds = 0
        self._emit_worker_telemetry(
            event_type="orchestrator_worker.processing_started",
            event=event,
            fingerprint=fingerprint,
            topic=topic,
        )
        self._log_milestone(
            milestone="episode_loaded",
            component="orchestrator_worker._handle_requested_event",
            event=event,
            topic=topic,
            event_type="decision.lifecycle.requested",
            fingerprint=fingerprint,
        )
        logger.info("[%s] Processing decision %s", self.worker_id, decision_id)

        try:
            # Dedupe: skip if already finalized
            status_record = self.status_store.get(decision_id)
            if status_record and status_record.status in {"completed", "failed"}:
                self._log_deduped(event=event, fingerprint=fingerprint, reason="status_final")
                consumer.commit(message=msg, asynchronous=False)
                return

            if self._hold_for_approval_if_needed(event):
                self._log_deduped(event=event, fingerprint=fingerprint, reason="waiting_for_approval")
                consumer.commit(message=msg, asynchronous=False)
                return

            if not self._dedupe_claim(dedupe_key, ttl_seconds=self._running_ttl()):
                self._log_deduped(event=event, fingerprint=fingerprint, reason="duplicate_inflight_or_processed")
                consumer.commit(message=msg, asynchronous=False)
                return

            max_attempts, backoff_seconds = self._retry_policy(event)
            attempts = self.status_store.get_attempts(decision_id)
            if attempts >= max_attempts:
                self._publish_dlq(event, error="max_attempts_exceeded", attempts=attempts)
                self.status_store.set_status(decision_id, "failed", ttl_seconds=self._final_ttl())
                self._dedupe_finalize(dedupe_key, ttl_seconds=self._final_ttl())
                consumer.commit(message=msg, asynchronous=False)
                return

            terminal = self._flush_outbox_if_terminal(event.context_id, decision_id)
            if terminal:
                self.status_store.set_status(decision_id, terminal, ttl_seconds=self._final_ttl())
                self._dedupe_finalize(dedupe_key, ttl_seconds=self._final_ttl())
                self._log_deduped(event=event, fingerprint=fingerprint, reason=f"terminal_outbox_{terminal}")
                consumer.commit(message=msg, asynchronous=False)
                return

            if self.idempotency_lock is not None:
                if self.idempotency_lock.is_locked(decision_id):
                    metrics.inc_lock_contention()
                with self.idempotency_lock.acquire(decision_id, self.worker_id) as lock:
                    self.status_store.set_status(
                        decision_id,
                        "running",
                        ttl_seconds=self._running_ttl(),
                    )
                    stop_event = threading.Event()
                    renew_thread = threading.Thread(
                        target=self._renew_loop,
                        args=(lock, stop_event),
                        daemon=True,
                    )
                    renew_thread.start()
                    try:
                        self._process_episode(event)
                    finally:
                        stop_event.set()
                        renew_thread.join(timeout=2)
            else:
                self.status_store.set_status(
                    decision_id,
                    "running",
                    ttl_seconds=self._running_ttl(),
                )
                self._process_episode(event)

            self._dedupe_finalize(dedupe_key, ttl_seconds=self._final_ttl())
            consumer.commit(message=msg, asynchronous=False)
            metrics.set_consumer_lag("orchestrator-workers", topic, 0.0)
            self._emit_worker_telemetry(
                event_type="orchestrator_worker.processing_completed",
                event=event,
                fingerprint=fingerprint,
                topic=topic,
            )
            logger.info("[%s] Completed %s", self.worker_id, decision_id)
        except Exception as e:
            self._dedupe_release(dedupe_key)
            logger.error("[%s] Failed %s", self.worker_id, decision_id, exc_info=True)
            self._emit_worker_telemetry(
                event_type="orchestrator_worker.processing_failed",
                event=event,
                fingerprint=fingerprint,
                topic=topic,
                error=str(e),
            )
            attempts = self.status_store.increment_attempts(decision_id, ttl_seconds=self._running_ttl())
            if isinstance(e, ApprovalMissingError) or "approval_missing" in str(e):
                self._publish_status(
                    event,
                    DecisionStatus.FAILED,
                    error="approval_missing",
                    metadata={"reason": "approval_missing"},
                )
                self._publish_dlq(event, error="approval_missing", attempts=attempts)
                self.status_store.set_status(decision_id, "failed", ttl_seconds=self._final_ttl())
                self._dedupe_finalize(dedupe_key, ttl_seconds=self._final_ttl())
                consumer.commit(message=msg, asynchronous=False)
                metrics.set_consumer_lag("orchestrator-workers", topic, 0.0)
                return
            if attempts >= self._retry_policy(event)[0]:
                self._publish_dlq(event, error=str(e), attempts=attempts)
                self.status_store.set_status(decision_id, "failed", ttl_seconds=self._final_ttl())
                self._dedupe_finalize(dedupe_key, ttl_seconds=self._final_ttl())
            else:
                if backoff_seconds > 0:
                    time.sleep(backoff_seconds)
                self._republish_request(event, attempts=attempts)
            consumer.commit(message=msg, asynchronous=False)
            metrics.set_consumer_lag("orchestrator-workers", topic, 0.0)

    # ── Event publishing ─────────────────────────────────────────────────────

    def _publish_status(
        self,
        event: DecisionEvent,
        status: DecisionStatus,
        *,
        iteration: Optional[int] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        metrics.record_status(status.value)
        out = DecisionEvent(
            schema_version=SCHEMA_VERSION,
            decision_id=event.decision_id,
            context_id=event.context_id,
            status=status,
            timestamp=now_iso(),
            task_ref=event.task_ref,
            truth_pack_ref=event.truth_pack_ref,
            truth_pack_version=event.truth_pack_version,
            iteration=iteration,
            error=error,
            metadata=metadata,
        )
        self._publish_event(
            context_id=event.context_id,
            topic=self._topic_name("decision_lifecycle", "decision.lifecycle"),
            key=event.decision_id,
            event=out,
        )

    def _publish_eval_result(
        self,
        *,
        event: DecisionEvent,
        iteration: int,
        passed: bool,
        score: float,
        components: Dict[str, float],
        gate_results: Dict[str, bool],
        penalties: list[str],
        failure_reasons: list[str],
        decision_ref: Optional[str] = None,
        runner_used: Optional[str] = None,
        tokens_used: Optional[int] = None,
        latency_ms: Optional[float] = None,
        training_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        logger.info(
            "eval_result_publish decision_id=%s passed=%s score=%.3f component_keys=%s "
            "gate_keys=%s failure_reasons=%s decision_ref=%s",
            event.decision_id,
            bool(passed),
            float(score),
            sorted([str(k) for k in dict(components or {}).keys()]),
            sorted([str(k) for k in dict(gate_results or {}).keys()]),
            list(failure_reasons or []),
            str(decision_ref or ""),
        )
        out = EvalResultEvent(
            schema_version=SCHEMA_VERSION,
            decision_id=event.decision_id,
            context_id=event.context_id,
            iteration=iteration,
            timestamp=now_iso(),
            passed=passed,
            score=score,
            components=components,
            gate_results=gate_results,
            penalties=penalties,
            failure_reasons=failure_reasons,
            runner_used=runner_used,
            tokens_used=tokens_used,
            latency_ms=latency_ms,
            decision_ref=decision_ref,
            training_data=training_data if isinstance(training_data, dict) else None,
        )
        self._publish_event(
            context_id=event.context_id,
            topic=self._topic_name("eval_results", "eval.results"),
            key=event.decision_id,
            event=out,
        )
        self._log_milestone(
            milestone="eval_published",
            component="orchestrator_worker._publish_eval_result",
            event=event,
            topic=self._topic_name("eval_results", "eval.results"),
            event_type="eval.results",
            passed=bool(passed),
            score=float(score),
            components_keys=sorted([str(k) for k in dict(components or {}).keys()]),
            failure_reasons=list(failure_reasons or []),
        )

    def _publish_event(
        self,
        *,
        context_id: str,
        topic: str,
        key: str,
        event: Any,
    ) -> None:
        payload = event.to_json()
        headers: Dict[str, str] = {}
        event_id = getattr(event, "event_id", None)
        content_hash = getattr(event, "content_hash", None)
        if event_id:
            headers["event_id"] = str(event_id)
        if content_hash:
            headers["content_hash"] = str(content_hash)

        record = OutboxRecord(
            event_id=str(event_id or content_hash or ""),
            topic=topic,
            payload=payload,
            headers=headers,
        )
        if record.event_id:
            self.outbox_store.add(context_id, record)

        self.event_bus.publish(
            topic=topic,
            key=key,
            value=payload,
            headers=headers if headers else None,
        )

        if record.event_id:
            self.outbox_store.mark_sent(context_id, record.event_id)

    def _flush_outbox_if_terminal(self, context_id: str, decision_id: str) -> Optional[str]:
        pending = self.outbox_store.pending(context_id)
        if not pending:
            return None

        terminal_status: Optional[str] = None
        decision_topic = self._topic_name("decision_lifecycle", "decision.lifecycle")
        for record in pending:
            self.event_bus.publish(
                topic=record.topic,
                key=decision_id,
                value=record.payload,
                headers=record.headers or None,
            )
            self.outbox_store.mark_sent(context_id, record.event_id)

            if record.topic == decision_topic:
                try:
                    ev = DecisionEvent.from_json(record.payload)
                except Exception:
                    ev = None
                if ev and ev.status in {DecisionStatus.COMPLETED, DecisionStatus.FAILED}:
                    terminal_status = "completed" if ev.status == DecisionStatus.COMPLETED else "failed"

        return terminal_status

    def _republish_request(self, event: DecisionEvent, *, attempts: int) -> None:
        metadata = dict(event.metadata or {})
        metadata["attempts"] = attempts
        out = DecisionEvent(
            schema_version=SCHEMA_VERSION,
            decision_id=event.decision_id,
            context_id=event.context_id,
            status=DecisionStatus.REQUESTED,
            timestamp=now_iso(),
            task_ref=event.task_ref,
            truth_pack_ref=event.truth_pack_ref,
            truth_pack_version=event.truth_pack_version,
            iteration=event.iteration,
            metadata=metadata,
        )
        self._publish_event(
            context_id=event.context_id,
            topic=self._topic_name("decision_lifecycle", "decision.lifecycle"),
            key=event.decision_id,
            event=out,
        )

    def _publish_dlq(self, event: DecisionEvent, *, error: str, attempts: int) -> None:
        metadata = dict(event.metadata or {})
        metadata["attempts"] = attempts
        metadata["error"] = error
        out = DecisionEvent(
            schema_version=SCHEMA_VERSION,
            decision_id=event.decision_id,
            context_id=event.context_id,
            status=DecisionStatus.FAILED,
            timestamp=now_iso(),
            task_ref=event.task_ref,
            truth_pack_ref=event.truth_pack_ref,
            truth_pack_version=event.truth_pack_version,
            iteration=event.iteration,
            error=error,
            metadata=metadata,
        )
        self._publish_event(
            context_id=event.context_id,
            topic=self._topic_name("decision_lifecycle_dlq", "decision.lifecycle.dlq"),
            key=event.decision_id,
            event=out,
        )
        metrics.inc_dlq_published(reason="decision_failed")
        self._log_dlq_publish(
            episode_id=str(event.decision_id),
            fingerprint=self._event_fingerprint(event),
            reason=str(error),
            topic=self._topic_name("decision_lifecycle_dlq", "decision.lifecycle.dlq"),
        )
        self._emit_worker_telemetry(
            event_type="orchestrator_worker.dlq_published",
            event=event,
            fingerprint=self._event_fingerprint(event),
            topic=self._topic_name("decision_lifecycle_dlq", "decision.lifecycle.dlq"),
            error=str(error),
            attempts=int(attempts),
        )

    def _publish_invalid_event_dlq(
        self,
        *,
        source_topic: str,
        raw_payload: str,
        reason: str,
        decision_id: str | None = None,
        context_id: str | None = None,
    ) -> None:
        topic = self._topic_name("decision_lifecycle_dlq", "decision.lifecycle.dlq")
        payload: Dict[str, Any] = {
            "schema_version": 1,
            "event_type": "InvalidEvent",
            "violation_type": "PolicyViolation",
            "reason": str(reason),
            "source_topic": str(source_topic),
            "timestamp": now_iso(),
            "raw_payload": str(raw_payload),
        }
        if isinstance(decision_id, str) and decision_id.strip():
            payload["decision_id"] = decision_id.strip()
        if isinstance(context_id, str) and context_id.strip():
            payload["context_id"] = context_id.strip()
        self.event_bus.publish(
            topic=topic,
            key=str(decision_id or "invalid-event"),
            value=json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        )
        metrics.inc_dlq_published(reason="invalid_event")
        correlation = self._correlation_context_from_raw_payload(
            raw_payload=raw_payload,
            decision_id=decision_id,
            trace_id=self._trace_id_from_raw_payload(raw_payload),
        )
        self._log_dlq_publish(
            episode_id=str(correlation.get("episode_id") or "unknown"),
            fingerprint=str(correlation.get("fingerprint") or ""),
            reason=str(reason),
            topic=str(topic),
        )
        logger.info(
            "telemetry=%s",
            json.dumps(
                {
                    "event_type": "orchestrator_worker.dlq_published",
                    "reason": str(reason),
                    "source_topic": str(source_topic),
                    "topic": str(topic),
                    **correlation,
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
        )

    # ── Skill bundle ─────────────────────────────────────────────────────────

    def _build_skill_router(self) -> SkillRouter | None:
        redis_client = self._policy_cache_redis
        if redis_client is None:
            return None
        async_redis = _AsyncRedisAdapter(redis_client)
        registry = SkillRegistry(redis_client=async_redis, state_manager=None)
        SkillBootstrapper(registry).bootstrap()
        return SkillRouter(registry=registry, redis_client=async_redis)

    def _resolve_skill_bundle(
        self,
        *,
        event: DecisionEvent,
        metadata: Mapping[str, Any],
        task: Mapping[str, Any],
    ) -> SkillBundle | None:
        if self._skill_router is None:
            return None
        domain = self._resolve_skill_domain(event=event, metadata=metadata, task=task)
        if not domain:
            return None
        try:
            bundle = _run_coro_sync(self._skill_router.resolve(domain=domain))
        except Exception as exc:
            logger.warning(
                "skill_bundle_resolve_failed decision_id=%s domain=%s error=%s",
                event.decision_id,
                domain,
                exc,
            )
            return None
        if isinstance(bundle, SkillBundle) and not bundle.is_empty:
            return bundle
        if isinstance(bundle, SkillBundle):
            return bundle
        return None

    def _resolve_skill_domain(
        self,
        *,
        event: DecisionEvent,
        metadata: Mapping[str, Any],
        task: Mapping[str, Any],
    ) -> str:
        effective_policy = metadata.get("effective_policy")
        effective_policy_map = effective_policy if isinstance(effective_policy, Mapping) else {}
        for candidate in (
            effective_policy_map.get("domain_name"),
            effective_policy_map.get("domain"),
            metadata.get("domain_name"),
            metadata.get("domain"),
            task.get("domain_name"),
            task.get("domain"),
            event.context_id,
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    # ── Telemetry and logging ────────────────────────────────────────────────

    def _event_fingerprint(self, event: DecisionEvent) -> str:
        raw = str(getattr(event, "content_hash", "") or "").strip().lower()
        if len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw):
            return raw
        payload = {
            "schema_version": int(event.schema_version),
            "decision_id": str(event.decision_id),
            "context_id": str(event.context_id),
            "status": str(event.status.value),
            "task_ref": str(event.task_ref),
            "truth_pack_ref": str(event.truth_pack_ref),
            "truth_pack_version": str(event.truth_pack_version),
            "iteration": event.iteration,
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()

    def _emit_worker_telemetry(
        self,
        *,
        event_type: str,
        event: DecisionEvent,
        fingerprint: str,
        topic: str,
        **extra: Any,
    ) -> None:
        row = self._correlation_context(event=event, fingerprint=fingerprint)
        row.update(
            {
                "event_type": str(event_type),
                "topic": str(topic),
                "decision_id": str(event.decision_id),
                "context_id": str(event.context_id),
                "worker_id": str(self.worker_id),
            }
        )
        for key, value in extra.items():
            if value is not None:
                row[str(key)] = value
        logger.info("telemetry=%s", json.dumps(row, ensure_ascii=True, sort_keys=True))

    def _log_dlq_publish(
        self,
        *,
        episode_id: str,
        fingerprint: str,
        reason: str,
        topic: str,
    ) -> None:
        logger.info(
            "telemetry=%s",
            json.dumps(
                {
                    "event_type": "dlq_publish",
                    "episode_id": str(episode_id or "unknown"),
                    "fingerprint": str(fingerprint or ""),
                    "reason": str(reason or "unknown"),
                    "topic": str(topic or ""),
                },
                ensure_ascii=True,
                sort_keys=True,
            ),
        )

    def _milestone_base(
        self, *, event: DecisionEvent, topic: str = "", event_type: str = "", fingerprint: str = ""
    ) -> Dict[str, str]:
        event_fingerprint = str(fingerprint or self._event_fingerprint(event))
        trace_id = self._trace_id_from_metadata(event.metadata)
        event_id = str(
            getattr(event, "event_id", "") or getattr(event, "content_hash", "") or event_fingerprint
        )
        return {
            "service_name": _SERVICE_NAME,
            "decision_id": str(event.decision_id),
            "context_id": str(event.context_id),
            "episode_id": str(event.decision_id),
            "event_type": str(event_type or event.status.value),
            "topic": str(topic),
            "fingerprint": event_fingerprint,
            "event_id": event_id,
            "trace_id": trace_id,
        }

    def _log_milestone(
        self,
        *,
        milestone: str,
        component: str,
        event: DecisionEvent,
        topic: str = "",
        event_type: str = "",
        fingerprint: str = "",
        **extra: Any,
    ) -> None:
        payload = self._milestone_base(event=event, topic=topic, event_type=event_type, fingerprint=fingerprint)
        payload["component"] = str(component)
        payload.update(extra)
        log_milestone(
            logger,
            str(milestone),
            **payload,
        )

    def _correlation_context(self, *, event: DecisionEvent, fingerprint: str) -> Dict[str, str]:
        event_id = str(getattr(event, "event_id", "") or getattr(event, "content_hash", "") or fingerprint)
        trace_id = self._trace_id_from_metadata(event.metadata)
        return {
            "episode_id": str(event.decision_id),
            "fingerprint": str(fingerprint),
            "event_id": event_id,
            "trace_id": trace_id,
        }

    def _correlation_context_from_raw_payload(
        self,
        *,
        raw_payload: str,
        decision_id: str | None,
        trace_id: str,
    ) -> Dict[str, str]:
        payload_fingerprint = sha256(str(raw_payload).encode("utf-8", errors="replace")).hexdigest()
        return {
            "episode_id": str(decision_id or "unknown"),
            "fingerprint": payload_fingerprint,
            "event_id": payload_fingerprint,
            "trace_id": str(trace_id),
        }

    def _trace_id_from_raw_payload(self, raw_payload: str) -> str:
        try:
            decoded = json.loads(raw_payload)
        except Exception:
            return ""
        if isinstance(decoded, dict):
            raw_trace = decoded.get("trace_id")
            if isinstance(raw_trace, str) and raw_trace.strip():
                return raw_trace.strip()
            meta = decoded.get("metadata")
            if isinstance(meta, dict):
                return self._trace_id_from_metadata(meta)
        return ""

    @staticmethod
    def _trace_id_from_metadata(metadata: Any) -> str:
        if not isinstance(metadata, dict):
            return ""
        for key in ("trace_id", "traceId", "x_trace_id"):
            raw = metadata.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
        return ""


# ── Module-level helpers ─────────────────────────────────────────────────────

class _AsyncRedisAdapter:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def get(self, key: str) -> Any:
        return self._client.get(key)

    async def setex(self, key: str, ttl_seconds: int, value: str) -> Any:
        return self._client.setex(key, int(ttl_seconds), value)

    async def sadd(self, key: str, *values: Any) -> Any:
        return self._client.sadd(key, *values)

    async def smembers(self, key: str) -> Any:
        return self._client.smembers(key)


def _run_coro_sync(coro: Any) -> Any:
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None

    if running_loop is None or not running_loop.is_running():
        return asyncio.run(coro)

    holder: Dict[str, Any] = {}
    error_holder: Dict[str, BaseException] = {}

    def _worker() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            holder["result"] = loop.run_until_complete(coro)
        except BaseException as exc:
            error_holder["error"] = exc
        finally:
            loop.close()

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in error_holder:
        raise error_holder["error"]
    return holder.get("result")


def _allow_in_memory_dedupe_for_env(raw_environment: str | None) -> bool:
    env = str(raw_environment or "").strip().lower()
    if env in {"prod", "production"}:
        return False
    return True


def _resolve_artifact_root() -> Path:
    raw = str(os.getenv("ARTIFACT_DIR", "") or "").strip()
    if raw:
        return Path(raw)
    container_root = Path("/app/artifacts")
    if container_root.exists():
        return container_root
    return Path("artifacts")


def _resolve_outbox_root() -> Path:
    raw = str(os.getenv("OUTBOX_DIR", "") or "").strip()
    if raw:
        return Path(raw)
    container_root = Path("/app/outbox")
    if container_root.exists():
        return container_root
    return Path("outbox")


def _csv_set(raw: str | None) -> set[str]:
    items = set()
    for item in str(raw or "").split(","):
        value = item.strip().lower()
        if value:
            items.add(value)
    return items


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed >= 0 else None


def _optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except Exception:
        return None
    return parsed if parsed >= 0 else None


def _clip_text(value: Any, *, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "...<truncated>"


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out
