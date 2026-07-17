from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Mapping

from ml.judges.aggregate import get_judge_aggregator_service
from ml.judges.contracts import JudgeFailure, JudgeGateResult, JudgeReport
from ml.judges.fingerprint import with_judge_report_fingerprint
from babyai_shared.ops.killswitch import get_killswitch_service
from policy.constitution_service import get_constitution_service
from policy.reason_taxonomy_service import get_reason_taxonomy_service
from babyai_shared.review.service import get_deterministic_review_service
from verify.artifacts.registry import write_artifact
import yaml


SCHEMA_VERSION = 1
_CANARY_ORDER = {"shadow": 0, "advice": 1, "active": 2}
_JUDGE_ACCEPT_VALUES = {"accept", "accepted", "allow", "allowed", "pass", "passed", "ok", "true", "1"}
_JUDGE_REJECT_VALUES = {"reject", "rejected", "deny", "denied", "fail", "failed", "false", "0", "review", "pending"}


class PromotionGateService:
    """
    Promotion gate service used by promote scripts.
    """

    def evaluate(
        self,
        *,
        trial: Mapping[str, Any],
        policy: Mapping[str, Any],
        model_family: str,
        requested_canary_level: str,
        trial_sha256: str | None,
        policy_sha256: str | None,
    ) -> Dict[str, Any]:
        return evaluate_promotion(
            trial=trial,
            policy=policy,
            model_family=model_family,
            requested_canary_level=requested_canary_level,
            trial_sha256=trial_sha256,
            policy_sha256=policy_sha256,
        )


_PROMOTION_GATE_SERVICE: PromotionGateService | None = None


def get_promotion_gate_service(*, reload: bool = False) -> PromotionGateService:
    global _PROMOTION_GATE_SERVICE
    if _PROMOTION_GATE_SERVICE is None or reload:
        _PROMOTION_GATE_SERVICE = PromotionGateService()
    return _PROMOTION_GATE_SERVICE


def load_promotion_policy(*, path: Path, model_family: str) -> Dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SystemExit(f"promotion policy must be mapping: {path}")
    defaults = raw.get("defaults")
    merged: Dict[str, Any] = deepcopy(defaults if isinstance(defaults, dict) else {})
    models = raw.get("models")
    if isinstance(models, dict):
        override = models.get(model_family)
        if isinstance(override, dict):
            merged = _deep_merge(merged, override)
    merged["schema_version"] = int(raw.get("schema_version", SCHEMA_VERSION))
    return merged


