from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import logging
from babyai_shared.core.logging_milestones import log_milestone
from infrastructure.pending_approvals_store import RedisPendingApprovalStore

try:
    from confluent_kafka import Consumer, KafkaError, KafkaException
except Exception:  # pragma: no cover - optional dependency
    Consumer = None  # type: ignore[assignment]
    KafkaError = None  # type: ignore[assignment]
    KafkaException = Exception  # type: ignore[assignment]


logger = logging.getLogger(__name__)
_SERVICE_NAME = "request-gate"
_COMPONENT = "infrastructure.lifecycle_observer"


def _require_consumer() -> Any:
    if Consumer is None:
        raise ImportError("confluent-kafka is required for request_gate lifecycle observer")
    return Consumer


class KafkaLifecycleApprovalObserver:
    def __init__(
        self,
        *,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        poll_timeout_seconds: float,
        pending_store: RedisPendingApprovalStore,
    ) -> None:
        consumer_cls = _require_consumer()
        self._topic = str(topic).strip() or "decision.lifecycle"
        self._poll_timeout_seconds = float(poll_timeout_seconds)
        self._pending_store = pending_store
        self._consumer = consumer_cls(
            {
                "bootstrap.servers": str(bootstrap_servers),
                "group.id": str(group_id),
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        self._consumer.subscribe([self._topic])

    def run_once(self) -> int:
        msg = self._consumer.poll(timeout=self._poll_timeout_seconds)
        if msg is None:
            return 0
        if msg.error():
            if KafkaError is not None and msg.error().code() == KafkaError._PARTITION_EOF:
                return 0
            if _is_retriable_consumer_error(msg.error()):
                return 0
            raise KafkaException(msg.error())

        raw_value = msg.value()
        if raw_value is None:
            self._consumer.commit(message=msg, asynchronous=False)
            return 1

        try:
            payload = json.loads(raw_value.decode("utf-8"))
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            self._handle_payload(payload)

        self._consumer.commit(message=msg, asynchronous=False)
        return 1

    def run_forever(self, *, stop_event: Any, idle_sleep_seconds: float = 0.2) -> None:
        while not stop_event.is_set():
            processed = self.run_once()
            if processed == 0:
                time.sleep(float(idle_sleep_seconds))

    def close(self) -> None:
        self._consumer.close()

    def _handle_payload(self, payload: dict[str, Any]) -> None:
        decision_id = str(payload.get("decision_id") or "").strip()
        if not decision_id:
            return

        status = str(payload.get("status") or "").strip().lower()
        context_id = str(payload.get("context_id") or "").strip()
        metadata_raw = payload.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}

        if status == "waiting_for_approval":
            required_fingerprint = str(metadata.get("policy_fingerprint") or "").strip().lower()
            if not required_fingerprint:
                return
            explanation_payload = _policy_explanation_payload(metadata)
            log_milestone(
                logger,
                "approval_required",
                service_name=_SERVICE_NAME,
                component=_COMPONENT,
                decision_id=str(decision_id),
                context_id=str(context_id),
                episode_id=str(decision_id),
                event_type=str(status),
                topic=self._topic,
                fingerprint=str(required_fingerprint),
                event_id="",
                trace_id=str(metadata.get("trace_id") or ""),
            )
            self._pending_store.upsert_pending(
                {
                    "decision_id": decision_id,
                    "context_id": context_id,
                    "policy_preset": str(metadata.get("policy_preset") or "").strip(),
                    "required_policy_fingerprint": required_fingerprint,
                    "explanation": _explanation_text(metadata.get("policy_explanation")),
                    "policy_explanation": explanation_payload,
                    "safety_profile": str(explanation_payload.get("safety_profile") or ""),
                    "write_scope": _write_scope_text(explanation_payload, metadata),
                    "if_you_change": explanation_payload.get("if_you_change"),
                    "created_at": str(payload.get("timestamp") or _now_iso()),
                    "user_prompt": _user_prompt(metadata),
                }
            )
            return

        if status == "requested":
            required_fingerprint = str(metadata.get("policy_fingerprint") or "").strip().lower()
            if required_fingerprint and bool(metadata.get("approval_required")):
                explanation_payload = _policy_explanation_payload(metadata)
                log_milestone(
                    logger,
                    "approval_required",
                    service_name=_SERVICE_NAME,
                    component=_COMPONENT,
                    decision_id=str(decision_id),
                    context_id=str(context_id),
                    episode_id=str(decision_id),
                    event_type=str(status),
                    topic=self._topic,
                    fingerprint=str(required_fingerprint),
                    event_id="",
                    trace_id=str(metadata.get("trace_id") or ""),
                )
                self._pending_store.upsert_pending(
                    {
                        "decision_id": decision_id,
                        "context_id": context_id,
                        "policy_preset": str(metadata.get("policy_preset") or "").strip(),
                        "required_policy_fingerprint": required_fingerprint,
                        "explanation": _explanation_text(metadata.get("policy_explanation")),
                        "policy_explanation": explanation_payload,
                        "safety_profile": str(explanation_payload.get("safety_profile") or ""),
                        "write_scope": _write_scope_text(explanation_payload, metadata),
                        "if_you_change": explanation_payload.get("if_you_change"),
                        "created_at": str(payload.get("timestamp") or _now_iso()),
                        "user_prompt": _user_prompt(metadata),
                    }
                )
            return

        if status in {"started", "generating", "evaluating", "evaluated", "repairing", "completed", "failed"}:
            self._pending_store.mark_processed(
                decision_id=decision_id,
                status=status,
                reason=status,
            )


def _is_retriable_consumer_error(error: Any) -> bool:
    try:
        if bool(error.retriable()):
            return True
    except Exception:
        pass
    if KafkaError is None:
        return False
    try:
        return error.code() in {
            KafkaError.UNKNOWN_TOPIC_OR_PART,
            KafkaError._TRANSPORT,
            KafkaError._ALL_BROKERS_DOWN,
        }
    except Exception:
        return False


def _explanation_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""
    reasons_raw = payload.get("why")
    reasons: list[str] = []
    if isinstance(reasons_raw, list):
        for item in reasons_raw:
            text = str(item or "").strip()
            if text:
                reasons.append(text)
    if reasons:
        return " ".join(reasons)
    reason_code = str(payload.get("reason_code") or "").strip()
    if reason_code:
        return reason_code
    return ""


def _user_prompt(metadata: dict[str, Any]) -> str:
    direct = metadata.get("user_prompt")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    ui_payload = metadata.get("ui")
    if isinstance(ui_payload, dict):
        ui_prompt = ui_payload.get("user_prompt")
        if isinstance(ui_prompt, str):
            return ui_prompt.strip()
    return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _policy_explanation_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    raw = metadata.get("policy_explanation")
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _write_scope_text(explanation_payload: dict[str, Any], metadata: dict[str, Any]) -> str:
    write_scope = explanation_payload.get("write_scope")
    if isinstance(write_scope, dict):
        return str(write_scope.get("type") or "").strip()
    effective_policy = metadata.get("effective_policy")
    if isinstance(effective_policy, dict):
        scope = effective_policy.get("write_scope")
        if isinstance(scope, dict):
            return str(scope.get("type") or "").strip()
    return ""
