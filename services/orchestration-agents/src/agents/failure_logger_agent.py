"""
FailureLoggerAgent - JSONL logging for Tier 3 learning.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
import json

from agents.base import Agent
from babyai_shared.bus.protocol import Context, Message, MessageType
from ml.telemetry import build_event, compute_case_id
from babyai_shared.privacy.gateway import PrivacyGateway


class FailureLoggerAgent(Agent):
    def __init__(self, agent_id: str = "logger-001", log_path: str = "logs/failures.jsonl") -> None:
        super().__init__(
            agent_id=agent_id,
            role="logger",
            accepts={MessageType.LOG_FAILURE, MessageType.LOG_SUCCESS},
        )
        self.log_path = Path(log_path)
        from policy.constitution_service import get_constitution_service
        get_constitution_service().require("write_path", {"path": self.log_path})
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.gateway = PrivacyGateway.default()

    def process(self, message: Message, context: Context) -> List[Message]:
        payload = message.payload or {}
        task_id = (context.task_spec or {}).get("task_id") or "unknown"

        generator_id = payload.get("generator_id") or None
        seed = payload.get("seed") or None
        case_id = payload.get("case_id") or compute_case_id(str(task_id), seed=seed, generator_id=generator_id)

        event_type = payload.get("event_type")
        if not event_type:
            if message.message_type == MessageType.LOG_SUCCESS:
                event_type = "final_outcome"
            elif payload.get("event") == "repair_proposed":
                event_type = "repair_attempt"
            elif payload.get("stop_reason"):
                event_type = "final_outcome"
            else:
                event_type = "failure"

        passed: bool | None = None
        if event_type in {"final_outcome", "eval_result"}:
            passed = message.message_type == MessageType.LOG_SUCCESS
            if payload.get("passed") is True:
                passed = True
            if payload.get("passed") is False:
                passed = False

        stop_reason = payload.get("stop_reason")
        if message.message_type == MessageType.LOG_SUCCESS and not stop_reason:
            stop_reason = "success"

        gate_before = payload.get("gate_before") or payload.get("ops_before")
        gate_after = payload.get("gate_after") or payload.get("ops_after")
        actions_applied = payload.get("actions_applied") or payload.get("action_applied") or payload.get("ops_actions")

        telemetry = build_event(
            event_type=str(event_type),
            task_id=str(task_id),
            case_id=str(case_id),
            seed=seed,
            generator_id=generator_id,
            passed=passed,
            final_score=payload.get("final_score"),
            repairs_used=context.repair_attempts if event_type == "final_outcome" else None,
            stop_reason=stop_reason,
            gate_before=gate_before if isinstance(gate_before, dict) else None,
            gate_after=gate_after if isinstance(gate_after, dict) else None,
            actions_applied=actions_applied if isinstance(actions_applied, list) else None,
        )

        record: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "context_id": context.context_id,
            "task_id": task_id,
            "decision_id": (context.architecture_decision or {}).get("decision_id"),
            **telemetry,
            "error_codes": payload.get("error_codes", []),
            "missing_must_include": payload.get("missing_must_include", []),
            "repair_type": payload.get("repair_type"),
            "attempt": context.repair_attempts,
            "event": payload.get("event"),
            "services_count": payload.get("services_count"),
            "required_ops": payload.get("required_ops"),
            "ops_uplift": payload.get("ops_uplift"),
            "gate_flip": payload.get("gate_flip"),
            "repair_cost": payload.get("repair_cost"),
            "repair_efficiency": payload.get("repair_efficiency"),
        }

        if "ops_readiness" in payload:
            record["ops_readiness"] = payload.get("ops_readiness")
        if "ops_before" in payload:
            record["ops_before"] = payload.get("ops_before")
        if "ops_after" in payload:
            record["ops_after"] = payload.get("ops_after")
        if "ops_actions" in payload:
            record["ops_actions"] = payload.get("ops_actions")
        if "action_applied" in payload:
            record["action_applied"] = payload.get("action_applied")
        if "still_failing_reasons" in payload:
            record["still_failing_reasons"] = payload.get("still_failing_reasons")
        if "failure_reasons_before" in payload:
            record["failure_reasons_before"] = payload.get("failure_reasons_before")
        if "action_source" in payload:
            record["action_source"] = payload.get("action_source")
        if "lookup_reason" in payload:
            record["lookup_reason"] = payload.get("lookup_reason")
        if "lookup_bucket" in payload:
            record["lookup_bucket"] = payload.get("lookup_bucket")
        if "lookup_key_found" in payload:
            record["lookup_key_found"] = payload.get("lookup_key_found")
        if "lookup_candidates_considered" in payload:
            record["lookup_candidates_considered"] = payload.get("lookup_candidates_considered")
        if "guard_skip_counts" in payload:
            record["guard_skip_counts"] = payload.get("guard_skip_counts")
        constitution_fp = context.attachments.get("constitution_fingerprint")
        constitution_version = context.attachments.get("constitution_version")
        if isinstance(constitution_fp, str) and constitution_fp.strip():
            record["constitution_fingerprint"] = constitution_fp
        if isinstance(constitution_version, str) and constitution_version.strip():
            record["constitution_version"] = constitution_version

        safe_record = self.gateway.scrub_json(record)
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(safe_record, ensure_ascii=True) + "\n")
            f.flush()

        return []
