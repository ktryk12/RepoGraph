"""
Validation Agent - validates decisions against schema and policy checks.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
import uuid

from agents.base import Agent
from policy.evaluator import EvalValidator
from babyai_shared.bus.protocol import Context, Message, MessageType
from babyai_shared.contracts.agent_node import AgentNode
from babyai_shared.core.action_proposal import ActionProposal
from babyai_shared.core.hypothesis import Hypothesis
from babyai_shared.core.outcome import Outcome


class ValidationAgent(AgentNode, Agent):
    def __init__(self, agent_id: str = "validation-001") -> None:
        super().__init__(
            agent_id=agent_id,
            role="validation",
            accepts={MessageType.VALIDATION_REQUEST},
        )
        self.evaluator = EvalValidator()

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type != MessageType.VALIDATION_REQUEST:
            return []

        decision = message.payload.get("decision") or context.architecture_decision
        task = message.payload.get("task") or context.task_spec

        payload = self.validate_decision(decision, task)
        context.validation_results = payload
        return [self._emit_complete(message, context, payload=payload)]

    def validate_decision(self, decision: Any, task: Any) -> Dict[str, Any]:
        if not isinstance(decision, dict) or not isinstance(task, dict):
            return {
                "passed": False,
                "errors": [{
                    "code": "MISSING_REQUIRED_KEY",
                    "path": "",
                    "msg": "Missing task or decision",
                    "severity": "error",
                }],
                "must_include": {"missing": [], "present": []},
                "evidence": {"missing_paths": [], "present_paths": []},
                "scores": None,
                "penalties": None,
            }

        result = self.evaluator.validate(decision, task)
        return self._normalize_eval_result(result)

    def validate_swarm_output(
        self,
        decision: Any,
        task: Any,
        *,
        expert_results: List[Any] | None = None,
        combine_meta: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = self.validate_decision(decision, task)
        errors = list(payload.get("errors", []) or [])

        for res in expert_results or []:
            if res.status != "ok":
                errors.append({
                    "code": "EXPERT_EXECUTION_FAILED",
                    "path": "$",
                    "msg": f"expert={res.expert_id} status={res.status}",
                    "severity": "error",
                })
            if not res.validation.valid:
                errors.append({
                    "code": "EXPERT_RESULT_INVALID",
                    "path": "$.validation",
                    "msg": f"expert={res.expert_id} produced invalid result",
                    "severity": "error",
                })

        conflicts = (combine_meta or {}).get("conflicts", []) or []
        for item in conflicts:
            key = str(item.get("key", ""))
            winner = str(item.get("chosen_expert", ""))
            loser = str(item.get("rejected_expert", ""))
            errors.append({
                "code": "SWARM_CONFLICT",
                "path": f"$.{key}" if key else "$",
                "msg": f"Conflict resolved by winner={winner} over rejected={loser}",
                "severity": "warning",
            })

        hard_errors = [e for e in errors if isinstance(e, dict) and e.get("severity") == "error"]
        payload["errors"] = errors
        payload["passed"] = bool(payload.get("passed")) and not hard_errors
        return payload

    def _normalize_eval_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        errors: List[Dict[str, Any]] = []
        for err in result.get("errors", []):
            code = "VALIDATION_ERROR"
            msg = str(err)
            if isinstance(err, str) and err.startswith("schema_error:"):
                code = "SCHEMA_VIOLATION"
            elif isinstance(err, str) and err.startswith("ops_readiness_missing_or_insufficient"):
                code = "OPS_READINESS_MISSING"
            errors.append({"code": code, "path": "", "msg": msg, "severity": "error"})

        must_missing = list(result.get("must_include_missing", []) or [])
        if must_missing:
            errors.append({
                "code": "MISSING_MUST_INCLUDE",
                "path": "verification_plan",
                "msg": ",".join(must_missing),
                "severity": "error",
            })

        evidence_missing = list(result.get("evidence_errors", []) or [])
        if evidence_missing:
            errors.append({
                "code": "EVIDENCE_CONSISTENCY",
                "path": "rationale",
                "msg": "; ".join(str(e) for e in evidence_missing),
                "severity": "error",
            })

        tool_claim_failures = list(result.get("tool_claim_failures", []) or [])
        for item in tool_claim_failures:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag") or "").strip()
            message = str(item.get("message") or "").strip()
            if not tag:
                continue
            errors.append({
                "code": tag,
                "path": str(item.get("evidence_ref") or ""),
                "msg": message or "tool claim gate failed",
                "severity": "error",
            })

        review_failures = list(result.get("review_failures", []) or [])
        for item in review_failures:
            if not isinstance(item, dict):
                continue
            tag = str(item.get("tag") or "").strip()
            if not tag:
                continue
            errors.append({
                "code": self._map_review_tag_to_error_code(tag),
                "path": str(item.get("evidence_ref") or ""),
                "msg": str(item.get("message") or "deterministic review failed"),
                "severity": "error",
            })

        return {
            "passed": bool(result.get("passed")) and not errors,
            "errors": errors,
            "must_include": {
                "missing": must_missing,
                "present": [],
            },
            "evidence": {
                "missing_paths": evidence_missing,
                "present_paths": [],
            },
            "tool_claims": {
                "failures": tool_claim_failures,
            },
            "deterministic_review": {
                "failures": review_failures,
            },
            "scores": result.get("scores"),
            "penalties": result.get("penalties"),
        }

    def _map_review_tag_to_error_code(self, tag: str) -> str:
        raw = str(tag).strip().lower()
        if raw in {"missing_threat_model", "missing_rate_limiting", "missing_audit_log", "missing_sast_pass"}:
            return "MISSING_MUST_INCLUDE"
        if raw in {"ops_missing_runbook", "ops_missing_monitoring_signals", "ops_missing_rollback"}:
            return "OPS_READINESS_MISSING"
        if raw in {"layering_violation", "service_sprawl"}:
            return "FORBIDDEN_STYLE"
        if raw == "deterministic_review_error":
            return "SCHEMA_VIOLATION"
        if raw == "license_risk":
            return "LICENSE_RISK"
        return str(tag)

    def _emit_complete(
        self,
        message: Message,
        context: Context,
        *,
        payload: Dict[str, Any],
    ) -> Message:
        return Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type=MessageType.VALIDATION_COMPLETE,
            payload=payload,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )

    # ------------------------------------------------------------------
    # AgentNode interface (Sprint A6-Adoption)
    # ------------------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "role": self.role,
        }

    def form_hypothesis(self, observations: List[Any]) -> Hypothesis:
        return Hypothesis(
            hypothesis_id=str(uuid.uuid4()),
            episode_id="",
            based_on_packets=(),
            summary="Validation: decision will conform to schema and policy.",
            predicted_effects=("validation_passed",),
            reversible=True,
            confidence=0.9,
            timestamp=datetime.utcnow().isoformat(),
        )

    def propose_action(self, hypothesis: Hypothesis) -> ActionProposal:
        return ActionProposal(
            proposal_id=str(uuid.uuid4()),
            episode_id=hypothesis.episode_id,
            proposed_by=self.agent_id,
            reason=hypothesis.summary,
            preconditions=("decision_available", "task_spec_available"),
            expected_effects=hypothesis.predicted_effects,
            risk_score=round(1.0 - hypothesis.confidence, 4),
            timestamp=datetime.utcnow().isoformat(),
        )

    def evaluate_outcome(self, outcome: Outcome) -> float:
        return float(outcome.success_score)
