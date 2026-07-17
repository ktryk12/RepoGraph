from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from jsonschema.exceptions import ValidationError

from policy.must_include_checks import check_must_include_failures
from policy.ops_readiness import (
    ops_readiness_services_threshold,
    ops_readiness_status,
    services_count_from_decision,
)
from policy.rationale import validate_rationale_has_strong_evidence
from policy.scorer import load_rules, score_architecture
from policy.tool_claim_checks import check_tool_claim_failures
from babyai_shared.review.service import get_deterministic_review_service
from verify.run_eval import SCHEMAS_DIR, build_schema_registry, validate_schema


@dataclass
class EvalValidator:
    schema_dir: Path = SCHEMAS_DIR
    rules_path: Path = Path("policy/policy_rules.yaml")

    def validate(self, decision: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        errors: List[str] = []

        registry = build_schema_registry(self.schema_dir)
        rules: Dict[str, Any] = {}

        try:
            validate_schema(task, self.schema_dir / "evaltask.schema.json", registry)
        except Exception as e:
            errors.append(self._fmt_schema_error("evaltask.schema.json", e))

        try:
            validate_schema(task["spec"], self.schema_dir / "problemspec.schema.json", registry)
        except Exception as e:
            errors.append(self._fmt_schema_error("problemspec.schema.json", e))

        try:
            validate_schema(decision, self.schema_dir / "architecturedecision.schema.json", registry)
        except Exception as e:
            errors.append(self._fmt_schema_error("architecturedecision.schema.json", e))

        try:
            rules = load_rules(str(self.rules_path))
        except Exception:
            rules = {}

        evidence_errors = []
        try:
            spec = task.get("spec", {}) if isinstance(task, dict) else {}
            evidence_errors = validate_rationale_has_strong_evidence(spec, decision)
        except Exception as e:
            errors.append(f"evidence_error: {type(e).__name__}: {e}")

        must_missing = []
        must_failures: List[Dict[str, Any]] = []
        try:
            raw_failures = check_must_include_failures(task, decision)
            must_missing = [f"{item.tag}: {item.message}" for item in raw_failures]
            must_failures = [item.to_dict() for item in raw_failures]
        except Exception as e:
            errors.append(f"must_include_error: {type(e).__name__}: {e}")

        tool_claim_failures: List[Dict[str, Any]] = []
        try:
            raw_tool_failures = check_tool_claim_failures(decision)
            tool_claim_failures = [item.to_dict() for item in raw_tool_failures]
        except Exception as e:
            errors.append(f"tool_claim_gate_error: {type(e).__name__}: {e}")

        review_failures: List[Dict[str, Any]] = []
        try:
            review_report = get_deterministic_review_service().review(
                {
                    "task": task,
                    "decision": decision,
                    "dependencies": decision.get("dependencies"),
                    "spdx_allowlist": decision.get("spdx_allowlist"),
                }
            )
            review_failures = [item.to_dict() for item in review_report.findings]
        except Exception as e:
            review_failures = [
                {
                    "rule": "deterministic_review_service",
                    "tag": "deterministic_review_error",
                    "message": f"{type(e).__name__}: {e}",
                    "evidence_ref": "$",
                    "severity": "error",
                }
            ]

        # Ops readiness (structured) for distributed decisions
        try:
            topo = decision.get("topology", {}) if isinstance(decision, dict) else {}
            core = topo.get("core") if isinstance(topo, dict) else None
            style = str(decision.get("chosen_style", "")).strip().lower()
            distributedish = (core == "distributed_core") or (
                style in {"microservices", "hybrid", "distributed_core", "event_driven", "layered"}
            )
            services_count = services_count_from_decision(decision)
            threshold = ops_readiness_services_threshold(rules, default=3)
            if distributedish and services_count >= threshold:
                status = ops_readiness_status(decision, rules=rules)
                if not status.passes:
                    errors.append(
                        "ops_readiness_missing_or_insufficient: "
                        f"services={services_count} required={status.required} present={status.present} missing={status.missing}"
                    )
        except Exception as e:
            errors.append(f"ops_readiness_error: {type(e).__name__}: {e}")

        scores = None
        penalties = None
        try:
            s = score_architecture(task, decision, rules=rules)
            scores = {
                "functional": s.functional,
                "security": s.security,
                "architecture_fit": s.architecture_fit,
                "total": s.total,
            }
            penalties = list(s.penalties)
        except Exception:
            # scoring is best-effort for this thin wrapper
            pass

        passed = (not errors) and (not evidence_errors) and (not must_missing) and (not tool_claim_failures) and (not review_failures)

        return {
            "passed": passed,
            "errors": errors,
            "evidence_errors": evidence_errors,
            "must_include_missing": must_missing,
            "must_include_failures": must_failures,
            "tool_claim_failures": tool_claim_failures,
            "review_failures": review_failures,
            "scores": scores,
            "penalties": penalties,
        }

    @staticmethod
    def _fmt_schema_error(schema_name: str, err: Exception) -> str:
        if isinstance(err, ValidationError):
            return f"schema_error:{schema_name} {err.message} (at {list(err.absolute_path)})"
        return f"schema_error:{schema_name} {type(err).__name__}: {err}"
