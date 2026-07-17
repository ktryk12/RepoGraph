from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import os
from typing import Any
from dataclasses import replace

from babyai_shared.core.logging_milestones import log_milestone
from babyai_shared.policy.approval_gate import approval_required, compute_policy_fingerprint
from domain import (
    DecisionRequest,
    build_lifecycle_requested_event,
    canonicalize_request,
    compute_lifecycle_event_fingerprint,
    compute_request_fingerprint,
    validate_policy_contract,
)
from domain.policy_explainer import build_policy_explanation

from .ports import DedupeStore, DlqPublisher, LifecyclePublisher, PolicyValidatorPort

logger = logging.getLogger(__name__)
_SERVICE_NAME = "request-gate"
_COMPONENT = "application.use_cases"


@dataclass(frozen=True)
class ValidateAndEnqueueSuccess:
    code: str
    decision_id: str
    request_fingerprint: str
    event_fingerprint: str


@dataclass(frozen=True)
class ValidateAndEnqueueFailure:
    code: str
    message: str
    retryable: bool
    details: dict[str, Any] = field(default_factory=dict)


ValidateAndEnqueueResult = ValidateAndEnqueueSuccess | ValidateAndEnqueueFailure


class ValidateAndEnqueueDecisionRequest:
    def __init__(
        self,
        *,
        dedupe_store: DedupeStore,
        lifecycle_publisher: LifecyclePublisher,
        dlq_publisher: DlqPublisher,
        dedupe_ttl_seconds: int = 86400,
        policy_validator: PolicyValidatorPort | None = None,
    ) -> None:
        self._dedupe_store = dedupe_store
        self._lifecycle_publisher = lifecycle_publisher
        self._dlq_publisher = dlq_publisher
        self._dedupe_ttl_seconds = max(1, int(dedupe_ttl_seconds))
        self._policy_validator = policy_validator

    def execute(self, request: DecisionRequest) -> ValidateAndEnqueueResult:
        request = replace(request, metadata=dict(request.metadata or {}))
        domain_error = validate_policy_contract(request.policy_contract)
        if domain_error is not None:
            return self._fail_and_dlq(
                code=str(domain_error.code),
                message=str(domain_error.message),
                retryable=False,
                request=request,
                details={"field": domain_error.field},
            )

        if self._policy_validator is not None:
            verdict = self._policy_validator.validate_request(request)
            if not bool(verdict.allowed):
                log_milestone(
                    logger,
                    "policy_validation_result",
                    service_name=_SERVICE_NAME,
                    component=_COMPONENT,
                    decision_id=str(request.decision_id),
                    context_id=str(request.context_id),
                    episode_id=str(request.decision_id),
                    event_type="policy_validation",
                    fingerprint="",
                    trace_id=str(request.metadata.get("trace_id") or ""),
                    policy_id=str(request.policy_contract.policy_id),
                    allowed=False,
                    effective_policy_hash="",
                    reason_code=str(verdict.reason_code or ""),
                )
                return self._fail_and_dlq(
                    code=str(verdict.reason_code or "POLICY_VALIDATOR_DENIED"),
                    message=str(verdict.message or "external policy validator denied request"),
                    retryable=False,
                    request=request,
                    details={"validator_metadata": dict(verdict.metadata or {})},
                )
            effective_policy = self._resolve_effective_policy(
                request=request,
                validator_metadata=dict(verdict.metadata or {}),
            )
            policy_constraints = dict(request.policy_contract.constraints or {})
            policy_fingerprint = compute_policy_fingerprint(effective_policy)
            approval_gate_required = approval_required(
                effective_policy=effective_policy,
                policy_constraints=policy_constraints,
                policy_preset=str(request.policy_contract.policy_id),
            )
            policy_explanation = build_policy_explanation(
                effective_policy=effective_policy,
                allowed=bool(verdict.allowed),
                reason_code=verdict.reason_code,
                policy_preset=str(request.policy_contract.policy_id),
                approval_required=approval_gate_required,
            )
            request.metadata["effective_policy"] = effective_policy
            request.metadata["policy_validator"] = {
                "allowed": bool(verdict.allowed),
                "reason_code": verdict.reason_code,
            }
            request.metadata["policy_fingerprint"] = policy_fingerprint
            request.metadata["approval_required"] = bool(approval_gate_required)
            request.metadata["policy_constraints"] = policy_constraints
            request.metadata["policy_preset"] = str(request.metadata.get("policy_preset") or request.policy_contract.policy_id)
            request.metadata["policy_explanation"] = policy_explanation
            log_milestone(
                logger,
                "policy_validation_result",
                service_name=_SERVICE_NAME,
                component=_COMPONENT,
                decision_id=str(request.decision_id),
                context_id=str(request.context_id),
                episode_id=str(request.decision_id),
                event_type="policy_validation",
                fingerprint=str(policy_fingerprint),
                trace_id=str(request.metadata.get("trace_id") or ""),
                policy_id=str(request.policy_contract.policy_id),
                allowed=bool(verdict.allowed),
                write_scope=str((effective_policy.get("write_scope") or {}).get("type") or ""),
                effective_policy_hash=str(policy_fingerprint),
            )
        else:
            effective_policy = self._resolve_effective_policy(request=request, validator_metadata={})
            policy_constraints = dict(request.policy_contract.constraints or {})
            policy_fingerprint = compute_policy_fingerprint(effective_policy)
            approval_gate_required = approval_required(
                effective_policy=effective_policy,
                policy_constraints=policy_constraints,
                policy_preset=str(request.policy_contract.policy_id),
            )
            policy_explanation = build_policy_explanation(
                effective_policy=effective_policy,
                allowed=True,
                reason_code="LOCAL_FALLBACK_ALLOW",
                policy_preset=str(request.policy_contract.policy_id),
                approval_required=approval_gate_required,
            )
            request.metadata["effective_policy"] = effective_policy
            request.metadata["policy_fingerprint"] = policy_fingerprint
            request.metadata["approval_required"] = bool(approval_gate_required)
            request.metadata["policy_constraints"] = policy_constraints
            request.metadata["policy_preset"] = str(request.metadata.get("policy_preset") or request.policy_contract.policy_id)
            request.metadata["policy_explanation"] = policy_explanation
            log_milestone(
                logger,
                "policy_validation_result",
                service_name=_SERVICE_NAME,
                component=_COMPONENT,
                decision_id=str(request.decision_id),
                context_id=str(request.context_id),
                episode_id=str(request.decision_id),
                event_type="policy_validation",
                fingerprint=str(policy_fingerprint),
                trace_id=str(request.metadata.get("trace_id") or ""),
                policy_id=str(request.policy_contract.policy_id),
                allowed=True,
                write_scope=str((effective_policy.get("write_scope") or {}).get("type") or ""),
                effective_policy_hash=str(policy_fingerprint),
                mode="local_fallback",
            )

        if bool(approval_gate_required) and _auto_approve_enabled():
            permit = _build_auto_execution_permit(
                decision_id=str(request.decision_id),
                policy_fingerprint=str(policy_fingerprint),
            )
            request.metadata["execution_permit"] = dict(permit)
            request.metadata["approval_token"] = dict(permit)
            request.metadata["approval_granted"] = True
            request.metadata["approval_granted_by"] = str(permit["approved_by"])
            request.metadata["approval_granted_at"] = str(permit["approved_at"])
            request.metadata["approval_auto_approve"] = True
            log_milestone(
                logger,
                "approval_auto_granted",
                service_name=_SERVICE_NAME,
                component=_COMPONENT,
                decision_id=str(request.decision_id),
                context_id=str(request.context_id),
                episode_id=str(request.decision_id),
                event_type="policy_validation",
                fingerprint=str(policy_fingerprint),
                trace_id=str(request.metadata.get("trace_id") or ""),
                policy_id=str(request.policy_contract.policy_id),
                approved_by=str(permit["approved_by"]),
            )

        request_fingerprint = compute_request_fingerprint(request)
        log_milestone(
            logger,
            "request_fingerprint_computed",
            service_name=_SERVICE_NAME,
            component=_COMPONENT,
            decision_id=str(request.decision_id),
            context_id=str(request.context_id),
            episode_id=str(request.decision_id),
            event_type="decision.requested",
            topic="decision.requested",
            fingerprint=str(request_fingerprint),
            trace_id=str(request.metadata.get("trace_id") or ""),
        )
        dedupe_key = f"request_gate:{request.decision_id}:{request_fingerprint}"
        if not self._dedupe_store.claim(key=dedupe_key, ttl_seconds=self._dedupe_ttl_seconds):
            return self._fail_and_dlq(
                code="DUPLICATE_REQUEST",
                message="request fingerprint already processed",
                retryable=False,
                request=request,
                details={"dedupe_key": dedupe_key},
            )

        try:
            event = build_lifecycle_requested_event(request)
            event_metadata = dict(event.metadata or {})
            effective = event_metadata.get("effective_policy")
            if not isinstance(effective, dict) or not effective:
                raise ValueError("lifecycle requested event must include non-empty metadata.effective_policy")
            self._lifecycle_publisher.publish(event)
            log_milestone(
                logger,
                "lifecycle_published",
                service_name=_SERVICE_NAME,
                component=_COMPONENT,
                decision_id=str(event.decision_id),
                context_id=str(event.context_id),
                episode_id=str(event.decision_id),
                event_type=str(event.status),
                topic="decision.lifecycle",
                fingerprint=str(event_metadata.get("event_fingerprint", "")),
                event_id=str(event_metadata.get("event_id", "")),
                trace_id=str(event_metadata.get("trace_id") or request.metadata.get("trace_id") or ""),
                includes_effective_policy=True,
            )
            return ValidateAndEnqueueSuccess(
                code="REQUEST_ENQUEUED",
                decision_id=str(request.decision_id),
                request_fingerprint=request_fingerprint,
                event_fingerprint=compute_lifecycle_event_fingerprint(event),
            )
        except Exception as exc:
            return self._fail_and_dlq(
                code="LIFECYCLE_PUBLISH_FAILED",
                message=str(exc),
                retryable=True,
                request=request,
                details={},
            )

    def _fail_and_dlq(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        request: DecisionRequest,
        details: dict[str, Any],
    ) -> ValidateAndEnqueueFailure:
        payload = {
            "reason_code": str(code),
            "message": str(message),
            "retryable": bool(retryable),
            "details": dict(details or {}),
            "request": canonicalize_request(request),
        }
        self._dlq_publisher.publish(
            reason_code=str(code),
            message=str(message),
            payload=payload,
        )
        return ValidateAndEnqueueFailure(
            code=str(code),
            message=str(message),
            retryable=bool(retryable),
            details=dict(details or {}),
        )

    def _resolve_effective_policy(
        self,
        *,
        request: DecisionRequest,
        validator_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        external = validator_metadata.get("effective_policy")
        if isinstance(external, dict) and external:
            return _normalize_effective_policy(external, request=request)

        constraints = dict(request.policy_contract.constraints or {})
        write_scope = constraints.get("write_scope")
        if not isinstance(write_scope, dict):
            write_scope_type = constraints.get("write_scope_type")
            write_scope = {"type": str(write_scope_type).strip()} if isinstance(write_scope_type, str) else {}
        metadata_scope_type = _resolve_scope_type_from_metadata(request.metadata)
        scope_type = str(write_scope.get("type") or "").strip() or metadata_scope_type or "policy_service"

        safety_mode = str(constraints.get("safety_mode") or "").strip().lower()
        if safety_mode not in {"strict", "balanced", "lenient"}:
            safety_mode = "strict" if request.policy_contract.policy_id == "public" else "balanced"

        quality_preset = "precision_first"
        if request.policy_contract.policy_id == "dev":
            quality_preset = "balanced"
        if request.policy_contract.policy_id == "restricted":
            quality_preset = "precision_first"

        return {
            "version": "effective_policy.v1",
            "policy_id": str(request.policy_contract.policy_id),
            "policy_version": 1,
            "write_scope": {"type": scope_type},
            "quality_profile": {"preset": quality_preset},
            "safety_profile": safety_mode,
            "constraints": constraints,
        }


def _normalize_effective_policy(
    payload: dict[str, Any],
    *,
    request: DecisionRequest,
) -> dict[str, Any]:
    write_scope = payload.get("write_scope")
    if not isinstance(write_scope, dict):
        write_scope = {}
    metadata_scope_type = _resolve_scope_type_from_metadata(request.metadata)
    scope_type = str(write_scope.get("type") or "").strip() or metadata_scope_type or "policy_service"
    normalized = dict(payload)
    normalized["policy_id"] = str(normalized.get("policy_id") or request.policy_contract.policy_id)
    normalized["policy_version"] = int(normalized.get("policy_version") or 1)
    normalized["write_scope"] = {"type": scope_type}
    if not isinstance(normalized.get("quality_profile"), dict):
        normalized["quality_profile"] = {"preset": "balanced"}
    return normalized


def _resolve_scope_type_from_metadata(metadata: dict[str, Any] | None) -> str | None:
    if not isinstance(metadata, dict):
        return None
    candidates = (
        metadata.get("required_write_scope"),
        _scope_type_from_object(metadata.get("write_scope")),
        metadata.get("write_scope_type"),
    )
    for candidate in candidates:
        normalized = _normalize_scope_type(candidate)
        if normalized is not None:
            return normalized
    return None


def _scope_type_from_object(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    return value.get("type")


def _normalize_scope_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized else None


def _auto_approve_enabled() -> bool:
    raw = str(os.getenv("AUTO_APPROVE", "false") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _build_auto_execution_permit(*, decision_id: str, policy_fingerprint: str) -> dict[str, Any]:
    return {
        "decision_id": str(decision_id),
        "policy_fingerprint": str(policy_fingerprint).strip().lower(),
        "approved_by": "request-gate:auto-approve",
        "approved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": "AUTO_APPROVE",
    }