def evaluate_promotion(
    *,
    trial: Mapping[str, Any],
    policy: Mapping[str, Any],
    model_family: str,
    requested_canary_level: str,
    trial_sha256: str | None,
    policy_sha256: str | None,
) -> Dict[str, Any]:
    reasons: list[str] = []
    inconclusive_reasons: list[str] = []
    trial_accepted = bool(trial.get("accepted"))
    deterministic_review = None
    review_pass = True

    if requested_canary_level not in _CANARY_ORDER:
        raise SystemExit(f"invalid canary level: {requested_canary_level}")

    review_input = trial.get("review_input_artifacts")
    if isinstance(review_input, Mapping):
        try:
            review_report = get_deterministic_review_service().review(review_input)
            deterministic_review = review_report.to_dict()
            if not review_report.hard_pass:
                review_pass = False
        except Exception as exc:
            review_pass = False
            deterministic_review = {
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
            reasons.append("deterministic_review_error")

    baseline = _extract_metrics(trial.get("baseline_metrics"), name="baseline_metrics", reasons=inconclusive_reasons)
    candidate = _extract_metrics(trial.get("candidate_metrics"), name="candidate_metrics", reasons=inconclusive_reasons)
    delta = _extract_delta(
        delta_payload=trial.get("delta_metrics"),
        baseline=baseline,
        candidate=candidate,
        reasons=inconclusive_reasons,
    )
    tasks_executed_min = min(baseline.get("tasks_executed", 0.0), candidate.get("tasks_executed", 0.0))

    hard_cfg = _as_dict(policy.get("hard_gates"))
    max_drop_pass_rate = _float(hard_cfg.get("max_drop_pass_rate"), default=0.0)
    max_increase_avg_repairs = _float(hard_cfg.get("max_increase_avg_repairs"), default=0.0)
    max_increase_timeouts = _float(hard_cfg.get("max_increase_timeouts"), default=0.0)
    max_increase_scope = _float(hard_cfg.get("max_increase_scope_violations"), default=0.0)

    hard_gates = [
        _gate(
            name="pass_rate_no_regression",
            observed=delta["pass_rate"],
            threshold=-max_drop_pass_rate,
            comparator=">=",
        ),
        _gate(
            name="avg_repairs_no_regression",
            observed=delta["avg_repairs"],
            threshold=max_increase_avg_repairs,
            comparator="<=",
        ),
        _gate(
            name="timeouts_no_regression",
            observed=delta["timeouts"],
            threshold=max_increase_timeouts,
            comparator="<=",
        ),
        _gate(
            name="scope_violations_no_regression",
            observed=delta["scope_violations"],
            threshold=max_increase_scope,
            comparator="<=",
        ),
    ]
    hard_gates_pass = all(bool(row.get("passed")) for row in hard_gates)
    hard_pass = bool(hard_gates_pass and not inconclusive_reasons)
    if not hard_gates_pass and not inconclusive_reasons:
        reasons.append("hard_gate_failure")

    judge_reports = [
        with_judge_report_fingerprint(
            _promotion_metrics_judge_report(
                trial=trial,
                hard_gates=hard_gates,
                hard_pass=hard_gates_pass,
            )
        )
    ]
    if isinstance(deterministic_review, dict):
        judge_reports.append(
            with_judge_report_fingerprint(
                _deterministic_review_judge_report(
                    trial=trial,
                    deterministic_review=deterministic_review,
                )
            )
        )
    judge_aggregate = get_judge_aggregator_service().aggregate(
        judge_reports,
        iteration=1,
        score=float(delta.get("pass_rate", 0.0)),
    )
    judge_acceptance = _resolve_judge_acceptance(
        trial=trial,
        judge_aggregate=judge_aggregate.to_dict(),
    )
    judge_accepted = bool(judge_acceptance.get("accepted", False))

    lift_cfg = _as_dict(policy.get("lift"))
    min_pass_rate_delta = _float(lift_cfg.get("min_pass_rate_delta"), default=0.001)
    min_avg_repairs_reduction = _float(lift_cfg.get("min_avg_repairs_reduction"), default=0.02)
    pass_rate_lift = delta["pass_rate"] >= min_pass_rate_delta
    avg_repairs_lift = (-delta["avg_repairs"]) >= min_avg_repairs_reduction
    lift_pass = hard_pass and (pass_rate_lift or avg_repairs_lift)

    eq_cfg = _as_dict(policy.get("equivalent"))
    eq_enabled = bool(eq_cfg.get("enabled", True))
    eq_min_tasks = _float(eq_cfg.get("min_tasks_executed"), default=1.0)
    eq_pass = (
        eq_enabled
        and hard_pass
        and tasks_executed_min >= eq_min_tasks
        and abs(delta["pass_rate"]) <= _float(eq_cfg.get("max_abs_pass_rate_delta"), default=0.002)
        and abs(delta["avg_repairs"]) <= _float(eq_cfg.get("max_abs_avg_repairs_delta"), default=0.05)
        and abs(delta["timeouts"]) <= _float(eq_cfg.get("max_abs_timeouts_delta"), default=0.0)
        and abs(delta["scope_violations"]) <= _float(eq_cfg.get("max_abs_scope_violations_delta"), default=0.0)
    )
    equivalent_used = bool(eq_pass and not lift_pass)

    if not trial_accepted:
        reasons.append("trial_not_accepted")
    if not judge_accepted:
        reasons.append("judge_not_accepted")
    if not (lift_pass or eq_pass) and not inconclusive_reasons:
        reasons.append("no_documented_lift_or_equivalent")
    if not review_pass:
        reasons.append("deterministic_review_failed")

    canary_recommended = None
    if lift_pass:
        canary_recommended = str(_as_dict(policy.get("canary")).get("recommended_for_lift", "active"))
    elif eq_pass:
        canary_recommended = str(_as_dict(policy.get("canary")).get("recommended_for_equivalent", "advice"))
    canary_ok = (
        isinstance(canary_recommended, str)
        and canary_recommended in _CANARY_ORDER
        and _CANARY_ORDER[requested_canary_level] <= _CANARY_ORDER[canary_recommended]
    )
    if (lift_pass or eq_pass) and not canary_ok:
        reasons.append("requested_canary_level_exceeds_recommendation")

    inconclusive = bool(inconclusive_reasons)
    reason_taxonomy = get_reason_taxonomy_service()
    max_reason_codes = int(reason_taxonomy.state.max_reason_codes_per_pack)
    if inconclusive:
        reasons = _cap_reason_codes(
            [*reasons, *[f"inconclusive:{item}" for item in inconclusive_reasons]],
            max_codes=max_reason_codes,
            overflow_code="inconclusive:truncated",
        )
    else:
        reasons = _cap_reason_codes(reasons, max_codes=max_reason_codes)
    inconclusive_reasons = _cap_reason_codes(inconclusive_reasons, max_codes=max_reason_codes)
    reason_taxonomy.require(reasons, pack_name="promotion.reasons")
    reason_taxonomy.require(inconclusive_reasons, pack_name="promotion.inconclusive_reasons")

    allowed = bool(
        trial_accepted
        and judge_accepted
        and hard_pass
        and review_pass
        and (lift_pass or eq_pass)
        and canary_ok
        and not inconclusive
    )
    decision = "approved"
    if inconclusive:
        decision = "blocked_inconclusive"
    elif not trial_accepted:
        decision = "blocked_trial_not_accepted"
    elif not judge_accepted:
        decision = "blocked_judge_not_accepted"
    elif not hard_pass:
        decision = "blocked_hard_gates"
    elif not review_pass:
        decision = "blocked_deterministic_review"
    elif not (lift_pass or eq_pass):
        decision = "blocked_no_lift_or_equivalent"
    elif not canary_ok:
        decision = "blocked_canary_level_too_high"

    return {
        "schema_version": SCHEMA_VERSION,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "model_family": str(model_family),
        "candidate_model": trial.get("candidate_model"),
        "candidate_model_hash": trial.get("candidate_model_hash"),
        "promotion_allowed": allowed,
        "decision": decision,
        "trial_accepted": trial_accepted,
        "hard_pass": hard_pass,
        "hard_gates": hard_gates,
        "judge_acceptance": judge_acceptance,
        "lift": {
            "pass": lift_pass,
            "pass_rate_lift": pass_rate_lift,
            "avg_repairs_lift": avg_repairs_lift,
            "min_pass_rate_delta": min_pass_rate_delta,
            "min_avg_repairs_reduction": min_avg_repairs_reduction,
        },
        "equivalent_policy": {
            "enabled": eq_enabled,
            "pass": bool(eq_pass),
            "used": equivalent_used,
            "min_tasks_executed": eq_min_tasks,
            "max_abs_pass_rate_delta": _float(eq_cfg.get("max_abs_pass_rate_delta"), default=0.002),
            "max_abs_avg_repairs_delta": _float(eq_cfg.get("max_abs_avg_repairs_delta"), default=0.05),
            "max_abs_timeouts_delta": _float(eq_cfg.get("max_abs_timeouts_delta"), default=0.0),
            "max_abs_scope_violations_delta": _float(eq_cfg.get("max_abs_scope_violations_delta"), default=0.0),
        },
        "canary": {
            "requested": requested_canary_level,
            "recommended": canary_recommended,
            "assigned": requested_canary_level if allowed else None,
        },
        "metrics": {
            "baseline": baseline,
            "candidate": candidate,
            "delta": delta,
            "tasks_executed_min": tasks_executed_min,
        },
        "inconclusive": inconclusive,
        "inconclusive_reasons": inconclusive_reasons,
        "reasons": reasons,
        "deterministic_review": deterministic_review,
        "judge_reports": judge_reports,
        "judge_aggregate": judge_aggregate.to_dict(),
        "fingerprints": {
            "trial_sha256": trial_sha256,
            "promotion_policy_sha256": policy_sha256,
            "evaluation_sha256": hash_payload(
                {
                    "model_family": model_family,
                    "trial": trial,
                    "policy": policy,
                    "requested_canary_level": requested_canary_level,
                }
            ),
        },
    }


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    constitution = get_constitution_service()
    get_killswitch_service().require_write(
        operation="scripts.promotion_gates.atomic_write_json",
        scope="PROMOTE_ACTIVE",
    )
    write_artifact(
        "promotion_json",
        json.dumps(payload, indent=2, ensure_ascii=True, sort_keys=True) + "\n",
        path,
        metadata={
            "source_ref": "scripts.promotion_gates",
            "constitution_version": constitution.state.version,
        },
    )


def hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(raw.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _extract_metrics(value: Any, *, name: str, reasons: list[str]) -> Dict[str, float]:
    payload = value if isinstance(value, dict) else {}
    result: Dict[str, float] = {}
    for key in ("pass_rate", "avg_repairs", "timeouts", "scope_violations", "tasks_executed"):
        raw = payload.get(key)
        try:
            result[key] = float(raw)
        except Exception:
            reasons.append(f"{name}_missing_{key}")
            result[key] = 0.0
    return result


def _extract_delta(
    *,
    delta_payload: Any,
    baseline: Mapping[str, float],
    candidate: Mapping[str, float],
    reasons: list[str],
) -> Dict[str, float]:
    payload = delta_payload if isinstance(delta_payload, dict) else {}
    delta: Dict[str, float] = {}
    for key in ("pass_rate", "avg_repairs", "timeouts", "scope_violations"):
        raw = payload.get(key)
        if raw is None:
            try:
                delta[key] = float(candidate.get(key, 0.0)) - float(baseline.get(key, 0.0))
            except Exception:
                reasons.append(f"delta_missing_{key}")
                delta[key] = 0.0
            continue
        try:
            delta[key] = float(raw)
        except Exception:
            reasons.append(f"delta_invalid_{key}")
            delta[key] = 0.0
    return delta


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _gate(*, name: str, observed: float, threshold: float, comparator: str) -> Dict[str, Any]:
    passed = bool(observed >= threshold) if comparator == ">=" else bool(observed <= threshold)
    return {
        "name": name,
        "passed": passed,
        "observed": float(observed),
        "threshold": float(threshold),
        "comparator": comparator,
    }


def _promotion_metrics_judge_report(
    *,
    trial: Mapping[str, Any],
    hard_gates: list[Dict[str, Any]],
    hard_pass: bool,
) -> JudgeReport:
    gate_rows: list[JudgeGateResult] = []
    for gate in hard_gates:
        name = str(gate.get("name") or "").strip() or "unknown_gate"
        passed = bool(gate.get("passed", False))
        failures: list[JudgeFailure] = []
        if not passed:
            failures.append(
                JudgeFailure(
                    tag="hard_gate_failure",
                    message=(
                        f"{name}: observed={gate.get('observed')} "
                        f"{gate.get('comparator')} threshold={gate.get('threshold')}"
                    ),
                    evidence_ref=f"$.hard_gates[{name}]",
                )
            )
        gate_rows.append(
            JudgeGateResult(
                name=name,
                passed=passed,
                failures=failures,
                metrics={
                    "observed": gate.get("observed"),
                    "threshold": gate.get("threshold"),
                    "comparator": gate.get("comparator"),
                },
            )
        )
    if not gate_rows:
        gate_rows = [
            JudgeGateResult(
                name="promotion_metrics",
                passed=False,
                failures=[JudgeFailure(tag="hard_gate_failure", message="no hard gates evaluated", evidence_ref="$.hard_gates")],
                metrics={},
            )
        ]
    return JudgeReport(
        judge_id="promotion_metrics",
        run_id=_opt_str(trial.get("run_id")),
        case_id=_opt_str(trial.get("case_id")),
        subject_id=_opt_str(trial.get("candidate_model_hash") or trial.get("candidate_model")),
        hard_pass=bool(hard_pass),
        hard_fail_tags=["hard_gate_failure"] if not hard_pass else [],
        hard_gates=gate_rows,
        metrics={},
        metadata={"source": "scripts.promotion_gates"},
    )


def _deterministic_review_judge_report(
    *,
    trial: Mapping[str, Any],
    deterministic_review: Mapping[str, Any],
) -> JudgeReport:
    findings = deterministic_review.get("findings", [])
    failures: list[JudgeFailure] = []
    if isinstance(findings, list):
        for row in findings:
            if not isinstance(row, dict):
                continue
            tag = str(row.get("tag") or "").strip()
            msg = str(row.get("message") or "").strip()
            if not tag or not msg:
                continue
            failures.append(
                JudgeFailure(
                    tag=tag,
                    message=msg,
                    evidence_ref=(_opt_str(row.get("evidence_ref"))),
                    severity=str(row.get("severity") or "error"),
                )
            )
    review_hard_pass = bool(deterministic_review.get("hard_pass", False))
    if not review_hard_pass and not failures:
        failures.append(
            JudgeFailure(
                tag="deterministic_review_error",
                message="deterministic review failed without findings payload",
                evidence_ref="$.deterministic_review",
            )
        )
    gate = JudgeGateResult(
        name="deterministic_review",
        passed=review_hard_pass,
        failures=failures,
        metrics=dict(deterministic_review.get("metrics", {}) if isinstance(deterministic_review.get("metrics"), dict) else {}),
    )
    return JudgeReport(
        judge_id="promotion_deterministic_review",
        run_id=_opt_str(trial.get("run_id")),
        case_id=_opt_str(trial.get("case_id")),
        subject_id=_opt_str(trial.get("candidate_model_hash") or trial.get("candidate_model")),
        hard_pass=review_hard_pass,
        hard_fail_tags=[item.tag for item in failures],
        hard_gates=[gate],
        metrics={},
        metadata={"source": "scripts.promotion_gates"},
    )


def _resolve_judge_acceptance(
    *,
    trial: Mapping[str, Any],
    judge_aggregate: Mapping[str, Any],
) -> Dict[str, Any]:
    decision, source = _extract_trial_judge_decision(trial)
    if decision is not None:
        accepted = decision in _JUDGE_ACCEPT_VALUES
        normalized_decision = decision
    else:
        accepted = bool(judge_aggregate.get("hard_pass", False))
        normalized_decision = ("accept" if accepted else "review")
        source = "computed_aggregate"

    payload = {
        "accepted": bool(accepted),
        "decision": str(normalized_decision),
        "source": str(source),
    }
    payload["fingerprint"] = hash_payload(payload)
    return payload


def _extract_trial_judge_decision(trial: Mapping[str, Any]) -> tuple[str | None, str]:
    raw = _normalize_judge_decision(trial.get("judge_decision"))
    if raw is not None:
        return raw, "trial.judge_decision"

    judge_pack = trial.get("judge_pack")
    if isinstance(judge_pack, Mapping):
        raw = _normalize_judge_decision(judge_pack.get("judge_decision"))
        if raw is not None:
            return raw, "trial.judge_pack.judge_decision"

    judge_acceptance = trial.get("judge_acceptance")
    if isinstance(judge_acceptance, Mapping):
        raw = _normalize_judge_decision(judge_acceptance.get("decision"))
        if raw is not None:
            return raw, "trial.judge_acceptance.decision"
        accepted = judge_acceptance.get("accepted")
        if isinstance(accepted, bool):
            return ("accept" if accepted else "review"), "trial.judge_acceptance.accepted"

    judge_aggregate = trial.get("judge_aggregate")
    if isinstance(judge_aggregate, Mapping):
        hard_pass = judge_aggregate.get("hard_pass")
        if isinstance(hard_pass, bool):
            return ("accept" if hard_pass else "review"), "trial.judge_aggregate.hard_pass"

    return None, "none"


def _normalize_judge_decision(value: Any) -> str | None:
    token = str(value or "").strip().lower()
    if not token:
        return None
    if token in _JUDGE_ACCEPT_VALUES:
        return "accept"
    if token in _JUDGE_REJECT_VALUES:
        return "review"
    return "review"


def _opt_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _cap_reason_codes(
    codes: list[str],
    *,
    max_codes: int,
    overflow_code: str | None = None,
) -> list[str]:
    normalized = _dedupe_preserve_order(codes)
    budget = max(1, int(max_codes))
    if len(normalized) <= budget:
        return normalized
    if overflow_code:
        out = normalized[: max(0, budget - 1)]
        if overflow_code not in out:
            out.append(overflow_code)
        return out[:budget]
    return normalized[:budget]


def _dedupe_preserve_order(codes: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in codes:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out
