# verify/run_eval.py
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from referencing import Registry, Resource

from ml.judges.aggregate import get_judge_aggregator_service
from ml.judges.contracts import JudgeFailure, JudgeGateResult, JudgeReport
from ml.judges.fingerprint import with_judge_report_fingerprint
from policy.must_include_checks import check_must_include_failures
from policy.perf_budget_service import budgeted_call
from policy.reason_taxonomy_service import get_reason_taxonomy_service
from policy.governance_smoke import (
    GOVERNANCE_HELLO_WORLD_TEMPLATE_ID,
    evaluate_governance_hello_world_artifact,
    is_governance_hello_world_task,
)
from policy.tool_claim_checks import check_tool_claim_failures
from policy.scorer import (
    GateFailure,
    GateResult,
    build_scorecard,
    load_rules,
    score_architecture,
)
from policy.rationale import rationale_evidence_summary
from babyai_shared.review.service import get_deterministic_review_service
from babyai_shared.core.logging_milestones import log_milestone


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"
logger = logging.getLogger(__name__)
_SERVICE_NAME = "verify"
_COMPONENT = "verify.run_eval"


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_schema_registry(schema_dir: Path) -> Registry:
    """
    Build an in-memory registry of all local schemas so $ref resolution never hits the network.
    We register each schema under:
      - its $id (if present)
      - its filename (e.g. problemspec.schema.json)
      - an alias matching your example.local pattern (robust for relative refs under that base)
    """
    registry = Registry()
    for p in schema_dir.glob("*.schema.json"):
        schema = load_json(p)
        res = Resource.from_contents(schema)

        # Register by filename (helps in many setups)
        registry = registry.with_resource(p.name, res)

        # Register by $id (this is what jsonschema resolves to in your case)
        sid = schema.get("$id")
        if isinstance(sid, str) and sid:
            registry = registry.with_resource(sid, res)

        # Extra alias: matches the base you used in $id values (example.local)
        alias = f"https://example.local/schemas/{p.name}"
        registry = registry.with_resource(alias, res)

    return registry


def validate_schema(instance: Dict[str, Any], schema_path: Path, registry: Registry) -> None:
    schema = load_json(schema_path)
    Draft202012Validator(schema, registry=registry).validate(instance)


def _schema_fail(task: Dict[str, Any], decision: Dict[str, Any], *, where: str, err: Exception) -> Dict[str, Any]:
    """
    Convert schema validation crashes into a deterministic FAIL result,
    so redteam suite reports FAIL (not ERROR).
    """
    task_id = task.get("task_id") if isinstance(task, dict) else None
    decision_id = None
    if isinstance(decision, dict):
        decision_id = decision.get("decision_id")

    msg = str(err)
    if isinstance(err, ValidationError):
        # keep it compact but informative
        msg = f"{err.message} (at {list(err.absolute_path)})"

    schema_gate = GateResult(
        name="schema",
        passed=False,
        failures=[
            GateFailure(
                tag="schema_invalid",
                message=f"{type(err).__name__}: {msg}",
                evidence_ref="$",
            )
        ],
        metrics={"where": str(where)},
    )
    scorecard = build_scorecard(
        hard_gates=[schema_gate],
        soft_score=0.0,
        total_score=0.0,
    )
    scorecard_payload = scorecard.to_dict()
    judge_report = with_judge_report_fingerprint(
        _judge_report_from_scorecard(
            task_id=task_id,
            decision_id=decision_id,
            scorecard=scorecard_payload,
            soft_score=0.0,
            judge_id="court",
        )
    )
    judge_aggregate = get_judge_aggregator_service().aggregate(
        [judge_report],
        iteration=1,
        score=0.0,
    )
    hard_fail_tags = list(judge_aggregate.hard_fail_tags)
    get_reason_taxonomy_service().require(
        hard_fail_tags,
        pack_name="deterministic_review.hard_fail_tags",
    )
    return {
        "task_id": task_id,
        "decision_id": decision_id,
        "scorecard": scorecard_payload,
        "hard_pass": False,
        "hard_fail_tags": hard_fail_tags,
        "scores": {
            "functional": 0.0,
            "security": 0.0,
            "architecture_fit": 0.0,
            "total": 0.0,
            "components": {
                "functional": 0.0,
                "security": 0.0,
                "architecture_fit": 0.0,
            },
        },
        "failure_reasons": ["schema_validation_failed"],
        "penalties": [f"schema_validation_failed:{where} | {msg}"],
        "evidence_errors": [],
        "must_include_missing": [],
        "must_include_failures": [],
        "rationale_signals": [],
        "judge_report": judge_report,
        "judge_aggregate": judge_aggregate.to_dict(),
        "error": f"{type(err).__name__}: {msg}",
    }


