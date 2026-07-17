# policy/scorer.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

import yaml
import os

from policy.must_include_checks import check_must_include
from policy.ops_readiness import (
    extract_ops_signals,
    ops_readiness_services_threshold,
    required_ops_for_services,
)


@dataclass
class ScoreResult:
    functional: float
    security: float
    architecture_fit: float
    total: float
    rationale: List[Dict[str, Any]]
    penalties: List[str]
    scorecard: "Scorecard"


@dataclass(frozen=True)
class GateFailure:
    tag: str
    message: str
    evidence_ref: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tag": str(self.tag),
            "message": str(self.message),
            "evidence_ref": str(self.evidence_ref) if isinstance(self.evidence_ref, str) and self.evidence_ref else None,
        }


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    failures: List[GateFailure]
    metrics: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": str(self.name),
            "passed": bool(self.passed),
            "failures": [item.to_dict() for item in self.failures],
            "metrics": dict(self.metrics or {}),
        }


@dataclass(frozen=True)
class Scorecard:
    hard_pass: bool
    hard_gates: List[GateResult]
    soft_score: float
    total_score: float | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hard_pass": bool(self.hard_pass),
            "hard_gates": [item.to_dict() for item in self.hard_gates],
            "soft_score": float(self.soft_score),
            "total_score": (float(self.total_score) if isinstance(self.total_score, (int, float)) else None),
        }


def build_scorecard(
    *,
    hard_gates: List[GateResult],
    soft_score: float,
    total_score: float | None = None,
) -> Scorecard:
    hard_pass = all(bool(g.passed) for g in hard_gates)
    return Scorecard(
        hard_pass=bool(hard_pass),
        hard_gates=list(hard_gates),
        soft_score=float(soft_score),
        total_score=(float(total_score) if isinstance(total_score, (int, float)) else None),
    )


def _get(d: Dict[str, Any], path: str, default=None):
    """
    Very small JSONPath-ish getter for paths like:
      $.nfr.compliance.pci_dss
    """
    if not path.startswith("$."):
        return default
    cur: Any = d
    for part in path[2:].split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur




def load_rules(path: str = "policy/policy_rules.yaml") -> Dict[str, Any]:
    env_path = os.getenv("POLICY_RULES_PATH")
    if env_path:
        path = env_path
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _cfg(d: Dict[str, Any], key: str, default):
    v = d.get(key, default)
    return v if v is not None else default


