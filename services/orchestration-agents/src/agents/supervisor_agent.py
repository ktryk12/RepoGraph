"""
Supervisor Agent - orchestrates the architecture decision pipeline.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
import json
import uuid

from agents.base import Agent
from agents.repair_contracts import RepairPlan, RepairPlanValidator, RepairRequest
from babyai_shared.contracts.agent_node import AgentNode
from babyai_shared.core.action_proposal import ActionProposal
from babyai_shared.core.hypothesis import Hypothesis
from babyai_shared.core.outcome import Outcome
from babyai_shared.bus.protocol import Context, Message, MessageType
from babyai_shared.core.universal_plan import (
    PlanConflictError,
    PlanDiff,
    PlanValidationError,
    UniversalPlan,
    apply_plan_diff,
    build_universal_plan,
)
from babyai_shared.core.universal_spec import assess_universal_spec, build_spec_fix_request
from babyai_shared.storage.artifact_store import FileArtifactStore


class SupervisorAgent(AgentNode, Agent):
    def __init__(
        self,
        agent_id: str = "supervisor-001",
        *,
        artifact_store: FileArtifactStore | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            role="supervisor",
            accepts={
                MessageType.USER_REQUEST,
                MessageType.REQUIREMENTS_COMPLETE,
                MessageType.ARCHITECTURE_DECISION,
                MessageType.VALIDATION_COMPLETE,
                MessageType.REPAIR_PROPOSED,
                MessageType.REPAIR_FAILED,
                MessageType.IMAGE_REQUEST,
                MessageType.VOICE_INPUT,
                MessageType.VOICE_OUTPUT,
            },
        )
        self.active_pipelines: Dict[str, str] = {}
        self._artifact_store = artifact_store or FileArtifactStore()
        # Lazy agent instances for image/voice routing
        self._comfyui_agent: Any | None = None
        self._voice_io_agent_id: str = "voice-io-001"

        # Extend accepted message types for image, voice, and trading pipelines
        self.accepts |= {
            MessageType.IMAGE_REQUEST,
            MessageType.VOICE_INPUT,
            MessageType.VOICE_OUTPUT,
            MessageType.TRADE_ANALYSIS_REQUEST,
            MessageType.TRADE_RECOMMENDATION,
            MessageType.CRYPTO_INTEL_SIGNAL,
            MessageType.INFRA_GAP_DETECTED,
        }
        self._trading_agent_id: str = "trading-agent-001"
        self._crypto_intel_agent: Any | None = None
        self._gap_detector_agent: Any | None = None
        self._deep_analysis_agent: Any | None = None

        self.accepts |= {
            MessageType.ANALYSIS_COMPLETE,
            MessageType.ANALYSIS_FAILED,
        }
        self._trend_scout_agent:        Any | None = None
        self._creative_brief_agent:     Any | None = None
        self._content_orchestrator:     Any | None = None

        self.accepts |= {
            MessageType.CONTENT_BRIEF_READY,
            MessageType.CONTENT_PUBLISHED,
            MessageType.CONTENT_PUBLISH_FAILED,
        }

        self.accepts |= {
            MessageType.WATCHDOG_RESEARCH,
            MessageType.WATCHDOG_SCRIPT_READY,
            MessageType.WATCHDOG_APPROVED,
            # Editorial pipeline
            MessageType.TOPIC_SUBMITTED,
            MessageType.EDITORIAL_DECISION_READY,
            MessageType.PRODUCTION_ROUTED,
            MessageType.PRODUCTION_COMPLETE,
            MessageType.HUMAN_APPROVED,
        }
        self._watchdog_agent: Any | None = None

    def select_swarm_experts(
        self,
        task: Any,
        registry: Any,
        *,
        hardware_plan: Dict[str, Any] | None = None,
    ) -> List[Any]:
        """
        Deterministically select in-process experts for a swarm episode.
        """
        from aesa.bootstrap.wiring import build_expert_selector
        return build_expert_selector(registry).select(task, hardware_plan=hardware_plan)

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type == MessageType.USER_REQUEST:
            return self._start_pipeline(message, context)
        if message.message_type == MessageType.REQUIREMENTS_COMPLETE:
            return self._route_to_architect(message, context)
        if message.message_type == MessageType.ARCHITECTURE_DECISION:
            return self._route_to_validation(message, context)
        if message.message_type == MessageType.VALIDATION_COMPLETE:
            return self._handle_validation_complete(message, context)
        if message.message_type == MessageType.REPAIR_PROPOSED:
            return self._handle_repair_proposed(message, context)
        if message.message_type == MessageType.REPAIR_FAILED:
            return self._handle_repair_failed(message, context)
        if message.message_type == MessageType.IMAGE_REQUEST:
            return self._route_to_image(message, context)
        if message.message_type in (MessageType.VOICE_INPUT, MessageType.VOICE_OUTPUT):
            return self._route_to_voice_io(message, context)
        if message.message_type == MessageType.TRADE_ANALYSIS_REQUEST:
            return self._route_to_trading(message, context)
        if message.message_type == MessageType.TRADE_RECOMMENDATION:
            return self._handle_trade_recommendation(message, context)
        if message.message_type == MessageType.CRYPTO_INTEL_SIGNAL:
            return self._handle_crypto_intel_signal(message, context)
        if message.message_type == MessageType.INFRA_GAP_DETECTED:
            return self._handle_infra_gap_signal(message, context)
        if message.message_type in (MessageType.ANALYSIS_COMPLETE, MessageType.ANALYSIS_FAILED):
            return self._handle_analysis_result(message, context)
        if message.message_type == MessageType.CONTENT_BRIEF_READY:
            return self._handle_content_brief_ready(message, context)
        if message.message_type in (MessageType.CONTENT_PUBLISHED, MessageType.CONTENT_PUBLISH_FAILED):
            return self._handle_content_publish_event(message, context)
        if message.message_type == MessageType.WATCHDOG_RESEARCH:
            return self._handle_watchdog_research(message, context)
        if message.message_type == MessageType.WATCHDOG_SCRIPT_READY:
            return self._handle_watchdog_script_ready(message, context)
        if message.message_type == MessageType.WATCHDOG_APPROVED:
            return self._handle_watchdog_approved(message, context)
        # Editorial pipeline
        if message.message_type == MessageType.TOPIC_SUBMITTED:
            return self._handle_topic_submitted(message, context)
        if message.message_type == MessageType.EDITORIAL_DECISION_READY:
            return self._handle_editorial_decision_ready(message, context)
        if message.message_type == MessageType.PRODUCTION_ROUTED:
            return self._handle_production_routed(message, context)
        if message.message_type == MessageType.PRODUCTION_COMPLETE:
            return self._handle_production_complete(message, context)
        if message.message_type == MessageType.HUMAN_APPROVED:
            return self._handle_human_approved(message, context)
        return []

    def _start_pipeline(self, message: Message, context: Context) -> List[Message]:
        self.active_pipelines[context.context_id] = "requirements"
        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="requirements-001",
            message_type=MessageType.USER_REQUEST,
            payload={
                "text": message.payload.get("text") or context.user_request or "",
                "reply_to": self.agent_id,
            },
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _route_to_architect(self, message: Message, context: Context) -> List[Message]:
        assessment = assess_universal_spec(context.task_spec)
        if assessment.status != "complete":
            details: Dict[str, Any] = assessment.to_dict()
            details["stage"] = "spec_intake"
            details["requires_spec_fix"] = True

            spec_fix_request = build_spec_fix_request(
                context_id=context.context_id,
                task_spec=context.task_spec,
                assessment=assessment,
            )
            details["spec_fix_request"] = dict(spec_fix_request)
            try:
                raw = json.dumps(
                    spec_fix_request,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                artifact_ref = self._artifact_store.put(
                    raw,
                    context_id=context.context_id,
                    name="spec_fix_request",
                    metadata={
                        "kind": "SpecFixRequest",
                        "status": "incomplete",
                    },
                ).ref
                context.add_artifact_ref(artifact_ref)
                context.attach_ref("spec_fix_request", artifact_ref)
                details["spec_fix_request_ref"] = artifact_ref
            except Exception as exc:
                details["spec_fix_request_write_error"] = f"{type(exc).__name__}: {exc}"

            self.active_pipelines[context.context_id] = "requirements"
            return [
                Message(
                    message_id=str(uuid.uuid4()),
                    from_agent=self.agent_id,
                    to_agent="logger-001",
                    message_type=MessageType.LOG_FAILURE,
                    payload={
                        "context_id": context.context_id,
                        "decision_id": None,
                        "event": "spec_incomplete",
                        "stop_reason": "spec_incomplete",
                        "quality_tags": list(assessment.quality_tags),
                        "spec_quality_score": float(assessment.spec_quality_score),
                    },
                    context_id=context.context_id,
                    timestamp=datetime.now().isoformat(),
                ),
                self._create_user_error(
                    context,
                    "Specification is incomplete; stop before planning/build and fix the spec first",
                    details,
                ),
            ]

        try:
            plan, plan_ref = self._prepare_universal_plan(message, context)
        except (PlanValidationError, PlanConflictError) as exc:
            details = {
                "stage": "planning",
                "error": f"{type(exc).__name__}: {exc}",
                "requires_plan_diff": True,
            }
            return [
                Message(
                    message_id=str(uuid.uuid4()),
                    from_agent=self.agent_id,
                    to_agent="logger-001",
                    message_type=MessageType.LOG_FAILURE,
                    payload={
                        "context_id": context.context_id,
                        "decision_id": None,
                        "event": "plan_diff_conflict",
                        "stop_reason": "plan_diff_conflict",
                        "error_codes": ["PLAN_DIFF_CONFLICT"],
                    },
                    context_id=context.context_id,
                    timestamp=datetime.now().isoformat(),
                ),
                self._create_user_error(
                    context,
                    "UniversalPlan update failed; apply legal PlanDiff against active plan version",
                    details,
                ),
            ]

        self.active_pipelines[context.context_id] = "architecture"
        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="architect-001",
            message_type=MessageType.ARCHITECTURE_REQUEST,
            payload={
                "skip_validation": True,
                "universal_plan_ref": plan_ref,
                "plan_id": plan.plan_id,
                "plan_version": plan.plan_version,
            },
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _prepare_universal_plan(
        self,
        message: Message,
        context: Context,
    ) -> tuple[UniversalPlan, str]:
        raw_direct_plan = message.payload.get("universal_plan") if isinstance(message.payload, dict) else None
        if raw_direct_plan is not None:
            raise PlanValidationError("direct_plan_override_forbidden_use_plan_diff")

        seed = self._plan_seed(message=message, context=context)
        plan = self._load_active_plan(context)
        if plan is None:
            plan = build_universal_plan(context.task_spec, seed=seed, plan_version=1, parent_version=None)
            plan_ref = self._persist_universal_plan(context, plan)
        else:
            plan_ref = str(context.attachments.get("universal_plan") or "").strip()
            if not plan_ref:
                plan_ref = self._persist_universal_plan(context, plan)

        raw_diff = message.payload.get("plan_diff") if isinstance(message.payload, dict) else None
        if raw_diff is None:
            return plan, plan_ref

        if not isinstance(raw_diff, dict):
            raise PlanValidationError("plan_diff_must_be_object")
        diff = PlanDiff.from_dict(raw_diff)
        updated = apply_plan_diff(plan, diff)
        updated_ref = self._persist_universal_plan(context, updated)
        return updated, updated_ref

    def _load_active_plan(self, context: Context) -> UniversalPlan | None:
        plan_ref = str(context.attachments.get("universal_plan") or "").strip()
        if not plan_ref:
            return None
        raw = self._artifact_store.get(plan_ref)
        if raw is None:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
            return UniversalPlan.from_dict(payload)
        except Exception as exc:
            raise PlanValidationError(f"invalid_active_universal_plan:{type(exc).__name__}") from exc

    def _persist_universal_plan(self, context: Context, plan: UniversalPlan) -> str:
        payload = plan.to_dict()
        raw = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        ref = self._artifact_store.put(
            raw,
            context_id=context.context_id,
            name=f"universal_plan_v{plan.plan_version}",
            metadata={
                "kind": "UniversalPlan",
                "plan_id": plan.plan_id,
                "plan_version": plan.plan_version,
                "parent_version": plan.parent_version,
            },
        ).ref
        context.add_artifact_ref(ref)
        context.attach_ref("universal_plan", ref)
        return ref

    def _plan_seed(self, *, message: Message, context: Context) -> int:
        candidates: List[Any] = []
        if isinstance(message.payload, dict):
            candidates.append(message.payload.get("seed"))
        task = context.task_spec if isinstance(context.task_spec, dict) else {}
        candidates.append(task.get("seed"))
        spec = task.get("spec") if isinstance(task.get("spec"), dict) else {}
        candidates.append(spec.get("seed") if isinstance(spec, dict) else None)
        for raw in candidates:
            try:
                return int(raw)
            except Exception:
                continue
        return 0

    def _route_to_validation(self, message: Message, context: Context) -> List[Message]:
        self.active_pipelines[context.context_id] = "validation"
        decision = message.payload.get("decision") or context.architecture_decision
        context.architecture_decision = decision
        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="validation-001",
            message_type=MessageType.VALIDATION_REQUEST,
            payload={"decision": decision, "task": context.task_spec},
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _handle_validation_complete(self, message: Message, context: Context) -> List[Message]:
        payload = message.payload or {}
        passed = bool(payload.get("passed"))
        errors = payload.get("errors", []) or []
        error_codes = self._extract_error_codes(errors)
        failure_tags = self._extract_failure_tags(payload)

        last_repair = context.repair_history[-1] if context.repair_history else {}
        if passed:
            self._update_last_repair_gate_snapshot(context, hard_tags=[])
            self.active_pipelines[context.context_id] = "translation"
            repair_type = last_repair.get("repair_type")
            success_msgs = [
                Message(
                    message_id=str(uuid.uuid4()),
                    from_agent=self.agent_id,
                    to_agent="translator-001",
                    message_type=MessageType.TRANSLATE_DECISION,
                    payload={"decision_id": (context.architecture_decision or {}).get("decision_id")},
                    context_id=context.context_id,
                    timestamp=datetime.now().isoformat(),
                ),
                Message(
                    message_id=str(uuid.uuid4()),
                    from_agent=self.agent_id,
                    to_agent="logger-001",
                    message_type=MessageType.LOG_SUCCESS,
                    payload={
                        "context_id": context.context_id,
                        "decision_id": (context.architecture_decision or {}).get("decision_id"),
                        "error_codes": [],
                        "repair_type": repair_type,
                        "repair_cost": last_repair.get("repair_cost"),
                        "repair_efficiency": last_repair.get("repair_efficiency"),
                        "event": "action_effectiveness_observed" if last_repair.get("ops_actions") else None,
                        "action_applied": last_repair.get("ops_actions"),
                        "action_source": last_repair.get("action_source"),
                        "lookup_reason": last_repair.get("lookup_reason"),
                        "lookup_bucket": last_repair.get("lookup_bucket"),
                        "lookup_key_found": last_repair.get("lookup_key_found"),
                        "lookup_candidates_considered": last_repair.get("lookup_candidates_considered"),
                        "guard_skip_counts": last_repair.get("guard_skip_counts"),
                        "failure_reasons_before": last_repair.get("error_codes"),
                        "still_failing_reasons": [],
                    },
                    context_id=context.context_id,
                    timestamp=datetime.now().isoformat(),
                ),
            ]
            return success_msgs

        ops_payload = {}
        if "OPS_READINESS_MISSING" in error_codes:
            ops_payload = self._ops_readiness_payload(context.architecture_decision)

        failure_payload = {
            "context_id": context.context_id,
            "decision_id": (context.architecture_decision or {}).get("decision_id"),
            "error_codes": error_codes,
            "failure_tags": failure_tags,
            "missing_must_include": payload.get("must_include", {}).get("missing", []),
            **ops_payload,
        }

        if last_repair.get("ops_actions"):
            failure_payload["action_applied"] = last_repair.get("ops_actions")
            failure_payload["still_failing_reasons"] = list(error_codes)
            failure_payload["failure_reasons_before"] = list(last_repair.get("error_codes") or [])
            failure_payload["action_source"] = last_repair.get("action_source")
            failure_payload["lookup_reason"] = last_repair.get("lookup_reason")
            failure_payload["lookup_bucket"] = last_repair.get("lookup_bucket")
            failure_payload["lookup_key_found"] = last_repair.get("lookup_key_found")
            failure_payload["lookup_candidates_considered"] = last_repair.get("lookup_candidates_considered")
            failure_payload["guard_skip_counts"] = last_repair.get("guard_skip_counts")

        def _failure_log(payload: Dict[str, Any]) -> Message:
            return Message(
                message_id=str(uuid.uuid4()),
                from_agent=self.agent_id,
                to_agent="logger-001",
                message_type=MessageType.LOG_FAILURE,
                payload=payload,
                context_id=context.context_id,
                timestamp=datetime.now().isoformat(),
            )

        self._update_last_repair_gate_snapshot(context, hard_tags=failure_tags)

        # Stagnation stop: same hard tags two validations in a row.
        last_before_tags = self._as_str_list(
            ((last_repair.get("gates_before", {}) or {}).get("hard_fail_tags", []) or [])
        )
        if last_before_tags and set(last_before_tags) == set(failure_tags):
            failure_payload["stop_reason"] = "no_progress"
            return [
                _failure_log(failure_payload),
                self._create_user_error(
                    context,
                    "Repair did not change hard failure tags; stopping for no progress",
                    payload,
                ),
            ]

        # Early exit: if a repair flipped the ops gate but validation still fails,
        # do not attempt further repairs.
        if last_repair.get("gate_flip") is True:
            failure_payload["stop_reason"] = "gate_flipped_but_still_failed"
            return [
                _failure_log(failure_payload),
                self._create_user_error(
                    context,
                    "Repair flipped ops gate; stopping further repairs after validation failure",
                    payload,
                ),
            ]

        if self._has_unrecoverable_errors(error_codes):
            failure_payload["stop_reason"] = "unrecoverable"
            return [_failure_log(failure_payload), self._create_user_error(context, "Validation errors are not repairable", payload)]

        if not context.can_repair():
            failure_payload["stop_reason"] = "budget_exhausted"
            return [_failure_log(failure_payload), self._create_user_error(
                context,
                f"Validation failed after {context.repair_attempts} repair attempts",
                payload,
            )]

        if self._is_repeating_error(context, error_codes):
            failure_payload["stop_reason"] = "repeating_error"
            return [_failure_log(failure_payload), self._create_user_error(
                context,
                "Repair loop detected - same errors repeating",
                payload,
            )]

        self.active_pipelines[context.context_id] = "repair"
        snapshot_refs: List[str] = []
        for ref in context.artifact_refs:
            if str(ref).strip():
                snapshot_refs.append(str(ref))
        for ref in context.attachments.values():
            if str(ref).strip():
                snapshot_refs.append(str(ref))
        request_contract = RepairRequest(
            failure_tags=list(failure_tags),
            minimal_target={
                "must_flip_any_of": list(failure_tags),
                "max_edits": 12,
            },
            budget=max(0, int(context.repair_budget) - int(context.repair_attempts)),
            snapshot_refs=sorted({str(r) for r in snapshot_refs if str(r).strip()}),
        )
        return [_failure_log(failure_payload), Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="repair-001",
            message_type=MessageType.REPAIR_REQUEST,
            payload={
                "task": context.task_spec,
                "decision_attempt": context.architecture_decision,
                "validation_errors": payload,
                "repair_request": request_contract.to_dict(),
            },
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _handle_repair_proposed(self, message: Message, context: Context) -> List[Message]:
        repaired_decision = message.payload.get("repaired_decision")
        error_codes = message.payload.get("error_codes", []) or []
        raw_request = message.payload.get("repair_request")
        raw_plan = message.payload.get("repair_plan")
        has_contract = isinstance(raw_request, dict) and isinstance(raw_plan, dict)

        if has_contract:
            repair_request = RepairRequest.from_payload(raw_request)
            repair_plan = RepairPlan.from_payload(raw_plan)
            plan_ok, plan_errors = RepairPlanValidator.validate(repair_request, repair_plan)
            if not plan_ok:
                return [
                    Message(
                        message_id=str(uuid.uuid4()),
                        from_agent=self.agent_id,
                        to_agent="logger-001",
                        message_type=MessageType.LOG_FAILURE,
                        payload={
                            "context_id": context.context_id,
                            "decision_id": (context.architecture_decision or {}).get("decision_id"),
                            "event": "repair_plan_invalid",
                            "repair_request": repair_request.to_dict(),
                            "repair_plan": repair_plan.to_dict(),
                            "errors": list(plan_errors),
                            "stop_reason": "invalid_repair_plan",
                        },
                        context_id=context.context_id,
                        timestamp=datetime.now().isoformat(),
                    ),
                    self._create_user_error(
                        context,
                        f"Repair plan invalid: {', '.join(plan_errors)}",
                        {"repair_plan": repair_plan.to_dict(), "repair_request": repair_request.to_dict()},
                    ),
                ]
        else:
            # Backward compatibility for legacy repair payloads that predate repair contracts.
            repair_request = RepairRequest(
                failure_tags=[str(code) for code in error_codes if str(code).strip()],
                minimal_target={},
                budget=max(0, int(context.repair_budget) - int(context.repair_attempts)),
                snapshot_refs=[],
            )
            repair_plan = RepairPlan.from_payload({})
        repair_type = message.payload.get("repair_type", "unknown")
        ops_before = message.payload.get("ops_before")
        ops_after = message.payload.get("ops_after")
        ops_actions = message.payload.get("ops_actions")
        action_source = message.payload.get("action_source")
        lookup_reason = message.payload.get("lookup_reason")
        lookup_bucket = message.payload.get("lookup_bucket")
        lookup_key_found = message.payload.get("lookup_key_found")
        lookup_candidates_considered = message.payload.get("lookup_candidates_considered")
        guard_skip_counts = message.payload.get("guard_skip_counts")

        before_decision = context.architecture_decision
        context.architecture_decision = repaired_decision
        context.record_repair(repair_type, error_codes)
        if context.repair_history:
            context.repair_history[-1]["failure_tags"] = list(repair_request.failure_tags)
            context.repair_history[-1]["repair_request"] = repair_request.to_dict()
            context.repair_history[-1]["repair_plan"] = repair_plan.to_dict()
            context.repair_history[-1]["gates_before"] = {"hard_fail_tags": list(repair_request.failure_tags)}
            context.repair_history[-1]["gates_after"] = None
            context.repair_history[-1]["hard_gate_flip"] = None
            context.repair_history[-1]["ops_before"] = ops_before
            context.repair_history[-1]["ops_after"] = ops_after
            context.repair_history[-1]["ops_actions"] = ops_actions
            context.repair_history[-1]["action_source"] = action_source
            context.repair_history[-1]["lookup_reason"] = lookup_reason
            context.repair_history[-1]["lookup_bucket"] = lookup_bucket
            context.repair_history[-1]["lookup_key_found"] = lookup_key_found
            context.repair_history[-1]["lookup_candidates_considered"] = lookup_candidates_considered
            context.repair_history[-1]["guard_skip_counts"] = guard_skip_counts

        uplift_payload = self._ops_uplift_payload(
            decision=context.architecture_decision,
            ops_before=ops_before,
            ops_after=ops_after,
        )

        if context.repair_history:
            context.repair_history[-1]["ops_uplift"] = uplift_payload.get("ops_uplift")
            context.repair_history[-1]["gate_flip"] = uplift_payload.get("gate_flip")
            repair_cost = self._repair_cost(
                before_decision=before_decision,
                after_decision=context.architecture_decision,
                ops_actions=ops_actions,
            )
            repair_efficiency = self._repair_efficiency(
                repair_cost=repair_cost,
                ops_uplift=uplift_payload.get("ops_uplift"),
                gate_flip=uplift_payload.get("gate_flip"),
            )
            context.repair_history[-1]["repair_cost"] = repair_cost
            context.repair_history[-1]["repair_efficiency"] = repair_efficiency

        return [
            Message(
                message_id=str(uuid.uuid4()),
                from_agent=self.agent_id,
                to_agent="logger-001",
                message_type=MessageType.LOG_FAILURE,
                payload={
                    "context_id": context.context_id,
                    "decision_id": (context.architecture_decision or {}).get("decision_id"),
                    "error_codes": error_codes,
                    "repair_type": repair_type,
                    "event": "repair_proposed",
                    "ops_before": ops_before,
                    "ops_after": ops_after,
                    "ops_actions": ops_actions,
                    "action_source": action_source,
                    "lookup_reason": lookup_reason,
                    "lookup_bucket": lookup_bucket,
                    "lookup_key_found": lookup_key_found,
                    "lookup_candidates_considered": lookup_candidates_considered,
                    "guard_skip_counts": guard_skip_counts,
                    "repair_cost": context.repair_history[-1].get("repair_cost") if context.repair_history else None,
                    "repair_efficiency": context.repair_history[-1].get("repair_efficiency") if context.repair_history else None,
                    "repair_request": repair_request.to_dict(),
                    "repair_plan": repair_plan.to_dict(),
                    **uplift_payload,
                },
                context_id=context.context_id,
                timestamp=datetime.now().isoformat(),
            ),
            Message(
                message_id=str(uuid.uuid4()),
                from_agent=self.agent_id,
                to_agent="validation-001",
                message_type=MessageType.VALIDATION_REQUEST,
                payload={"decision": repaired_decision, "task": context.task_spec},
                context_id=context.context_id,
                timestamp=datetime.now().isoformat(),
            ),
        ]

    def _route_to_image(self, message: Message, context: Context) -> List[Message]:
        self.active_pipelines[context.context_id] = "image"
        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="comfyui-001",
            message_type=MessageType.IMAGE_REQUEST,
            payload=message.payload,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _route_to_voice_io(self, message: Message, context: Context) -> List[Message]:
        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="voice-io-agent",
            message_type=message.message_type,
            payload=message.payload,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _route_to_trading(self, message: Message, context: Context) -> List[Message]:
        self.active_pipelines[context.context_id] = "trading"
        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent=self._trading_agent_id,
            message_type=MessageType.TRADE_ANALYSIS_REQUEST,
            payload=message.payload,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _handle_crypto_intel_signal(self, message: Message, context: Context) -> List[Message]:
        from agents.crypto_intel_agent import CryptoIntelAgent  # lazy import
        signal_type = message.payload.get("signal_type", "unknown")
        confidence  = message.payload.get("confidence", 0.0)
        requires_action = message.payload.get("requires_action", False)
        _log_event = {
            "event":           "crypto_intel_signal",
            "signal_type":     signal_type,
            "confidence":      confidence,
            "requires_action": requires_action,
        }
        messages: List[Message] = [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="logger-001",
            message_type=MessageType.LOG_SUCCESS,
            payload=_log_event,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]
        # Forward high-confidence actionable signals to the trading agent
        if requires_action and signal_type in ("whale", "newproject"):
            messages.append(Message(
                message_id=str(uuid.uuid4()),
                from_agent=self.agent_id,
                to_agent=self._trading_agent_id,
                message_type=MessageType.TRADE_ANALYSIS_REQUEST,
                payload={
                    "trigger":     "crypto_intel",
                    "signal_type": signal_type,
                    "confidence":  confidence,
                    "data":        message.payload.get("data", {}),
                },
                context_id=context.context_id,
                timestamp=datetime.now().isoformat(),
            ))

        # eToro-tagged signals: log pending trade, never auto-execute
        if requires_action and "etoro" in signal_type.lower():
            self._log_pending_etoro_trade(message.payload, context.context_id)

        return messages

    def _log_pending_etoro_trade(
        self, payload: Dict[str, Any], context_id: str
    ) -> None:
        """
        Log a pending eToro trade proposal to logs/pending_trades.log.
        NEVER calls place_order automatically — human must approve via CLI.
        """
        import logging as _logging
        import uuid as _uuid
        _sv_log = _logging.getLogger(__name__)

        data          = payload.get("data", {})
        instrument_id = data.get("instrument_id", 0)
        amount        = data.get("amount", 0.0)
        is_buy        = data.get("is_buy", True)
        mode          = data.get("mode", "demo")
        trade_id      = str(_uuid.uuid4())

        trade_entry = {
            "trade_id":      trade_id,
            "created_at":    datetime.now().isoformat(),
            "instrument_id": instrument_id,
            "amount":        amount,
            "is_buy":        is_buy,
            "mode":          mode,
            "source_signal": payload.get("signal_type", "unknown"),
            "confidence":    payload.get("confidence", 0.0),
            "status":        "pending",
            "context_id":    context_id,
        }

        import json as _json
        import os as _os
        from pathlib import Path as _Path

        log_dir  = _Path(_os.getenv("BABYAI_LOG_DIR", "logs"))
        trade_log = log_dir / "pending_trades.log"
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            with trade_log.open("a", encoding="utf-8") as fh:
                fh.write(_json.dumps(trade_entry, ensure_ascii=True, separators=(",", ":")) + "\n")
            _sv_log.info(
                "etoro_trade_pending trade_id=%s instrument=%s amount=%s buy=%s "
                "— Trade pending human approval — "
                "run: python -m babyai.cli approve-trade %s",
                trade_id, instrument_id, amount, is_buy, trade_id,
            )
        except Exception as exc:
            _sv_log.error("etoro_trade_log_failed error=%s", exc)

    def _handle_infra_gap_signal(self, message: Message, context: Context) -> List[Message]:
        """
        Handle an INFRA_GAP_DETECTED signal from GapDetectorAgent.

        L7 boundary — this handler ONLY logs the gap.
        It does NOT forward to bootstrap, does NOT create agents.
        Human must approve via: python -m babyai.cli approve-gap <gap_id>
        """
        from agents.gap_detector_agent import GapDetectorAgent  # lazy import
        gap_report   = message.payload.get("gap_report", {})
        gap_id       = gap_report.get("gap_id", message.payload.get("gap_id", "unknown"))
        topic        = gap_report.get("topic", "unknown")
        confidence   = gap_report.get("confidence", 0.0)
        gap_type     = gap_report.get("gap_type", "unknown")
        suggested    = gap_report.get("suggested_agent", "")

        import logging
        _log_sv = logging.getLogger(__name__)
        _log_sv.info(
            "infra_gap_received gap_id=%s topic=%s gap_type=%s confidence=%.2f"
            " — Gap proposal logged. Awaiting human approval: %s",
            gap_id, topic, gap_type, confidence, gap_id,
        )

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="logger-001",
            message_type=MessageType.LOG_SUCCESS,
            payload={
                "event":            "infra_gap_received",
                "gap_id":           gap_id,
                "topic":            topic,
                "gap_type":         gap_type,
                "confidence":       confidence,
                "suggested_agent":  suggested,
                "requires_action":  False,
                "status":           "pending_human_approval",
                "note":             (
                    f"Gap proposal logged. Awaiting human approval: {gap_id}. "
                    f"Run: python -m babyai.cli approve-gap {gap_id}"
                ),
            },
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _handle_trade_recommendation(self, message: Message, context: Context) -> List[Message]:
        self.active_pipelines.pop(context.context_id, None)
        symbol = message.payload.get("symbol", "?")
        action = message.payload.get("action", "HOLD")
        confidence = message.payload.get("confidence", 0.0)
        allowed = message.payload.get("policy_allowed", True)
        _log_event = {
            "event": "trade_recommendation",
            "symbol": symbol,
            "action": action,
            "confidence": confidence,
            "policy_allowed": allowed,
            "paper_only": message.payload.get("paper_only", True),
        }
        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="logger-001",
            message_type=MessageType.LOG_SUCCESS,
            payload=_log_event,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _handle_repair_failed(self, message: Message, context: Context) -> List[Message]:
        reason = message.payload.get("reason", "Unknown repair failure")
        context.record_repair("repair_failed", [])
        return [self._create_user_error(context, f"Repair failed: {reason}", {})]

    def _has_unrecoverable_errors(self, error_codes: List[str]) -> bool:
        unrecoverable = {"SCHEMA_VIOLATION", "TYPE_ERROR", "MISSING_REQUIRED_KEY"}
        return any(code in unrecoverable for code in error_codes)

    def _is_repeating_error(self, context: Context, error_codes: List[str]) -> bool:
        if not context.repair_history:
            return False
        last = context.repair_history[-1]
        last_codes = set(last.get("error_codes", []) or [])
        current_codes = set(error_codes)
        if not current_codes:
            return False
        overlap = len(current_codes & last_codes)
        return (overlap / len(current_codes)) > 0.5

    def _extract_error_codes(self, errors: List[Any]) -> List[str]:
        codes: List[str] = []
        for err in errors:
            if isinstance(err, dict) and err.get("code"):
                codes.append(str(err.get("code")))
        return codes

    def _extract_failure_tags(self, payload: Dict[str, Any]) -> List[str]:
        tags: List[str] = []
        errors = payload.get("errors", []) if isinstance(payload, dict) else []
        if isinstance(errors, list):
            for err in errors:
                if isinstance(err, dict) and err.get("code"):
                    tags.append(str(err.get("code")))

        must_missing = ((payload.get("must_include", {}) or {}).get("missing", [])) if isinstance(payload, dict) else []
        if isinstance(must_missing, list):
            for item in must_missing:
                text = str(item)
                head = text.split(":", 1)[0].strip()
                if head:
                    tags.append(head)

        evidence_missing = ((payload.get("evidence", {}) or {}).get("missing_paths", [])) if isinstance(payload, dict) else []
        if isinstance(evidence_missing, list) and evidence_missing:
            tags.append("evidence_missing")

        seen = set()
        out: List[str] = []
        for tag in tags:
            t = str(tag).strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
        return out

    def _update_last_repair_gate_snapshot(self, context: Context, *, hard_tags: List[str]) -> None:
        if not context.repair_history:
            return
        last = context.repair_history[-1]
        before_tags = self._as_str_list(((last.get("gates_before", {}) or {}).get("hard_fail_tags", [])))
        after_tags = [str(t) for t in hard_tags if str(t).strip()]
        last["gates_after"] = {"hard_fail_tags": list(after_tags)}
        if before_tags or after_tags:
            last["hard_gate_flip"] = set(before_tags) != set(after_tags)

    def _as_str_list(self, values: Any) -> List[str]:
        if not isinstance(values, list):
            return []
        out: List[str] = []
        for value in values:
            s = str(value).strip()
            if s:
                out.append(s)
        return out

    def _create_user_error(self, context: Context, message: str, details: Dict[str, Any]) -> Message:
        return Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="user",
            message_type=MessageType.ARCHITECTURE_VALIDATION_FAILED,
            payload={
                "error": message,
                "details": details,
                "repair_history": context.repair_history,
            },
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )

    def _ops_readiness_payload(self, decision: Dict[str, Any] | None) -> Dict[str, Any]:
        if not isinstance(decision, dict):
            return {}
        try:
            from policy.ops_readiness import ops_readiness_status, services_count_from_decision
            status = ops_readiness_status(decision)
            services_count = services_count_from_decision(decision)
            return {
                "ops_readiness": {
                    "required": status.required,
                    "present": status.present,
                    "missing": list(status.missing),
                    "passes": status.passes,
                }
                ,
                "services_count": services_count,
                "required_ops": status.required,
            }
        except Exception:
            return {}

    def _ops_uplift_payload(
        self,
        *,
        decision: Dict[str, Any] | None,
        ops_before: Any,
        ops_after: Any,
    ) -> Dict[str, Any]:
        try:
            from policy.ops_readiness import services_count_from_decision
            services_count = services_count_from_decision(decision or {})
        except Exception:
            services_count = None

        required = None
        if isinstance(ops_after, dict):
            required = ops_after.get("required")
        if required is None and isinstance(ops_before, dict):
            required = ops_before.get("required")

        before_present = ops_before.get("present", 0) if isinstance(ops_before, dict) else 0
        after_present = ops_after.get("present", 0) if isinstance(ops_after, dict) else 0
        ops_uplift = after_present - before_present

        gate_flip = None
        if isinstance(required, int):
            gate_flip = (before_present < required) and (after_present >= required)

        return {
            "services_count": services_count,
            "required_ops": required,
            "ops_uplift": ops_uplift,
            "gate_flip": gate_flip,
        }

    def _repair_cost(
        self,
        *,
        before_decision: Dict[str, Any] | None,
        after_decision: Dict[str, Any] | None,
        ops_actions: Any,
    ) -> Dict[str, Any]:
        before = before_decision if isinstance(before_decision, dict) else {}
        after = after_decision if isinstance(after_decision, dict) else {}

        actions_applied = len(ops_actions) if isinstance(ops_actions, list) else 0

        def _count_added_lines(key: str) -> int:
            before_list = before.get(key, []) if isinstance(before.get(key), list) else []
            after_list = after.get(key, []) if isinstance(after.get(key), list) else []
            before_set = {str(x).strip() for x in before_list if x is not None}
            added = [x for x in after_list if str(x).strip() and str(x).strip() not in before_set]
            return len(added)

        from policy.ops_readiness import extract_ops_signals
        before_signals = extract_ops_signals(before, allow_text_fallback=False)
        after_signals = extract_ops_signals(after, allow_text_fallback=False)
        ops_fields_added = sum(
            1 for k, v in after_signals.items() if v and not before_signals.get(k, False)
        )

        return {
            "actions_applied": actions_applied,
            "verification_plan_lines_added": _count_added_lines("verification_plan"),
            "stop_conditions_lines_added": _count_added_lines("stop_conditions"),
            "ops_readiness_fields_added": ops_fields_added,
        }

    def _repair_efficiency(
        self,
        *,
        repair_cost: Dict[str, Any],
        ops_uplift: Any,
        gate_flip: Any,
    ) -> Dict[str, Any]:
        actions_applied = repair_cost.get("actions_applied", 0) if isinstance(repair_cost, dict) else 0
        uplift_per_action = None
        if isinstance(ops_uplift, (int, float)) and actions_applied:
            uplift_per_action = ops_uplift / actions_applied
        gate_flip_per_action = 0.0
        if gate_flip is True and actions_applied:
            gate_flip_per_action = 1.0 / actions_applied
        return {
            "uplift_per_action": uplift_per_action,
            "gate_flip_per_action": gate_flip_per_action,
        }

    # ------------------------------------------------------------------
    # AgentNode interface (Sprint A6-Adoption)
    # ------------------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "active_pipelines": dict(self.active_pipelines),
            "pipeline_count": len(self.active_pipelines),
        }

    def form_hypothesis(self, observations: List[Any]) -> Hypothesis:
        return Hypothesis(
            hypothesis_id=str(uuid.uuid4()),
            episode_id="",
            based_on_packets=(),
            summary="Supervisor: pipeline routing will proceed without conflict.",
            predicted_effects=("pipeline_routed",),
            reversible=True,
            confidence=0.8,
            timestamp=datetime.utcnow().isoformat(),
        )

    def propose_action(self, hypothesis: Hypothesis) -> ActionProposal:
        return ActionProposal(
            proposal_id=str(uuid.uuid4()),
            episode_id=hypothesis.episode_id,
            proposed_by=self.agent_id,
            reason=hypothesis.summary,
            preconditions=(),
            expected_effects=hypothesis.predicted_effects,
            risk_score=round(1.0 - hypothesis.confidence, 4),
            timestamp=datetime.utcnow().isoformat(),
        )

    def evaluate_outcome(self, outcome: Outcome) -> float:
        return float(outcome.success_score)

    def _handle_analysis_result(self, message: Message, context: Context) -> List[Message]:
        """
        Handle ANALYSIS_COMPLETE or ANALYSIS_FAILED from DeepAnalysisAgent.

        L7 boundary — logs the result only.
        Human reviews the thesis via logs/analysis_results.log.
        No automatic trade proposals.
        """
        import logging as _logging
        _sv_log = _logging.getLogger(__name__)

        payload      = message.payload
        analysis_id  = payload.get("analysis_id", "unknown")
        symbol       = payload.get("symbol", "unknown")
        verdict      = payload.get("verdict", "unknown")
        score        = payload.get("analysis_score", 0.0)
        is_failed    = message.message_type == MessageType.ANALYSIS_FAILED

        if is_failed:
            _sv_log.warning(
                "deep_analysis_failed analysis_id=%s symbol=%s error=%s",
                analysis_id, symbol, payload.get("error", ""),
            )
        else:
            _sv_log.info(
                "deep_analysis_complete analysis_id=%s symbol=%s verdict=%s score=%.2f"
                " — requires_human_review=True",
                analysis_id, symbol, verdict, score,
            )

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="logger-001",
            message_type=MessageType.LOG_SUCCESS if not is_failed else MessageType.LOG_FAILURE,
            payload={
                "event":                 "analysis_result",
                "analysis_id":           analysis_id,
                "symbol":                symbol,
                "verdict":               verdict,
                "analysis_score":        score,
                "requires_action":       False,
                "requires_human_review": True,
                "failed":                is_failed,
            },
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _handle_content_brief_ready(
        self, message: Message, context: Context
    ) -> List[Message]:
        """
        Handle CONTENT_BRIEF_READY from CreativeBriefAgent.

        L7 boundary — logs the brief, never auto-approves.
        Human must run: python -m babyai.cli approve-brief <brief_id>
        """
        payload  = message.payload
        brief_id = payload.get("brief_id", "unknown")
        symbol   = payload.get("symbol", "unknown")
        fmt      = payload.get("recommended_format", "")
        channel  = payload.get("recommended_channel", "")
        score    = payload.get("opportunity_score", 0.0)

        _sv_log.info(
            "content_brief_ready brief_id=%s symbol=%s format=%s channel=%s score=%.2f"
            " — requires_human_review=True",
            brief_id, symbol, fmt, channel, score,
        )

        self._write_pending_brief_log(payload, context.context_id)

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="logger-001",
            message_type=MessageType.LOG_SUCCESS,
            payload={
                "event":                 "content_brief_ready",
                "brief_id":              brief_id,
                "symbol":                symbol,
                "recommended_format":    fmt,
                "recommended_channel":   channel,
                "opportunity_score":     score,
                "requires_action":       False,
                "requires_human_review": True,
                "status":                "pending_approval",
            },
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _handle_content_publish_event(
        self, message: Message, context: Context
    ) -> List[Message]:
        """Handle CONTENT_PUBLISHED or CONTENT_PUBLISH_FAILED — log only."""
        payload    = message.payload
        brief_id   = payload.get("brief_id", "unknown")
        channel    = payload.get("channel", "unknown")
        is_failed  = message.message_type == MessageType.CONTENT_PUBLISH_FAILED

        if is_failed:
            _sv_log.error(
                "content_publish_failed brief_id=%s channel=%s error=%s",
                brief_id, channel, payload.get("error", ""),
            )
        else:
            _sv_log.info(
                "content_published brief_id=%s channel=%s platform_ref=%s",
                brief_id, channel, payload.get("platform_ref", ""),
            )

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="logger-001",
            message_type=MessageType.LOG_FAILURE if is_failed else MessageType.LOG_SUCCESS,
            payload={
                "event":           "content_publish_event",
                "brief_id":        brief_id,
                "channel":         channel,
                "failed":          is_failed,
                "platform_ref":    payload.get("platform_ref", ""),
                "requires_action": False,
            },
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _write_pending_brief_log(
        self, brief: dict, context_id: str
    ) -> None:
        """Write a pending brief to logs/pending_briefs.log (JSON lines)."""
        import json as _json
        import os as _os
        from pathlib import Path as _Path
        log_dir  = _Path(_os.getenv("BABYAI_LOG_DIR", "logs"))
        log_file = log_dir / "pending_briefs.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "status":     "pending_approval",
                "context_id": context_id,
                **brief,
            }
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(_json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")
        except Exception as exc:
            _sv_log.warning("supervisor_brief_log_failed error=%s", exc)

    # ------------------------------------------------------------------
    # Watchdog investigative content pipeline
    # ------------------------------------------------------------------

    def _handle_watchdog_research(
        self, message: Message, context: Context
    ) -> List[Message]:
        """
        Route WATCHDOG_RESEARCH -> watchdog-001.

        WatchdogAgent applies all policy gates internally
        (confidence >= 0.85, >=2 independent sources, no active litigation).
        """
        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="watchdog-001",
            message_type=MessageType.WATCHDOG_RESEARCH,
            payload=message.payload,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _handle_watchdog_script_ready(
        self, message: Message, context: Context
    ) -> List[Message]:
        """
        Handle WATCHDOG_SCRIPT_READY from WatchdogAgent.

        L7 boundary — NEVER auto-approves.
        Script is written to logs/pending_briefs.log and held for human review.
        Human must run: python -m babyai.cli approve-watchdog <script_id>
        """
        import logging as _logging
        _sv_log = _logging.getLogger(__name__)

        payload     = message.payload
        script_id   = payload.get("script_id", "unknown")
        topic_id    = payload.get("topic_id", "unknown")
        platform    = payload.get("platform", "unknown")
        confidence  = float(payload.get("confidence", 0.0))
        content_tag = str(payload.get("content_tag", "GENERAL")).upper()
        is_nsfw     = content_tag == "NSFW"

        _sv_log.info(
            "watchdog_script_ready script_id=%s topic_id=%s platform=%s confidence=%.2f"
            " content_tag=%s — requires_human_review=True — NEVER auto-approve",
            script_id, topic_id, platform, confidence, content_tag,
        )

        # NSFW gets its own log and a separate, labelled gate notification
        if is_nsfw:
            self._write_pending_nsfw_log(payload, context.context_id)
            _sv_log.warning(
                "watchdog_nsfw_gate topic_id=%s — NSFW content requires dedicated human gate",
                topic_id,
            )
        else:
            self._write_pending_watchdog_log(payload, context.context_id)

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="logger-001",
            message_type=MessageType.LOG_SUCCESS,
            payload={
                "event":                 "watchdog_script_ready",
                "script_id":             script_id,
                "topic_id":              topic_id,
                "platform":              platform,
                "confidence":            confidence,
                "content_tag":           content_tag,
                "nsfw_gate_required":    is_nsfw,
                "requires_action":       False,
                "requires_human_review": True,
                "status":                "pending_approval",
                "note": (
                    f"[NSFW] Watchdog script logged. Requires dedicated NSFW gate: {script_id}."
                    if is_nsfw else
                    f"Watchdog script logged. Awaiting human approval: {script_id}. "
                    f"Run: python -m babyai.cli approve-watchdog {script_id}"
                ),
            },
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _handle_watchdog_approved(
        self, message: Message, context: Context
    ) -> List[Message]:
        """
        Handle WATCHDOG_APPROVED (explicit human gate passed) -> scheduler-001.

        Only reachable via human approval — never auto-triggered by any agent.
        """
        import logging as _logging
        _sv_log = _logging.getLogger(__name__)

        payload   = message.payload
        script_id = payload.get("script_id", "unknown")
        topic_id  = payload.get("topic_id", "unknown")

        _sv_log.info(
            "watchdog_approved script_id=%s topic_id=%s — routing to scheduler-001",
            script_id, topic_id,
        )

        return [
            Message(
                message_id=str(uuid.uuid4()),
                from_agent=self.agent_id,
                to_agent="logger-001",
                message_type=MessageType.LOG_SUCCESS,
                payload={
                    "event":           "watchdog_approved",
                    "script_id":       script_id,
                    "topic_id":        topic_id,
                    "requires_action": False,
                    "status":          "approved",
                },
                context_id=context.context_id,
                timestamp=datetime.now().isoformat(),
            ),
            Message(
                message_id=str(uuid.uuid4()),
                from_agent=self.agent_id,
                to_agent="scheduler-001",
                message_type=MessageType.WATCHDOG_APPROVED,
                payload=message.payload,
                context_id=context.context_id,
                timestamp=datetime.now().isoformat(),
            ),
        ]

    # ------------------------------------------------------------------
    # Editorial pipeline handlers
    # ------------------------------------------------------------------

    def _handle_topic_submitted(
        self, message: Message, context: Context
    ) -> List[Message]:
        """
        TOPIC_SUBMITTED → editorial-council-001.
        Council runs 5-voter deliberation; result comes back as
        EDITORIAL_DECISION_READY or HUMAN_APPROVAL_REQUIRED (legal veto).
        """
        return [Message(
            message_id   = str(uuid.uuid4()),
            from_agent   = self.agent_id,
            to_agent     = "editorial-council-001",
            message_type = MessageType.TOPIC_SUBMITTED,
            payload      = message.payload,
            context_id   = context.context_id,
            timestamp    = datetime.now().isoformat(),
        )]

    def _handle_editorial_decision_ready(
        self, message: Message, context: Context
    ) -> List[Message]:
        """
        EDITORIAL_DECISION_READY → production-router-001.
        Router fans out to one package per format and emits
        HUMAN_APPROVAL_REQUIRED for each. Never auto-publish.
        """
        self._write_pending_editorial_log(message.payload or {}, context.context_id)
        return [Message(
            message_id   = str(uuid.uuid4()),
            from_agent   = self.agent_id,
            to_agent     = "production-router-001",
            message_type = MessageType.EDITORIAL_DECISION_READY,
            payload      = message.payload,
            context_id   = context.context_id,
            timestamp    = datetime.now().isoformat(),
        )]

    def _handle_production_routed(
        self, message: Message, context: Context
    ) -> List[Message]:
        """
        PRODUCTION_ROUTED — log that a package is awaiting human approval.
        Supervisor does NOT forward to scheduler until HUMAN_APPROVED arrives.
        """
        self._write_pending_editorial_log(message.payload or {}, context.context_id)
        return []   # Nothing auto-forwarded; wait for human gate

    def _handle_production_complete(
        self, message: Message, context: Context
    ) -> List[Message]:
        """
        PRODUCTION_COMPLETE — output agent finished assembly.
        Still requires HUMAN_APPROVAL_REQUIRED before scheduling.
        L7 boundary: never auto-schedule.
        """
        return [Message(
            message_id   = str(uuid.uuid4()),
            from_agent   = self.agent_id,
            to_agent     = "supervisor",
            message_type = MessageType.HUMAN_APPROVAL_REQUIRED,
            payload      = {
                **(message.payload or {}),
                "human_approval_required": True,
                "reason": "production_complete_awaiting_review",
            },
            context_id   = context.context_id,
            timestamp    = datetime.now().isoformat(),
        )]

    def _handle_human_approved(
        self, message: Message, context: Context
    ) -> List[Message]:
        """
        HUMAN_APPROVED (explicit human gate) → scheduler-001.
        Only reachable via an explicit human action — never auto-triggered.
        """
        return [Message(
            message_id   = str(uuid.uuid4()),
            from_agent   = self.agent_id,
            to_agent     = "scheduler-001",
            message_type = MessageType.SCHEDULE_FOR_POSTING,
            payload      = message.payload,
            context_id   = context.context_id,
            timestamp    = datetime.now().isoformat(),
        )]

    def _write_pending_editorial_log(
        self, payload: dict, context_id: str
    ) -> None:
        """Append a pending editorial package entry to logs/pending_briefs.log."""
        import json as _json
        import os as _os
        from pathlib import Path as _Path
        import logging as _logging

        log_dir  = _Path(_os.getenv("BABYAI_LOG_DIR", "logs"))
        log_file = log_dir / "pending_briefs.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "type":       "editorial_package",
                "status":     "pending_approval",
                "context_id": context_id,
                **payload,
            }
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(_json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")
        except Exception as exc:
            _logging.getLogger(__name__).warning(
                "supervisor_editorial_log_failed error=%s", exc
            )

    def _write_pending_nsfw_log(
        self, payload: dict, context_id: str
    ) -> None:
        """Write NSFW-tagged watchdog scripts to logs/nsfw_pending.log (separate gate queue)."""
        import json as _json
        import os as _os
        from pathlib import Path as _Path
        import logging as _logging

        log_dir  = _Path(_os.getenv("BABYAI_LOG_DIR", "logs"))
        log_file = log_dir / "nsfw_pending.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "type":                  "watchdog_script",
                "status":                "pending_nsfw_approval",
                "content_tag":           "NSFW",
                "nsfw_gate_required":    True,
                "human_approval_required": True,
                "context_id":            context_id,
                **payload,
            }
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(_json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")
        except Exception as exc:
            _logging.getLogger(__name__).warning(
                "supervisor_nsfw_log_failed error=%s", exc
            )

    def _write_pending_watchdog_log(
        self, script: dict, context_id: str
    ) -> None:
        """Write a pending watchdog script to logs/pending_briefs.log (JSON lines)."""
        import json as _json
        import os as _os
        from pathlib import Path as _Path
        import logging as _logging

        log_dir  = _Path(_os.getenv("BABYAI_LOG_DIR", "logs"))
        log_file = log_dir / "pending_briefs.log"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "type":       "watchdog_script",
                "status":     "pending_approval",
                "context_id": context_id,
                **script,
            }
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(_json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")
        except Exception as exc:
            _logging.getLogger(__name__).warning(
                "supervisor_watchdog_log_failed error=%s", exc
            )
