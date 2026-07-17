from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Mapping

from babyai_shared.bus.event_schemas import ApprovalEvent, SCHEMA_VERSION, now_iso
from babyai_shared.core.logging_milestones import log_milestone
from application.use_cases import ValidateAndEnqueueDecisionRequest
from infrastructure import (
    HttpPolicyValidatorAdapter,
    KafkaApprovalPublisher,
    KafkaDecisionRequestedConsumer,
    KafkaDlqPublisher,
    KafkaLifecycleApprovalObserver,
    KafkaLifecyclePublisher,
    RedisDedupeStore,
    RedisPendingApprovalStore,
)

try:
    from fastapi import FastAPI, HTTPException, Request
    import uvicorn
except Exception:  # pragma: no cover - optional dependency
    FastAPI = None  # type: ignore[assignment]
    HTTPException = RuntimeError  # type: ignore[assignment]
    Request = object  # type: ignore[assignment]
    uvicorn = None  # type: ignore[assignment]


logger = logging.getLogger(__name__)
_SERVICE_NAME = "request-gate"
_COMPONENT_RUNTIME = "main.runtime"
_COMPONENT_API = "main.api"


class RequestGateRuntime:
    def __init__(self) -> None:
        bootstrap_servers = str(
            os.getenv("REQUEST_GATE_BOOTSTRAP_SERVERS")
            or os.getenv("KAFKA_BOOTSTRAP_SERVERS")
            or os.getenv("KAFKA_BROKERS")
            or "kafka:9092"
        )
        lifecycle_topic = str(os.getenv("REQUEST_GATE_LIFECYCLE_TOPIC", "decision.lifecycle"))
        dlq_topic = str(os.getenv("REQUEST_GATE_DLQ_TOPIC", "decision.requested.dlq"))
        requested_topic = str(os.getenv("REQUEST_GATE_INPUT_TOPIC", "decision.requested"))
        approval_topic = str(os.getenv("REQUEST_GATE_APPROVAL_TOPIC", "decision.approval"))
        group_id = str(os.getenv("REQUEST_GATE_GROUP_ID", "request-gate"))
        poll_timeout = float(os.getenv("REQUEST_GATE_POLL_TIMEOUT_SECONDS", "1.0"))
        dedupe_ttl_seconds = int(os.getenv("REQUEST_GATE_DEDUPE_TTL_SECONDS", "86400"))
        redis_url = os.getenv("REQUEST_GATE_REDIS_URL", "redis://redis:6379/1")
        allow_in_memory_dedupe = _env_bool("REQUEST_GATE_ALLOW_IN_MEMORY_DEDUPE", default=True)
        self._idle_sleep_seconds = float(os.getenv("REQUEST_GATE_IDLE_SLEEP_SECONDS", "0.2"))
        observer_group_id = str(os.getenv("REQUEST_GATE_LIFECYCLE_OBSERVER_GROUP_ID", "request-gate-approvals"))
        pending_ttl_seconds = int(os.getenv("REQUEST_GATE_PENDING_APPROVAL_TTL_SECONDS", "86400"))

        dedupe_store = RedisDedupeStore(
            redis_url=redis_url,
            allow_in_memory_fallback=allow_in_memory_dedupe,
        )
        lifecycle_publisher = KafkaLifecyclePublisher(
            bootstrap_servers=bootstrap_servers,
            topic=lifecycle_topic,
        )
        dlq_publisher = KafkaDlqPublisher(
            bootstrap_servers=bootstrap_servers,
            topic=dlq_topic,
        )
        approval_publisher = KafkaApprovalPublisher(
            bootstrap_servers=bootstrap_servers,
            topic=approval_topic,
        )
        pending_store = RedisPendingApprovalStore(
            redis_url=redis_url,
            ttl_seconds=pending_ttl_seconds,
            allow_in_memory_fallback=allow_in_memory_dedupe,
        )
        policy_validator = None
        if _env_bool("REQUEST_GATE_POLICY_VALIDATOR_ENABLED", default=True):
            policy_validator = HttpPolicyValidatorAdapter(
                base_url=str(os.getenv("REQUEST_GATE_POLICY_VALIDATOR_BASE_URL", "http://policy-validator:8095")),
                api_key=os.getenv("REQUEST_GATE_POLICY_VALIDATOR_API_KEY"),
                timeout_seconds=float(os.getenv("REQUEST_GATE_POLICY_VALIDATOR_TIMEOUT_SECONDS", "3.0")),
            )

        use_case = ValidateAndEnqueueDecisionRequest(
            dedupe_store=dedupe_store,
            lifecycle_publisher=lifecycle_publisher,
            dlq_publisher=dlq_publisher,
            dedupe_ttl_seconds=dedupe_ttl_seconds,
            policy_validator=policy_validator,
        )
        self._consumer = KafkaDecisionRequestedConsumer(
            bootstrap_servers=bootstrap_servers,
            topic=requested_topic,
            group_id=group_id,
            poll_timeout_seconds=poll_timeout,
            use_case=use_case,
            dlq_publisher=dlq_publisher,
        )
        self._lifecycle_observer = KafkaLifecycleApprovalObserver(
            bootstrap_servers=bootstrap_servers,
            topic=lifecycle_topic,
            group_id=observer_group_id,
            poll_timeout_seconds=poll_timeout,
            pending_store=pending_store,
        )
        self._lifecycle_publisher = lifecycle_publisher
        self._dlq_publisher = dlq_publisher
        self._approval_publisher = approval_publisher
        self._dedupe_store = dedupe_store
        self._pending_store = pending_store
        self._stop_event = threading.Event()
        self._consumer_thread: threading.Thread | None = None
        self._observer_thread: threading.Thread | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._consumer_thread = threading.Thread(target=self._run_consumer_loop, daemon=True, name="request-gate-consumer")
        self._observer_thread = threading.Thread(target=self._run_observer_loop, daemon=True, name="request-gate-lifecycle-observer")
        self._consumer_thread.start()
        self._observer_thread.start()
        self._started = True

    def stop(self) -> None:
        self._stop_event.set()
        for thread in (self._consumer_thread, self._observer_thread):
            if thread is not None:
                thread.join(timeout=5.0)
        self._consumer.close()
        self._lifecycle_observer.close()
        self._lifecycle_publisher.close()
        self._dlq_publisher.close()
        self._approval_publisher.close()
        self._started = False

    def health(self) -> dict[str, Any]:
        consumer_alive = bool(self._consumer_thread and self._consumer_thread.is_alive())
        observer_alive = bool(self._observer_thread and self._observer_thread.is_alive())
        return {
            "ok": bool(self._started and consumer_alive and observer_alive and not self._stop_event.is_set()),
            "service": "request-gate",
            "consumer_thread_alive": consumer_alive,
            "approval_observer_thread_alive": observer_alive,
            "dedupe_backend": self._dedupe_store.backend(),
            "pending_approvals_backend": self._pending_store.backend(),
        }

    def list_pending_approvals(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return self._pending_store.list_pending(limit=limit)

    def get_pending_approval(self, decision_id: str) -> dict[str, Any] | None:
        return self._pending_store.get_pending(decision_id)

    def publish_approval(
        self,
        *,
        decision_id: str,
        approved: bool,
        approved_by: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        pending = self._pending_store.get_pending(decision_id)
        if pending is None:
            log_milestone(
                logger,
                "approval_state_missing",
                service_name=_SERVICE_NAME,
                component=_COMPONENT_RUNTIME,
                decision_id=str(decision_id),
                context_id="",
                episode_id=str(decision_id),
                event_type="approval",
                topic=str(os.getenv("REQUEST_GATE_APPROVAL_TOPIC", "decision.approval")),
                trace_id="",
            )
            raise KeyError(f"pending approval not found for decision_id={decision_id}")
        event = build_approval_event_from_pending(
            pending,
            approved=bool(approved),
            approved_by=approved_by,
            reason=reason,
        )
        self._approval_publisher.publish(event)
        log_milestone(
            logger,
            "approved" if bool(approved) else "denied",
            service_name=_SERVICE_NAME,
            component=_COMPONENT_RUNTIME,
            decision_id=str(event.decision_id),
            context_id=str(event.context_id or ""),
            episode_id=str(event.decision_id),
            event_type="decision.approval",
            topic=str(os.getenv("REQUEST_GATE_APPROVAL_TOPIC", "decision.approval")),
            fingerprint=str(event.policy_fingerprint),
            event_id=str(getattr(event, "event_id", "") or getattr(event, "content_hash", "")),
            trace_id="",
            approved=bool(event.approved),
            approved_by=str(event.approved_by),
        )
        if bool(approved):
            log_milestone(
                logger,
                "approval_token_issued",
                service_name=_SERVICE_NAME,
                component=_COMPONENT_RUNTIME,
                decision_id=str(event.decision_id),
                context_id=str(event.context_id or ""),
                episode_id=str(event.decision_id),
                event_type="decision.approval",
                topic=str(os.getenv("REQUEST_GATE_APPROVAL_TOPIC", "decision.approval")),
                fingerprint=str(event.policy_fingerprint),
                event_id=str(getattr(event, "event_id", "") or getattr(event, "content_hash", "")),
                trace_id="",
                approved_by=str(event.approved_by),
            )
        self._pending_store.mark_processed(
            decision_id=str(decision_id),
            status="approved" if bool(approved) else "denied",
            processed_by=str(approved_by),
            reason=str(reason or ""),
        )
        return {
            "decision_id": event.decision_id,
            "context_id": str(event.context_id or ""),
            "approved": bool(event.approved),
            "policy_fingerprint": str(event.policy_fingerprint),
            "approved_by": str(event.approved_by),
            "approved_at": str(event.approved_at),
            "reason": str(event.reason or ""),
        }

    def _run_consumer_loop(self) -> None:
        try:
            self._consumer.run_forever(
                stop_event=self._stop_event,
                idle_sleep_seconds=self._idle_sleep_seconds,
            )
        except Exception:
            logger.exception("request_gate_consumer_loop_failed")

    def _run_observer_loop(self) -> None:
        try:
            self._lifecycle_observer.run_forever(
                stop_event=self._stop_event,
                idle_sleep_seconds=self._idle_sleep_seconds,
            )
        except Exception:
            logger.exception("request_gate_lifecycle_observer_loop_failed")


def build_approval_event_from_pending(
    pending: Mapping[str, Any],
    *,
    approved: bool,
    approved_by: str,
    reason: str | None = None,
) -> ApprovalEvent:
    decision_id = str(pending.get("decision_id") or "").strip()
    if not decision_id:
        raise ValueError("pending approval must include decision_id")
    policy_fingerprint = str(pending.get("required_policy_fingerprint") or "").strip().lower()
    if not _is_sha256_hex(policy_fingerprint):
        raise ValueError("pending approval must include required_policy_fingerprint sha256")
    safe_approved_by = str(approved_by or "").strip() or "ui"
    safe_reason = str(reason or "").strip() or None
    context_id = str(pending.get("context_id") or "").strip() or None
    return ApprovalEvent(
        schema_version=SCHEMA_VERSION,
        decision_id=decision_id,
        context_id=context_id,
        approved=bool(approved),
        policy_fingerprint=policy_fingerprint,
        approved_by=safe_approved_by,
        approved_at=now_iso(),
        reason=safe_reason,
    )


def create_app(*, runtime: RequestGateRuntime | None = None) -> Any:
    if FastAPI is None:
        raise RuntimeError("FastAPI is required for request_gate service. Install: pip install fastapi uvicorn")
    service_runtime = runtime or RequestGateRuntime()
    app = FastAPI(title="request-gate", version="1.0.0")
    app.state.runtime = service_runtime

    @app.on_event("startup")
    def _on_startup() -> None:
        service_runtime.start()

    @app.on_event("shutdown")
    def _on_shutdown() -> None:
        service_runtime.stop()

    @app.get("/health")
    def health() -> dict[str, Any]:
        status = service_runtime.health()
        if not bool(status.get("ok")):
            raise HTTPException(status_code=503, detail=status)
        return status

    @app.get("/approvals/pending")
    def approvals_pending() -> list[dict[str, Any]]:
        return service_runtime.list_pending_approvals()

    @app.get("/approvals/{decision_id}")
    def approval_detail(decision_id: str) -> dict[str, Any]:
        pending = service_runtime.get_pending_approval(decision_id)
        if pending is None:
            log_milestone(
                logger,
                "approval_state_missing",
                service_name=_SERVICE_NAME,
                component=_COMPONENT_API,
                decision_id=str(decision_id),
                context_id="",
                episode_id=str(decision_id),
                event_type="approval_lookup",
                topic="",
                trace_id="",
            )
            raise HTTPException(status_code=404, detail={"error": "not_found", "decision_id": decision_id})
        return pending

    @app.post("/approvals/{decision_id}/approve")
    async def approval_approve(decision_id: str, request: Request) -> dict[str, Any]:
        payload = await _request_json_payload(request)
        approved_by = str(payload.get("approved_by") or "").strip() or "ui"
        reason = str(payload.get("reason") or "").strip() or None
        try:
            result = service_runtime.publish_approval(
                decision_id=decision_id,
                approved=True,
                approved_by=approved_by,
                reason=reason,
            )
        except KeyError as exc:
            log_milestone(
                logger,
                "approval_state_missing",
                service_name=_SERVICE_NAME,
                component=_COMPONENT_API,
                decision_id=str(decision_id),
                context_id="",
                episode_id=str(decision_id),
                event_type="approval",
                topic="decision.approval",
                trace_id="",
            )
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "invalid_request", "message": str(exc)}) from exc
        return {"ok": True, **result}

    @app.post("/approvals/{decision_id}/deny")
    async def approval_deny(decision_id: str, request: Request) -> dict[str, Any]:
        payload = await _request_json_payload(request)
        approved_by = str(payload.get("approved_by") or "").strip() or "ui"
        reason = str(payload.get("reason") or "").strip() or "DENIED"
        try:
            result = service_runtime.publish_approval(
                decision_id=decision_id,
                approved=False,
                approved_by=approved_by,
                reason=reason,
            )
        except KeyError as exc:
            log_milestone(
                logger,
                "approval_state_missing",
                service_name=_SERVICE_NAME,
                component=_COMPONENT_API,
                decision_id=str(decision_id),
                context_id="",
                episode_id=str(decision_id),
                event_type="approval",
                topic="decision.approval",
                trace_id="",
            )
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"error": "invalid_request", "message": str(exc)}) from exc
        return {"ok": True, **result}

    return app


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    if uvicorn is None:
        raise RuntimeError("uvicorn is required for request_gate service. Install: pip install uvicorn")
    app = create_app()
    port = int(os.getenv("REQUEST_GATE_PORT", "8097"))
    uvicorn.run(app, host="0.0.0.0", port=port)
    return 0


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _is_sha256_hex(value: str) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(ch in "0123456789abcdef" for ch in text)


async def _request_json_payload(request: Request) -> dict[str, Any]:
    try:
        parsed = await request.json()
    except Exception:
        return {}
    if isinstance(parsed, dict):
        return dict(parsed)
    return {}


if __name__ == "__main__":
    raise SystemExit(main())