def score_architecture(
    task: Dict[str, Any],
    decision: Dict[str, Any],
    *,
    rules: Dict[str, Any] | None = None,
) -> ScoreResult:
    """
    Deterministic architecture-fit scoring and checks.

    Reality-aligned security model:
    - If spec does NOT ask for security, we do NOT punish missing PCI/audit/threat-model etc.
    - BUT microservices adds inherent security/operability requirements (service-to-service calls,
      identity, secrets, rate limiting / gateway concerns).
      So: microservices => minimum baseline security signals required, otherwise score hit.
    """
    if rules is None:
        rules = load_rules()

    spec = task["spec"]
    expected = task["expected"]

    weights: Dict[str, float] = _cfg(rules, "weights", {})
    gates: Dict[str, Any] = _cfg(rules, "gates", {})
    limits: Dict[str, Any] = _cfg(rules, "limits", {})
    penalties_cfg: Dict[str, Any] = _cfg(rules, "penalties", {})

    chosen_style = decision.get("chosen_style")
    separated_services = decision.get("topology", {}).get("separated_services", []) or []
    services_count = len(separated_services)
    core_style = decision.get("topology", {}).get("core")

    functional_score = 1.0
    security_score = 0.0
    arch_fit = 1.0
    rationale: List[Dict[str, Any]] = []
    penalty_notes: List[str] = []

    allowed_styles = expected.get("allowed_styles", []) or []
    forbidden = expected.get("forbidden", []) or []

    # ---- Required style check ----
    if chosen_style not in allowed_styles:
        arch_fit *= 0.0
        penalty_notes.append(f"style_mismatch: chosen_style='{chosen_style}' allowed={allowed_styles}")

    # ---- Forbidden rules (task-level) ----
    forbidden_multiplier = float(_cfg(penalties_cfg, "forbidden_violation_multiplier", 0.0))

    if "microservices" in forbidden and chosen_style == "microservices":
        arch_fit *= forbidden_multiplier
        penalty_notes.append("forbidden: microservices")

    if "more_than_2_services" in forbidden and services_count > 2:
        arch_fit *= forbidden_multiplier
        penalty_notes.append(f"forbidden: more_than_2_services (got {services_count})")

    if "more_than_3_services" in forbidden and services_count > 3:
        arch_fit *= forbidden_multiplier
        penalty_notes.append(f"forbidden: more_than_3_services (got {services_count})")

    # ---- Must-include enforcement (task-level) ----
    must_missing = check_must_include(task, decision)

    per_item = float(_cfg(penalties_cfg, "must_include_missing_per_item", 0.15))
    cap = float(_cfg(penalties_cfg, "must_include_missing_cap", 0.8))

    if must_missing:
        arch_fit -= min(cap, per_item * len(must_missing))
        penalty_notes.append(f"missing must_include: {must_missing}")

    # ---- Signals & rationale (policy-evidence) ----
    pci = bool(_get(spec, "$.nfr.compliance.pci_dss", False))
    team_size = int(_get(spec, "$.constraints.team_size", 999))
    mvp_days = int(_get(spec, "$.constraints.time_to_mvp_days", 9999))
    obs_level = str(_get(spec, "$.nfr.operability.observability_level", "none"))

    throughput = int(_get(spec, "$.nfr.performance.throughput_rps", 0))
    p95 = int(_get(spec, "$.nfr.performance.p95_latency_ms", 999999))
    rpo = int(_get(spec, "$.nfr.reliability.rpo_minutes", 999999))
    rto = int(_get(spec, "$.nfr.reliability.rto_minutes", 999999))

    gdpr = bool(_get(spec, "$.nfr.privacy.gdpr", False))
    audit_required = bool(_get(spec, "$.nfr.operability.audit_log_required", False))

    threat_model_required = bool(_get(spec, "$.nfr.security.threat_model_required", False))
    rate_limiting_required = bool(_get(spec, "$.nfr.security.rate_limiting_required", False))

    auth_raw = _get(spec, "$.nfr.security.auth", []) or []
    data_classification = str(_get(spec, "$.nfr.security.data_classification", "internal")).lower()

    # normalize auth to list[str]
    if isinstance(auth_raw, list):
        auth: List[str] = [str(x) for x in auth_raw if x is not None]
    elif isinstance(auth_raw, str) and auth_raw:
        auth = [auth_raw]
    else:
        auth = []

    has_auth = len(auth) > 0
    has_mtls = "mTLS" in auth
    is_sensitive = any(x in data_classification for x in ["confidential", "pii", "payment"])

    # ------------------------------------------------------------------
    # ✅ Reality-aligned security scope
    # ------------------------------------------------------------------
    security_baseline_no_scope = float(_cfg(penalties_cfg, "security_baseline_no_scope", 1.0))
    security_low_threshold = float(_cfg(penalties_cfg, "security_low_threshold", 0.85))
    include_missing_signals = bool(_cfg(penalties_cfg, "security_penalty_include_missing_signals", True))

    # Spec-driven security requirements (explicit)
    security_required_by_spec = any(
        [
            pci,
            audit_required,
            threat_model_required,
            rate_limiting_required,
            is_sensitive,
            # Optional: treat GDPR as a stronger signal if you want
            # gdpr,
        ]
    )

    # Architecture-driven minimum requirements (implicit)
    security_required_by_arch = (chosen_style == "microservices")

    security_required = security_required_by_spec or security_required_by_arch

    # ---- If not required at all, assume baseline satisfied ----
    if not security_required:
        security_score = max(0.0, min(1.0, security_baseline_no_scope))
        rationale.append(
            {
                "reason": "No explicit security requirements and no architecture-implied security scope",
                "signal": "security_not_required",
                "weight": 1.0,
                "evidence_path": "$.nfr.security",
            }
        )
    else:
        # ----------------------------
        # Spec-driven scoring
        # ----------------------------
        if threat_model_required:
            security_score += 0.35
            rationale.append(
                {
                    "reason": "Threat model required",
                    "signal": "nfr.security.threat_model_required=true",
                    "weight": 0.35,
                    "evidence_path": "$.nfr.security.threat_model_required",
                }
            )

        if rate_limiting_required:
            security_score += 0.15
            rationale.append(
                {
                    "reason": "Rate limiting required",
                    "signal": "nfr.security.rate_limiting_required=true",
                    "weight": 0.15,
                    "evidence_path": "$.nfr.security.rate_limiting_required",
                }
            )

        if audit_required:
            security_score += 0.25
            rationale.append(
                {
                    "reason": "Audit log required",
                    "signal": "nfr.operability.audit_log_required=true",
                    "weight": 0.25,
                    "evidence_path": "$.nfr.operability.audit_log_required",
                }
            )

        if pci:
            security_score += 0.25
            rationale.append(
                {
                    "reason": "PCI scope present",
                    "signal": "nfr.compliance.pci_dss=true",
                    "weight": 0.25,
                    "evidence_path": "$.nfr.compliance.pci_dss",
                }
            )

        if is_sensitive:
            security_score += 0.25
            rationale.append(
                {
                    "reason": "Sensitive data classification",
                    "signal": f"nfr.security.data_classification={data_classification}",
                    "weight": 0.25,
                    "evidence_path": "$.nfr.security.data_classification",
                }
            )

        # ----------------------------
        # Baseline controls (help, but don't define scope)
        # ----------------------------
        if has_auth:
            security_score += 0.10
            rationale.append(
                {
                    "reason": "Authentication configured (baseline control)",
                    "signal": "nfr.security.auth is non-empty",
                    "weight": 0.10,
                    "evidence_path": "$.nfr.security.auth",
                }
            )

        if has_mtls:
            security_score += 0.15
            rationale.append(
                {
                    "reason": "mTLS configured (strong service identity)",
                    "signal": "nfr.security.auth includes mTLS",
                    "weight": 0.15,
                    "evidence_path": "$.nfr.security.auth",
                }
            )

        security_score = min(1.0, security_score)

        # ----------------------------
        # Microservices implicit minimum gate (reality)
        # ----------------------------
        if security_required_by_arch:
            # Minimal baseline for microservices:
            # - You need *some* auth at edges (OIDC/JWT) OR mTLS inside
            # - And rate limiting is strongly recommended (gateway / API management)
            micro_min_ok = (has_auth or has_mtls) and (rate_limiting_required or obs_level in ["standard", "advanced"])

            if not micro_min_ok:
                # Soft gate: reduce architecture fit (system should learn to avoid insecure microservices)
                micro_mult = float(_cfg(penalties_cfg, "microservices_min_security_multiplier", 0.80))
                arch_fit *= micro_mult
                penalty_notes.append("gate: microservices_min_security_not_met")

                if include_missing_signals:
                    missing: List[str] = []
                    if not (has_auth or has_mtls):
                        missing.append("auth_or_mtls_required_for_microservices")
                    if not rate_limiting_required:
                        missing.append("rate_limiting_recommended_for_microservices")
                    if obs_level not in ["standard", "advanced"]:
                        missing.append("operability_observability_should_be_standard_or_advanced")
                    penalty_notes.append("security_missing_signals: " + ",".join(missing))

        # Penalize low security only when required (spec or arch)
        if security_score < security_low_threshold:
            penalty_notes.append(f"security_low: {security_score:.3f} < {security_low_threshold:.3f}")

    # Architecture signals (explainability)
    def w(name: str, default: float) -> float:
        return float(_cfg(weights, name, default))

    if pci:
        rationale.append(
            {
                "reason": "PCI/Payment isolation signal",
                "signal": "compliance.pci_dss=true",
                "weight": w("pci_isolation", 0.9),
                "evidence_path": "$.nfr.compliance.pci_dss",
            }
        )

    if team_size <= 4 and mvp_days <= 30:
        rationale.append(
            {
                "reason": "Small team + fast MVP",
                "signal": f"constraints.team_size={team_size} AND time_to_mvp_days={mvp_days}",
                "weight": w("small_team_fast_mvp", 0.8),
                "evidence_path": "$.constraints",
            }
        )

    if obs_level in ["none", "basic"]:
        rationale.append(
            {
                "reason": "Low operability maturity",
                "signal": f"operability.observability_level={obs_level}",
                "weight": w("low_operability", 0.7),
                "evidence_path": "$.nfr.operability.observability_level",
            }
        )

    if obs_level in ["standard", "advanced"]:
        rationale.append(
            {
                "reason": "Higher operability maturity",
                "signal": f"operability.observability_level={obs_level}",
                "weight": w("high_operability", 0.7),
                "evidence_path": "$.nfr.operability.observability_level",
            }
        )

    if throughput >= 200 or p95 <= 250:
        rationale.append(
            {
                "reason": "Performance scale pressure",
                "signal": f"throughput_rps={throughput} OR p95_latency_ms={p95}",
                "weight": w("high_throughput", 0.5),
                "evidence_path": "$.nfr.performance",
            }
        )

    if rpo <= 10 or rto <= 30:
        rationale.append(
            {
                "reason": "Strict RTO/RPO",
                "signal": f"rpo_minutes={rpo} OR rto_minutes={rto}",
                "weight": w("strict_rto_rpo", 0.6),
                "evidence_path": "$.nfr.reliability",
            }
        )

    if gdpr and audit_required:
        rationale.append(
            {
                "reason": "GDPR + audit implies governance controls",
                "signal": "privacy.gdpr=true AND audit_log_required=true",
                "weight": w("gdpr_audit", 0.6),
                "evidence_path": "$.nfr",
            }
        )

    if has_mtls:
        rationale.append(
            {
                "reason": "mTLS implies stronger service identity / edge security needs",
                "signal": "security.auth includes mTLS",
                "weight": w("mtls_security", 0.6),
                "evidence_path": "$.nfr.security.auth",
            }
        )

    # ---- Gating rules ----
    if chosen_style == "microservices":
        allowed_obs = set(_cfg(gates, "microservices_requires_observability_level", ["standard", "advanced"]))
        if obs_level not in allowed_obs:
            mult = float(_cfg(penalties_cfg, "microservices_without_observability_multiplier", 0.2))
            arch_fit *= mult
            penalty_notes.append(f"gate: microservices_requires_observability_level={sorted(allowed_obs)} got='{obs_level}'")

    # ---- Ops readiness hard gate for microservices / many services ----
    ops_min_signals = int(_cfg(limits, "ops_readiness_min_signals", 3))
    ops_services_threshold = ops_readiness_services_threshold(rules, default=3)
    required_ops = required_ops_for_services(services_count, rules, default_required=ops_min_signals)
    ops_signals = extract_ops_signals(decision)
    present = [k for k, v in ops_signals.items() if v]
    missing = [k for k, v in ops_signals.items() if not v]

    needs_ops_gate = (
        services_count >= ops_services_threshold
        and (chosen_style == "microservices" or core_style == "distributed_core")
    )

    ops_failures: List[GateFailure] = []
    if needs_ops_gate and required_ops > 0 and len(present) < required_ops:
        msg = f"required={required_ops} present={len(present)} missing={','.join(missing)}"
        ops_failures.append(
            GateFailure(
                tag="ops_readiness_missing",
                message=msg,
                evidence_ref="$.ops_readiness",
            )
        )
        penalty_notes.append(f"ops_readiness_missing({msg})")
        rationale.append(
            {
                "reason": "Distributed architecture without sufficient ops readiness signals",
                "signal": f"services={services_count} present_ops={len(present)}/{required_ops} missing={','.join(missing)}",
                "weight": 1.0,
                "evidence_path": "$.verification_plan",
            }
        )

    # ---- Soft overengineering penalty (kept mild): even when distributed is acceptable,
    # a very small team + short horizon should pay a score cost for service sprawl risk.
    strict_reliability = (rpo <= 30) or (rto <= 60)
    high_scale_pressure = (throughput >= 500) or (p95 <= 250)
    regulated_or_sensitive = pci or is_sensitive
    org_capacity = (team_size >= 8) and (mvp_days >= 60)
    hard_constraints_for_sprawl = high_scale_pressure or strict_reliability or regulated_or_sensitive or org_capacity

    if services_count > 2 and hard_constraints_for_sprawl and team_size <= 4 and mvp_days <= 90:
        sprawl_soft_mult = float(_cfg(penalties_cfg, "service_sprawl_soft_multiplier", 0.95))
        arch_fit *= sprawl_soft_mult
        penalty_notes.append(
            "service_sprawl_soft: "
            f"services={services_count} team_size={team_size} time_to_mvp_days={mvp_days} "
            f"multiplier={sprawl_soft_mult:.3f}"
        )

    ops_gate = GateResult(
        name="ops_readiness",
        passed=not ops_failures,
        failures=ops_failures,
        metrics={
            "applies": bool(needs_ops_gate and required_ops > 0),
            "required": int(required_ops),
            "present": int(len(present)),
            "missing": list(missing),
            "services_count": int(services_count),
            "chosen_style": str(chosen_style or ""),
            "core_style": str(core_style or ""),
        },
    )

    arch_fit = max(0.0, min(1.0, arch_fit))

    # Weighted total using task weights
    wts = task.get("scoring", {}) or {}
    wf = float(_cfg(wts, "functional", 0.5))
    ws = float(_cfg(wts, "security", 0.3))
    wa = float(_cfg(wts, "architecture_fit", 0.2))

    total = functional_score * wf + security_score * ws + arch_fit * wa

    scorecard = build_scorecard(
        hard_gates=[ops_gate],
        soft_score=float(total),
        total_score=float(total),
    )

    return ScoreResult(
        functional=functional_score,
        security=security_score,
        architecture_fit=arch_fit,
        total=total,
        rationale=rationale,
        penalties=penalty_notes,
        scorecard=scorecard,
    )