def _hard_fail_tags_from_scorecard(scorecard: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    gates = scorecard.get("hard_gates") if isinstance(scorecard, dict) else []
    if not isinstance(gates, list):
        return tags
    for gate in gates:
        if not isinstance(gate, dict):
            continue
        if bool(gate.get("passed", False)):
            continue
        failures = gate.get("failures")
        if not isinstance(failures, list):
            continue
        for failure in failures:
            if not isinstance(failure, dict):
                continue
            tag = str(failure.get("tag") or "").strip()
            if tag:
                tags.append(tag)
    return tags


def _judge_report_from_scorecard(
    *,
    task_id: str | None,
    decision_id: str | None,
    scorecard: Dict[str, Any],
    soft_score: float,
    judge_id: str,
) -> JudgeReport:
    raw_gates = scorecard.get("hard_gates", []) if isinstance(scorecard, dict) else []
    gates: List[JudgeGateResult] = []
    if isinstance(raw_gates, list):
        for raw_gate in raw_gates:
            if not isinstance(raw_gate, dict):
                continue
            raw_failures = raw_gate.get("failures", [])
            failures: List[JudgeFailure] = []
            if isinstance(raw_failures, list):
                for raw_failure in raw_failures:
                    if not isinstance(raw_failure, dict):
                        continue
                    tag = str(raw_failure.get("tag") or "").strip()
                    message = str(raw_failure.get("message") or "").strip()
                    if not tag or not message:
                        continue
                    failures.append(
                        JudgeFailure(
                            tag=tag,
                            message=message,
                            evidence_ref=(str(raw_failure.get("evidence_ref")) if raw_failure.get("evidence_ref") else None),
                            severity=str(raw_failure.get("severity") or "error"),
                        )
                    )
            gates.append(
                JudgeGateResult(
                    name=str(raw_gate.get("name") or "unknown_gate"),
                    passed=bool(raw_gate.get("passed", False)),
                    failures=failures,
                    metrics=dict(raw_gate.get("metrics", {}) if isinstance(raw_gate.get("metrics"), dict) else {}),
                )
            )

    if not gates:
        gates = [
            JudgeGateResult(
                name="unknown_gate",
                passed=False,
                failures=[JudgeFailure(tag="schema_invalid", message="missing hard_gates in scorecard", evidence_ref="$.scorecard")],
                metrics={},
            )
        ]
    return JudgeReport(
        judge_id=str(judge_id),
        run_id=None,
        case_id=(str(task_id) if isinstance(task_id, str) and task_id.strip() else None),
        subject_id=(str(decision_id) if isinstance(decision_id, str) and decision_id.strip() else None),
        hard_pass=bool(scorecard.get("hard_pass", False)),
        hard_fail_tags=_hard_fail_tags_from_scorecard(scorecard),
        hard_gates=gates,
        metrics={
            "soft_score": float(soft_score),
            "total_score": float(scorecard.get("total_score", soft_score) if isinstance(scorecard.get("total_score"), (int, float)) else soft_score),
        },
        metadata={"source": "verify.run_eval"},
    )


def _build_deterministic_review_gate(task: Dict[str, Any], decision: Dict[str, Any]) -> tuple[GateResult, Dict[str, Any]]:
    try:
        report = get_deterministic_review_service().review(
            {
                "task": task,
                "decision": decision,
                "dependencies": decision.get("dependencies"),
                "spdx_allowlist": decision.get("spdx_allowlist"),
            }
        )
        gate = GateResult(
            name="deterministic_review",
            passed=bool(report.hard_pass),
            failures=[
                GateFailure(
                    tag=str(item.tag),
                    message=str(item.message),
                    evidence_ref=(str(item.evidence_ref) if isinstance(item.evidence_ref, str) and item.evidence_ref else None),
                )
                for item in report.findings
            ],
            metrics=dict(report.metrics),
        )
        return gate, report.to_dict()
    except Exception as exc:
        gate = GateResult(
            name="deterministic_review",
            passed=False,
            failures=[
                GateFailure(
                    tag="deterministic_review_error",
                    message=f"{type(exc).__name__}: {exc}",
                    evidence_ref="$",
                )
            ],
            metrics={"error": f"{type(exc).__name__}: {exc}"},
        )
        return gate, {
            "schema_version": 1,
            "hard_pass": False,
            "deterministic": True,
            "findings": [
                {
                    "rule": "deterministic_review_service",
                    "tag": "deterministic_review_error",
                    "message": f"{type(exc).__name__}: {exc}",
                    "evidence_ref": "$",
                    "severity": "error",
                }
            ],
            "fail_tags": ["deterministic_review_error"],
            "metrics": {"total_findings": 1, "rule_counts": {"deterministic_review_service": 1}},
            "input_fingerprint": "0" * 64,
        }


def run_single(task_path: Path, decision_path: Path) -> Dict[str, Any]:
    return budgeted_call(
        "judge.run_eval.single",
        lambda: _run_single_impl(task_path, decision_path),
        metadata={"task_path": str(task_path), "decision_path": str(decision_path)},
    )


def _run_single_impl(task_path: Path, decision_path: Path) -> Dict[str, Any]:
    task = load_json(task_path)
    decision = load_json(decision_path)

    registry = build_schema_registry(SCHEMAS_DIR)

    # Schema validations (offline) — convert ValidationError into FAIL result
    try:
        validate_schema(task, SCHEMAS_DIR / "evaltask.schema.json", registry)
        validate_schema(task["spec"], SCHEMAS_DIR / "problemspec.schema.json", registry)
        validate_schema(decision, SCHEMAS_DIR / "architecturedecision.schema.json", registry)
    except (ValidationError, KeyError, TypeError) as e:
        # KeyError/TypeError: also treat as schema-ish invalid inputs
        where = "unknown"
        if isinstance(e, ValidationError):
            # Best effort: identify which schema step likely failed by reading message context is unreliable,
            # so just label generically.
            where = "validation"
        log_milestone(
            logger,
            "schema_validation_failed",
            service_name=_SERVICE_NAME,
            component=_COMPONENT,
            decision_id=str(decision.get("decision_id") or ""),
            context_id=str(decision.get("context_id") or ""),
            episode_id=str(decision.get("decision_id") or ""),
            event_type="eval.schema",
            topic="",
            event_id="",
            trace_id="",
            where=where,
            error_type=type(e).__name__,
            error_summary=str(e),
        )
        return _schema_fail(task, decision, where=where, err=e)

    if is_governance_hello_world_task(task):
        return _run_governance_hello_world_eval(task=task, decision=decision)

    review_gate, review_report = _build_deterministic_review_gate(task, decision)

    # Rationale evidence enforcement (hard gate)
    evidence_summary = rationale_evidence_summary(task["spec"], decision)
    evidence_errors = list(evidence_summary.get("errors") or [])
    evidence_gate_failures: List[GateFailure] = []
    if evidence_errors:
        for err in evidence_errors:
            evidence_gate_failures.append(
                GateFailure(
                    tag="evidence_missing",
                    message=str(err),
                    evidence_ref="$.rationale",
                )
            )
    evidence_gate = GateResult(
        name="evidence",
        passed=not evidence_gate_failures,
        failures=evidence_gate_failures,
        metrics={
            "strong_count": int(evidence_summary.get("strong_count", 0) or 0),
            "required_count": int(evidence_summary.get("required_count", 0) or 0),
            "min_weight": float(evidence_summary.get("min_weight", 0.7) or 0.7),
        },
    )

    # Must-include enforcement (hard facts, anti-camouflage)
    must_include_failures = check_must_include_failures(task, decision)
    must_include_missing = [f"{item.tag}: {item.message}" for item in must_include_failures]
    must_gate = GateResult(
        name="must_include",
        passed=not must_include_failures,
        failures=[
            GateFailure(
                tag=str(item.tag),
                message=str(item.message),
                evidence_ref=(str(item.evidence_ref) if isinstance(item.evidence_ref, str) and item.evidence_ref else None),
            )
            for item in must_include_failures
        ],
        metrics={"missing_count": int(len(must_include_failures))},
    )

    # Tool-claim enforcement (hard gate): no tool claims without evidence pack.
    tool_claim_failures = check_tool_claim_failures(decision, artifact_root=REPO_ROOT / "artifacts")
    tool_claim_gate = GateResult(
        name="tool_claims",
        passed=not tool_claim_failures,
        failures=[
            GateFailure(
                tag=str(item.tag),
                message=str(item.message),
                evidence_ref=(str(item.evidence_ref) if isinstance(item.evidence_ref, str) and item.evidence_ref else None),
            )
            for item in tool_claim_failures
        ],
        metrics={"failure_count": int(len(tool_claim_failures))},
    )

    # Score
    rules = load_rules(str(REPO_ROOT / "policy" / "policy_rules.yaml"))
    decision_id = str(decision.get("decision_id") or "")
    artifact_count = _artifact_count(decision)
    log_milestone(
        logger,
        "eval_scoring_started",
        service_name=_SERVICE_NAME,
        component=_COMPONENT,
        decision_id=decision_id,
        context_id=str(decision.get("context_id") or ""),
        episode_id=decision_id,
        event_type="eval.scoring",
        topic="",
        event_id="",
        trace_id=str(decision.get("trace_id") or ""),
        artifact_count=int(artifact_count),
        task_id=str(task.get("task_id") or ""),
    )
    score_result, scoring_failure_reasons = _safe_score_architecture(
        task=task,
        decision=decision,
        rules=rules,
        decision_id=decision_id,
        artifact_count=artifact_count,
    )
    scores_payload = _build_scores_payload(
        task=task,
        score_result=score_result,
    )
    log_milestone(
        logger,
        "eval_scoring_result",
        service_name=_SERVICE_NAME,
        component=_COMPONENT,
        decision_id=decision_id,
        context_id=str(decision.get("context_id") or ""),
        episode_id=decision_id,
        event_type="eval.scoring",
        topic="",
        event_id="",
        trace_id=str(decision.get("trace_id") or ""),
        score_total=float(scores_payload.get("total", 0.0)),
        components_keys=sorted(
            [
                str(k)
                for k in (
                    scores_payload.get("components", {})
                    if isinstance(scores_payload.get("components"), dict)
                    else {}
                ).keys()
            ]
        ),
    )
    result = score_result
    schema_gate = GateResult(
        name="schema",
        passed=True,
        failures=[],
        metrics={"where": "validation"},
    )
    ops_gates = list(result.scorecard.hard_gates) if getattr(result, "scorecard", None) else []
    scorecard = build_scorecard(
        hard_gates=[schema_gate, review_gate, evidence_gate, must_gate, tool_claim_gate, *ops_gates],
        soft_score=float(scores_payload.get("total", 0.0)),
        total_score=float(scores_payload.get("total", 0.0)),
    )
    scorecard_payload = scorecard.to_dict()
    judge_report = with_judge_report_fingerprint(
        _judge_report_from_scorecard(
            task_id=str(task.get("task_id") or ""),
            decision_id=str(decision.get("decision_id") or ""),
            scorecard=scorecard_payload,
            soft_score=float(scores_payload.get("total", 0.0)),
            judge_id="court",
        )
    )
    judge_aggregate = get_judge_aggregator_service().aggregate(
        [judge_report],
        iteration=1,
        score=float(scores_payload.get("total", 0.0)),
    )
    hard_pass = bool(judge_aggregate.hard_pass)
    hard_fail_tags = list(judge_aggregate.hard_fail_tags)
    get_reason_taxonomy_service().require(
        hard_fail_tags,
        pack_name="deterministic_review.hard_fail_tags",
    )

    return {
        "task_id": task["task_id"],
        "decision_id": decision.get("decision_id"),
        "scorecard": scorecard_payload,
        "hard_pass": hard_pass,
        "hard_fail_tags": hard_fail_tags,
        "scores": scores_payload,
        "failure_reasons": scoring_failure_reasons,
        "penalties": list(getattr(result, "penalties", []) or []),
        "evidence_errors": evidence_errors,
        "must_include_missing": must_include_missing,
        "must_include_failures": [item.to_dict() for item in must_include_failures],
        "tool_claim_failures": [item.to_dict() for item in tool_claim_failures],
        "deterministic_review": review_report,
        "judge_report": judge_report,
        "judge_aggregate": judge_aggregate.to_dict(),
        "rationale_signals": list(getattr(result, "rationale", []) or []),
    }


def _run_governance_hello_world_eval(*, task: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    task_id = str(task.get("task_id") or "")
    decision_id = str(decision.get("decision_id") or "")
    artifact_payload = decision.get("governance_smoke_payload")
    if not isinstance(artifact_payload, dict):
        artifact_payload = decision.get("governance_smoke")
    smoke_eval = evaluate_governance_hello_world_artifact(
        artifact_payload if isinstance(artifact_payload, dict) else None
    )
    passed = bool(smoke_eval.get("passed", False))
    score_total = float(smoke_eval.get("score", 0.0))
    components_raw = smoke_eval.get("components")
    components = dict(components_raw) if isinstance(components_raw, dict) else {}
    failure_reasons = [str(item) for item in list(smoke_eval.get("failure_reasons") or []) if str(item)]
    if not passed and not failure_reasons:
        failure_reasons = ["governance_eval_failed"]
    scorecard = build_scorecard(
        hard_gates=[
            GateResult(
                name="governance_hello_world",
                passed=passed,
                failures=[] if passed else [GateFailure(tag="governance_payload_mismatch", message="governance artifact payload mismatch", evidence_ref="$.governance_smoke_payload")],
                metrics={"template": GOVERNANCE_HELLO_WORLD_TEMPLATE_ID},
            )
        ],
        soft_score=score_total,
        total_score=score_total,
    )
    scorecard_payload = scorecard.to_dict()
    judge_report = with_judge_report_fingerprint(
        _judge_report_from_scorecard(
            task_id=task_id,
            decision_id=decision_id,
            scorecard=scorecard_payload,
            soft_score=score_total,
            judge_id="court",
        )
    )
    judge_aggregate = get_judge_aggregator_service().aggregate(
        [judge_report],
        iteration=1,
        score=score_total,
    )
    hard_fail_tags = list(judge_aggregate.hard_fail_tags)
    get_reason_taxonomy_service().require(
        hard_fail_tags,
        pack_name="deterministic_review.hard_fail_tags",
    )
    return {
        "task_id": task_id,
        "decision_id": decision_id,
        "scorecard": scorecard_payload,
        "hard_pass": bool(judge_aggregate.hard_pass),
        "hard_fail_tags": hard_fail_tags,
        "scores": {
            "functional": float(components.get("functional", 0.0)),
            "security": float(components.get("security", 0.0)),
            "architecture_fit": float(components.get("architecture_fit", 0.0)),
            "total": score_total,
            "components": {
                "functional": float(components.get("functional", 0.0)),
                "security": float(components.get("security", 0.0)),
                "architecture_fit": float(components.get("architecture_fit", 0.0)),
            },
        },
        "failure_reasons": failure_reasons,
        "penalties": [],
        "evidence_errors": [],
        "must_include_missing": [],
        "must_include_failures": [],
        "tool_claim_failures": [],
        "deterministic_review": {"template": GOVERNANCE_HELLO_WORLD_TEMPLATE_ID},
        "judge_report": judge_report,
        "judge_aggregate": judge_aggregate.to_dict(),
        "rationale_signals": [],
    }


def _artifact_count(decision: Dict[str, Any]) -> int:
    artifacts = decision.get("artifacts")
    if isinstance(artifacts, list):
        return len(artifacts)
    if isinstance(artifacts, dict):
        return len(artifacts)
    return 0


def _missing_scoring_inputs(task: Dict[str, Any], decision: Dict[str, Any], *, artifact_count: int) -> Dict[str, Any]:
    missing: List[str] = []
    if not isinstance(task.get("spec"), dict):
        missing.append("task.spec")
    if not isinstance(task.get("expected"), dict):
        missing.append("task.expected")
    if not isinstance(decision.get("chosen_style"), str):
        missing.append("decision.chosen_style")
    if not isinstance(decision.get("topology"), dict):
        missing.append("decision.topology")
    if int(artifact_count) <= 0:
        missing.append("decision.artifacts")
    return {
        "missing_fields": missing,
        "artifact_count": int(artifact_count),
        "decision_keys": sorted([str(k) for k in decision.keys()]),
    }


def _safe_score_architecture(
    *,
    task: Dict[str, Any],
    decision: Dict[str, Any],
    rules: Dict[str, Any],
    decision_id: str,
    artifact_count: int,
) -> tuple[Any, List[str]]:
    failure_reasons: List[str] = []
    try:
        result = score_architecture(task, decision, rules=rules)
        return result, failure_reasons
    except Exception:
        missing = _missing_scoring_inputs(task, decision, artifact_count=artifact_count)
        missing_fields = list(missing.get("missing_fields", []))
        log_milestone(
            logger,
            "eval_scoring_missing_inputs",
            service_name=_SERVICE_NAME,
            component=_COMPONENT,
            decision_id=decision_id,
            context_id=str(decision.get("context_id") or ""),
            episode_id=decision_id,
            event_type="eval.scoring",
            topic="",
            event_id="",
            trace_id=str(decision.get("trace_id") or ""),
            artifact_count=int(missing.get("artifact_count", 0)),
            missing_fields=missing_fields[:8],
            decision_keys=list(missing.get("decision_keys", []))[:20],
        )
        failure_reasons.append("missing_scoring_inputs")
        fallback = type(
            "ScoreFallback",
            (),
            {
                "functional": 0.0,
                "security": 0.0,
                "architecture_fit": 0.0,
                "total": 0.0,
                "penalties": [f"scoring_missing_inputs:{','.join(list(missing.get('missing_fields', [])))}"],
                "rationale": [],
                "scorecard": None,
            },
        )()
        return fallback, failure_reasons


def _build_scores_payload(*, task: Dict[str, Any], score_result: Any) -> Dict[str, Any]:
    functional = float(getattr(score_result, "functional", 0.0) or 0.0)
    security = float(getattr(score_result, "security", 0.0) or 0.0)
    architecture_fit = float(getattr(score_result, "architecture_fit", 0.0) or 0.0)
    total = float(getattr(score_result, "total", 0.0) or 0.0)
    components = {
        "functional": functional,
        "security": security,
        "architecture_fit": architecture_fit,
    }
    payload: Dict[str, Any] = {
        "functional": functional,
        "security": security,
        "architecture_fit": architecture_fit,
        "total": total,
        "components": components,
    }
    weights = task.get("scoring")
    if isinstance(weights, dict):
        payload["weights"] = {
            str(k): float(v)
            for k, v in weights.items()
            if isinstance(v, (int, float))
        }
    return payload


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3:
        raise SystemExit("Usage: python -m verify.run_eval <task.json> <decision.json>")

    out = run_single(Path(sys.argv[1]), Path(sys.argv[2]))
    print(json.dumps(out, indent=2, ensure_ascii=False))
