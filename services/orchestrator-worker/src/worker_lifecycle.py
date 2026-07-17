"""
WorkerLifecycleMixin — approval, dedupe, idempotency and policy cache for OrchestratorWorker.

Handles: approval flow, dedupe cache, policy fingerprint, lock renewal.

NOTE: load_truth_pack is imported lazily from orchestrator_worker inside _retry_policy.
This preserves monkeypatch compatibility:
    monkeypatch.setattr("orchestrator_worker.load_truth_pack", mock)
works correctly because the lazy import always fetches the current module-level name.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional

from jsonschema import Draft202012Validator

from services.aesa.domain.approval import ExecutionPermit, require_execution_permit_from_mapping
from bus.event_schemas import ApprovalEvent, DecisionEvent, DecisionStatus, SCHEMA_VERSION, now_iso
from bus import metrics
from policy.approval_gate import approval_required, compute_policy_fingerprint

logger = logging.getLogger("orchestrator_worker")

_EPISODE_REQUESTED_V1_SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "docs" / "schemas" / "episode_requested.v1.schema.json"
)


class WorkerLifecycleMixin:
    """
    Approval flow, dedupe, idempotency, and policy cache.  Requires the following
    attributes on self:
      artifact_store, event_bus, status_store, _dedupe_redis, _dedupe_cache,
      _dedupe_cache_lock, _allow_in_memory_dedupe, _approval_tokens,
      _required_policy_fingerprints, _pending_requested_events, _waiting_emitted,
      _decision_contexts, _approval_required_policy_ids, _approval_required_safety_profiles,
      _approval_state_lock, _policy_cache_redis, _episode_requested_v1_validator,
      idempotency_lock, lock_renew_interval, failpoint, _failpoint_triggered, worker_id
    and the methods: _publish_event, _publish_status, _topic_name, _log_milestone,
    _emit_worker_telemetry, _correlation_context, _trace_id_from_metadata.
    """

    # ── Approval flow ────────────────────────────────────────────────────────

    def _handle_approval_event(self, *, approval_event: ApprovalEvent) -> None:
        decision_id = str(approval_event.decision_id or "").strip()
        policy_fingerprint = str(approval_event.policy_fingerprint or "").strip().lower()
        approved_by = str(approval_event.approved_by or "").strip()
        approved_at = str(approval_event.approved_at or "").strip()
        approved = bool(getattr(approval_event, "approved", True))
        if not decision_id or not policy_fingerprint or not approved_by or not approved_at:
            self._publish_invalid_event_dlq(
                source_topic=self._topic_name("decision_approval", "decision.approval"),
                raw_payload=approval_event.to_json(),
                reason="approval_event_invalid_fields",
                decision_id=decision_id or None,
            )
            return

        with self._approval_state_lock:
            required_fingerprint = str(
                self._required_policy_fingerprints.get(decision_id) or ""
            ).strip().lower()
            if required_fingerprint and required_fingerprint != policy_fingerprint:
                self._publish_invalid_event_dlq(
                    source_topic=self._topic_name("decision_approval", "decision.approval"),
                    raw_payload=approval_event.to_json(),
                    reason=(
                        "approval_policy_fingerprint_mismatch:"
                        f"expected={required_fingerprint}:got={policy_fingerprint}"
                    ),
                    decision_id=decision_id,
                    context_id=self._decision_contexts.get(decision_id),
                )
                return

            pending_event = self._pending_requested_events.get(decision_id)
            context_id = str(
                self._decision_contexts.get(decision_id)
                or (pending_event.context_id if pending_event is not None else "")
                or str(getattr(approval_event, "context_id", "") or "")
                or "approval"
            )

            if not approved:
                self._approval_tokens.pop(decision_id, None)
                self._pending_requested_events.pop(decision_id, None)
                self._waiting_emitted.pop(decision_id, None)
                if pending_event is not None:
                    denied_metadata = dict(pending_event.metadata or {})
                    denied_metadata["approval_required"] = True
                    denied_metadata["approval_denied"] = True
                    denied_metadata["approval_denied_by"] = approved_by
                    denied_metadata["approval_denied_at"] = approved_at
                    denied_metadata["approval_denied_reason"] = (
                        str(approval_event.reason or "").strip() or None
                    )
                    denied_metadata["policy_fingerprint"] = policy_fingerprint
                    self._publish_status(
                        pending_event,
                        DecisionStatus.FAILED,
                        error="approval_denied",
                        metadata=denied_metadata,
                    )
                    self.status_store.set_status(decision_id, "failed", ttl_seconds=self._final_ttl())
                return

            permit = ExecutionPermit(
                decision_id=decision_id,
                policy_fingerprint=policy_fingerprint,
                approved_by=approved_by,
                approved_at=approved_at,
                reason=str(approval_event.reason or "").strip() or None,
            )
            self._approval_tokens[decision_id] = permit

        self._persist_approval_token(permit=permit, context_id=context_id)
        if pending_event is not None:
            self._republish_requested_with_permit(event=pending_event, permit=permit)

    def _handle_policy_approved_event(self, *, event: Dict[str, Any]) -> None:
        session_id = str(event.get("session_id") or "").strip()
        domain_name = str(event.get("domain_name") or "").strip()
        fingerprint = str(event.get("fingerprint") or "").strip().lower()
        effective_policy = event.get("effective_policy")

        if not session_id or not domain_name or not fingerprint or not isinstance(effective_policy, dict):
            logger.warning(
                "policy_approved_missing_fields session_id=%s domain_name=%s has_effective_policy=%s",
                session_id,
                domain_name,
                isinstance(effective_policy, dict),
            )
            return

        if self._policy_cache_redis is None:
            logger.warning(
                "policy_approved_cache_unavailable domain=%s session_id=%s", domain_name, session_id
            )
            return

        cache_key = f"policy_bootstrap:effective_policy:{domain_name}"
        normalized_policy = self._normalize_to_effective_policy_v1(event=event)
        payload = {
            "effective_policy": dict(normalized_policy),
            "raw_effective_policy": dict(effective_policy),
            "fingerprint": fingerprint,
            "session_id": session_id,
            "approved_at": event.get("approved_at"),
            "source": "policy_bootstrap",
        }
        try:
            self._policy_cache_redis.setex(
                cache_key,
                60 * 60 * 24 * 7,
                json.dumps(payload, ensure_ascii=True, sort_keys=True),
            )
        except Exception as exc:
            logger.warning(
                "policy_approved_cache_write_failed domain=%s session_id=%s error=%s",
                domain_name,
                session_id,
                str(exc),
            )
            return
        logger.info(
            "policy_approved_cached domain=%s fingerprint=%s session_id=%s",
            domain_name,
            fingerprint[:12],
            session_id,
        )

    def _normalize_to_effective_policy_v1(self, *, event: Dict[str, Any]) -> Dict[str, Any]:
        raw = event.get("effective_policy")
        payload = dict(raw) if isinstance(raw, dict) else {}
        fingerprint = str(event.get("fingerprint") or "").strip().lower()
        domain_name = str(event.get("domain_name") or "").strip()
        approval_values = payload.get("approval_required")
        approval_required_flag = False
        if isinstance(approval_values, bool):
            approval_required_flag = approval_values
        elif isinstance(approval_values, list):
            approval_required_flag = len(approval_values) > 0
        elif isinstance(approval_values, str):
            approval_required_flag = bool(approval_values.strip())

        write_scope_payload = payload.get("write_scope")
        write_scope_type = "policy_service"
        if isinstance(write_scope_payload, dict):
            candidate = write_scope_payload.get("type")
            if isinstance(candidate, str) and candidate.strip():
                write_scope_type = candidate.strip()
        constraints_payload = payload.get("constraints")
        if isinstance(constraints_payload, dict) and write_scope_type == "policy_service":
            nested_scope = constraints_payload.get("write_scope")
            if isinstance(nested_scope, dict):
                candidate = nested_scope.get("type")
                if isinstance(candidate, str) and candidate.strip():
                    write_scope_type = candidate.strip()

        constraints: Dict[str, Any] = dict(constraints_payload) if isinstance(constraints_payload, dict) else {}
        constraints["approval_required"] = bool(approval_required_flag)
        model_profile = str(payload.get("model_profile") or "").strip() or "general"
        safety_profile = str(payload.get("safety_profile") or "").strip() or "balanced"
        quality_profile = payload.get("quality_profile")
        quality_profile_obj = dict(quality_profile) if isinstance(quality_profile, dict) else {"preset": "balanced"}

        return {
            "schema_version": "effective_policy.v1",
            "version": "effective_policy.v1",
            "policy_id": str(fingerprint or "policy-bootstrap"),
            "policy_version": 1,
            "domain_name": domain_name,
            "domain_description": str(payload.get("domain_description") or ""),
            "authoritative_sources": _as_string_list(payload.get("authoritative_sources")),
            "autonomous_actions": _as_string_list(payload.get("autonomous_actions")),
            "forbidden_outputs": _as_string_list(payload.get("forbidden_outputs")),
            "target_user_context": str(payload.get("target_user_context") or ""),
            "write_scope": {"type": write_scope_type},
            "quality_profile": quality_profile_obj,
            "safety_profile": safety_profile,
            "model_profile": model_profile,
            "constraints": constraints,
        }

    def _hold_for_approval_if_needed(self, event: DecisionEvent) -> bool:
        if not self._approval_required_for_event(event):
            self._log_milestone(
                milestone="approval_state_checked",
                component="orchestrator_worker._hold_for_approval_if_needed",
                event=event,
                topic=self._topic_name("decision_lifecycle", "decision.lifecycle"),
                event_type="approval.state",
                state="not_required",
            )
            return False

        decision_id = str(event.decision_id)
        policy_fingerprint = self._policy_fingerprint_for_event(event)
        with self._approval_state_lock:
            if policy_fingerprint:
                self._required_policy_fingerprints[decision_id] = policy_fingerprint
            self._decision_contexts[decision_id] = str(event.context_id)

            metadata = dict(event.metadata or {})
            inline_permit_payload = metadata.get("execution_permit") or metadata.get("approval_token")
            if isinstance(inline_permit_payload, dict):
                try:
                    inline_permit = require_execution_permit_from_mapping(
                        inline_permit_payload,
                        decision_id=decision_id,
                        policy_fingerprint=policy_fingerprint or None,
                    )
                except Exception:
                    inline_permit = None
                if inline_permit is not None:
                    self._approval_tokens[decision_id] = inline_permit

            token = self._approval_tokens.get(decision_id)
            if token is not None and (
                (not policy_fingerprint) or token.policy_fingerprint == str(policy_fingerprint).strip().lower()
            ):
                self._log_milestone(
                    milestone="approval_state_checked",
                    component="orchestrator_worker._hold_for_approval_if_needed",
                    event=event,
                    topic=self._topic_name("decision_lifecycle", "decision.lifecycle"),
                    event_type="approval.state",
                    fingerprint=str(policy_fingerprint or token.policy_fingerprint),
                    state="approved",
                    approved_by=str(token.approved_by),
                )
                metadata["execution_permit"] = token.to_dict()
                metadata["approval_token"] = token.to_dict()
                metadata["approval_granted"] = True
                if policy_fingerprint:
                    metadata["policy_fingerprint"] = policy_fingerprint
                event.metadata = metadata
                self._pending_requested_events.pop(decision_id, None)
                self._waiting_emitted.pop(decision_id, None)
                return False

            self._pending_requested_events[decision_id] = event
            waiting_marker = str(self._waiting_emitted.get(decision_id) or "")
            marker = str(policy_fingerprint or "")
            if waiting_marker == marker:
                self._log_milestone(
                    milestone="approval_state_checked",
                    component="orchestrator_worker._hold_for_approval_if_needed",
                    event=event,
                    topic=self._topic_name("decision_lifecycle", "decision.lifecycle"),
                    event_type="approval.state",
                    fingerprint=str(policy_fingerprint),
                    state="waiting_cached",
                )
                return True
            self._waiting_emitted[decision_id] = marker

        waiting_metadata = dict(event.metadata or {})
        waiting_metadata["approval_required"] = True
        waiting_metadata["wait_reason"] = "awaiting_decision_approval"
        if policy_fingerprint:
            waiting_metadata["policy_fingerprint"] = policy_fingerprint
        self._publish_status(
            event,
            DecisionStatus.WAITING_FOR_APPROVAL,
            metadata=waiting_metadata,
        )
        self._log_milestone(
            milestone="approval_state_checked",
            component="orchestrator_worker._hold_for_approval_if_needed",
            event=event,
            topic=self._topic_name("decision_lifecycle", "decision.lifecycle"),
            event_type="approval.state",
            fingerprint=str(policy_fingerprint),
            state="waiting_for_approval",
        )
        return True

    def _approval_required_for_event(self, event: DecisionEvent) -> bool:
        metadata = dict(event.metadata or {})
        effective_policy = metadata.get("effective_policy")
        constraints = metadata.get("policy_constraints")
        policy_preset = metadata.get("policy_preset")
        return approval_required(
            effective_policy=effective_policy if isinstance(effective_policy, dict) else None,
            policy_constraints=constraints if isinstance(constraints, dict) else None,
            policy_preset=str(policy_preset or ""),
            required_policy_ids=self._approval_required_policy_ids,
            required_safety_profiles=self._approval_required_safety_profiles,
        )

    def _policy_fingerprint_for_event(self, event: DecisionEvent) -> str:
        metadata = dict(event.metadata or {})
        raw = str(metadata.get("policy_fingerprint") or "").strip().lower()
        if len(raw) == 64 and all(ch in "0123456789abcdef" for ch in raw):
            return raw
        effective_policy = metadata.get("effective_policy")
        if isinstance(effective_policy, dict) and effective_policy:
            return compute_policy_fingerprint(effective_policy)
        return ""

    def _persist_approval_token(self, *, permit: ExecutionPermit, context_id: str) -> None:
        payload = {
            "schema_version": 1,
            "event_type": "DecisionApprovalToken",
            "decision_id": permit.decision_id,
            "policy_fingerprint": permit.policy_fingerprint,
            "approved_by": permit.approved_by,
            "approved_at": permit.approved_at,
            "reason": permit.reason,
        }
        try:
            self.artifact_store.put(
                json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"),
                context_id=str(context_id or "approval"),
                name=f"approval:{permit.decision_id}",
                metadata={"type": "approval"},
            )
        except Exception:
            logger.exception("approval_token_persist_failed decision_id=%s", permit.decision_id)

    def _republish_requested_with_permit(self, *, event: DecisionEvent, permit: ExecutionPermit) -> None:
        metadata = dict(event.metadata or {})
        metadata["execution_permit"] = permit.to_dict()
        metadata["approval_token"] = permit.to_dict()
        metadata["approval_granted"] = True
        metadata["approval_granted_at"] = permit.approved_at
        metadata["approval_granted_by"] = permit.approved_by
        metadata["policy_fingerprint"] = permit.policy_fingerprint
        out = DecisionEvent(
            schema_version=SCHEMA_VERSION,
            decision_id=event.decision_id,
            context_id=event.context_id,
            status=DecisionStatus.REQUESTED,
            timestamp=now_iso(),
            task_ref=event.task_ref,
            truth_pack_ref=event.truth_pack_ref,
            truth_pack_version=event.truth_pack_version,
            metadata=metadata,
        )
        self._publish_event(
            context_id=event.context_id,
            topic=self._topic_name("decision_lifecycle", "decision.lifecycle"),
            key=event.decision_id,
            event=out,
        )

    # ── Dedupe ───────────────────────────────────────────────────────────────

    @staticmethod
    def _dedupe_key(*, decision_id: str, fingerprint: str) -> str:
        return f"episode:{str(decision_id)}:fingerprint:{str(fingerprint)}"

    def _dedupe_claim(self, key: str, *, ttl_seconds: int) -> bool:
        ttl = max(1, int(ttl_seconds))
        if self._dedupe_redis is not None:
            redis_key = f"dedupe:{key}"
            try:
                return bool(self._dedupe_redis.set(redis_key, "1", nx=True, ex=ttl))
            except Exception:
                if self._allow_in_memory_dedupe:
                    return self._dedupe_claim_in_memory(key, ttl=ttl)
                raise RuntimeError("idempotency_store_unavailable")
        if self._allow_in_memory_dedupe:
            return self._dedupe_claim_in_memory(key, ttl=ttl)
        raise RuntimeError("idempotency_persistent_store_required")

    def _dedupe_finalize(self, key: str, *, ttl_seconds: int) -> None:
        ttl = max(1, int(ttl_seconds))
        if self._dedupe_redis is not None:
            redis_key = f"dedupe:{key}"
            try:
                if not self._dedupe_redis.expire(redis_key, ttl):
                    self._dedupe_redis.set(redis_key, "1", ex=ttl)
                return
            except Exception:
                pass
        with self._dedupe_cache_lock:
            self._dedupe_cache[key] = time.time() + ttl

    def _dedupe_release(self, key: str) -> None:
        if self._dedupe_redis is not None:
            redis_key = f"dedupe:{key}"
            try:
                self._dedupe_redis.delete(redis_key)
                return
            except Exception:
                pass
        with self._dedupe_cache_lock:
            self._dedupe_cache.pop(key, None)

    def _dedupe_claim_in_memory(self, key: str, *, ttl: int) -> bool:
        now = time.time()
        with self._dedupe_cache_lock:
            expired = [k for k, until in self._dedupe_cache.items() if until <= now]
            for old in expired:
                self._dedupe_cache.pop(old, None)
            current = self._dedupe_cache.get(key)
            if current is not None and current > now:
                return False
            self._dedupe_cache[key] = now + ttl
            return True

    def _log_deduped(self, *, event: DecisionEvent, fingerprint: str, reason: str) -> None:
        self._log_milestone(
            milestone="dedupe_hit",
            component="orchestrator_worker._log_deduped",
            event=event,
            topic=self._topic_name("decision_lifecycle", "decision.lifecycle"),
            event_type="dedupe",
            fingerprint=fingerprint,
            reason=str(reason),
        )
        row = self._correlation_context(event=event, fingerprint=fingerprint)
        row.update(
            {
                "event_type": "orchestrator_worker.dedupe",
                "deduped": True,
                "reason": str(reason),
                "decision_id": str(event.decision_id),
                "context_id": str(event.context_id),
            }
        )
        logger.info("telemetry=%s", json.dumps(row, ensure_ascii=True, sort_keys=True))

    # ── Lifecycle helpers ────────────────────────────────────────────────────

    def _renew_loop(self, lock: Any, stop_event: threading.Event) -> None:
        interval = self.lock_renew_interval
        if interval is None:
            if self.idempotency_lock is not None:
                interval = max(5, int(self.idempotency_lock.ttl * 0.5))
            else:
                interval = 30
        while not stop_event.wait(interval):
            ok = False
            try:
                ok = lock.renew()
            except Exception:
                ok = False
            if not ok:
                logger.warning("[%s] Failed to renew lock", self.worker_id)

    def _maybe_failpoint(self, point: str) -> None:
        if self.failpoint != point:
            return
        if self._failpoint_triggered:
            return
        self._failpoint_triggered = True
        raise KeyboardInterrupt(f"Failpoint triggered: {point}")

    def _retry_policy(self, event: DecisionEvent) -> tuple[int, int]:
        # Lazy import preserves monkeypatch("orchestrator_worker.load_truth_pack", ...)
        import orchestrator_worker as _ow
        max_attempts = int(self.event_bus.config.get("consumer", {}).get("retry_max_attempts", 3))
        backoff_seconds = int(self.event_bus.config.get("consumer", {}).get("retry_backoff_seconds", 5))
        try:
            truth_pack = _ow.load_truth_pack(event.truth_pack_ref)
            retries = truth_pack.get("retries", {}) if isinstance(truth_pack, dict) else {}
            if isinstance(retries, dict):
                max_attempts = int(retries.get("max_attempts", max_attempts))
                backoff_seconds = int(retries.get("backoff_seconds", backoff_seconds))
        except Exception:
            pass
        return max_attempts, backoff_seconds

    def _running_ttl(self) -> int:
        return int(self.event_bus.config.get("dedupe", {}).get("running_ttl_seconds", 1800))

    def _final_ttl(self) -> int:
        return int(self.event_bus.config.get("dedupe", {}).get("final_ttl_seconds", 86400))

    def _validate_episode_requested_v1(self, event: DecisionEvent) -> None:
        payload = {
            "schema_version": 1,
            "event_type": "EpisodeRequested",
            "episode_id": str(event.decision_id),
            "request_fingerprint": self._event_fingerprint(event),
            "timestamp": str(event.timestamp),
            "task_id": str(event.task_ref),
            "context_id": str(event.context_id),
        }
        self._episode_requested_v1_validator.validate(payload)

    # ── Redis resolvers ──────────────────────────────────────────────────────

    def _resolve_dedupe_redis(self) -> Any | None:
        lock_redis = getattr(self.idempotency_lock, "redis", None)
        if lock_redis is not None:
            return lock_redis
        status_redis = getattr(self.status_store, "redis", None)
        if status_redis is not None:
            return status_redis
        return None

    def _resolve_policy_cache_redis(self) -> Any | None:
        if self._dedupe_redis is not None:
            return self._dedupe_redis
        redis_url = str(os.getenv("REDIS_URL", "")).strip()
        if not redis_url:
            return None
        try:
            import redis  # type: ignore
        except Exception:
            return None
        try:
            client = redis.Redis.from_url(redis_url)
            client.ping()
            return client
        except Exception:
            return None


# ── Module-level helpers ─────────────────────────────────────────────────────

def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _load_episode_requested_v1_validator() -> Draft202012Validator:
    schema = json.loads(_EPISODE_REQUESTED_V1_SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)
