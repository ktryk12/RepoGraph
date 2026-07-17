# policy/must_include_checks.py
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List

from policy.musts import required_must_tokens

_CLAIM_CONCRETE_RE = re.compile(
    r"(ADR-\d+|test[-_ ]?run[-_ ]?[A-Za-z0-9_.:-]+|endpoint[-_ :#/][A-Za-z0-9_.:/-]+|[A-Za-z0-9_\-/\\]+\.[A-Za-z0-9]{1,8})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class MustIncludeFailure:
    tag: str
    message: str
    evidence_ref: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tag": str(self.tag),
            "message": str(self.message),
            "evidence_ref": str(self.evidence_ref) if isinstance(self.evidence_ref, str) and self.evidence_ref else None,
        }


def _as_str_list(x: Any) -> List[str]:
    if isinstance(x, list):
        return [str(i) for i in x]
    return []


def _contains_any(haystack: List[str], needles: List[str]) -> bool:
    hs = " ".join(_as_str_list(haystack)).lower()
    return any(n.lower() in hs for n in needles)


def _plan_looks_placeholder(vp_lines_lc: List[str]) -> bool:
    """
    Detect 'schema-valid but useless' plans.
    Conservative: only mark placeholder if it's really screaming TODO.
    """
    if not vp_lines_lc:
        return True

    markers = ("[todo]", "todo", "tbd", "placeholder", "unmapped", "missing")
    flagged = [ln for ln in vp_lines_lc if any(m in ln for m in markers)]

    # If all lines are placeholder-ish -> placeholder plan
    if len(flagged) == len(vp_lines_lc):
        return True

    # If the plan is tiny and has placeholder markers -> likely placeholder
    if len(vp_lines_lc) <= 2 and flagged:
        return True

    return False


def _services_count(decision: Dict[str, Any]) -> int:
    topo = decision.get("topology", {}) if isinstance(decision, dict) else {}
    if not isinstance(topo, dict):
        return 0
    raw = topo.get("separated_services", [])
    if not isinstance(raw, list):
        return 0
    return sum(1 for s in raw if isinstance(s, str) and s.strip())


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _vp_has_any(vp_lines_lc: List[str], needles: List[str]) -> bool:
    return any(any(n in line for n in needles) for line in vp_lines_lc)


def _add_failure(failures: List[MustIncludeFailure], *, tag: str, message: str, evidence_ref: str | None) -> None:
    failures.append(
        MustIncludeFailure(
            tag=str(tag),
            message=str(message),
            evidence_ref=str(evidence_ref) if isinstance(evidence_ref, str) and evidence_ref else None,
        )
    )


def _dedupe_failures(failures: List[MustIncludeFailure]) -> List[MustIncludeFailure]:
    seen = set()
    out: List[MustIncludeFailure] = []
    for item in failures:
        key = (item.tag, item.message, item.evidence_ref or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _must_token_failures(task: Dict[str, Any], decision: Dict[str, Any]) -> List[MustIncludeFailure]:
    """
    Task-level must_include checks.
    """
    failures: List[MustIncludeFailure] = []

    # Task-level must_include + derived-from-spec must_include
    must = required_must_tokens(task)

    verification_plan = _as_str_list(decision.get("verification_plan", []) or [])
    topology = decision.get("topology", {}) or {}
    services = _as_str_list((topology.get("separated_services", []) or []))

    rationale = decision.get("rationale", []) or []
    if not isinstance(rationale, list):
        rationale = []

    rationale_text = " ".join(
        [str(r.get("reason", "")) + " " + str(r.get("signal", "")) for r in rationale if isinstance(r, dict)]
    ).lower()

    vp_lines_lc = [ln.lower() for ln in verification_plan]
    plan_placeholder = _plan_looks_placeholder(vp_lines_lc)

    for req in must:
        r = str(req)

        if r == "audit_log":
            ok = any("audit" in s.lower() for s in services) or _contains_any(
                verification_plan,
                [
                    "audit log",
                    "audit event",
                    "audit trail",
                    "audit completeness",
                    "audit completeness tests",
                    "audit log completeness",
                ],
            )
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_audit_log",
                    message="audit_log: need audit service or audit-log tests in verification_plan",
                    evidence_ref="$.verification_plan",
                )

        elif r == "threat_model":
            ok = _contains_any(verification_plan, ["threat model", "threat modeling", "stride", "pasta", "linddun"])
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_threat_model",
                    message="threat_model: missing explicit threat modeling step in verification_plan",
                    evidence_ref="$.verification_plan",
                )

        elif r == "contract_tests":
            ok = _contains_any(verification_plan, ["contract test", "pact", "consumer-driven", "cdc"])
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_contract_tests",
                    message="contract_tests: missing in verification_plan",
                    evidence_ref="$.verification_plan",
                )

        elif r == "sast_pass":
            ok = _contains_any(
                verification_plan,
                ["sast", "static analysis", "codeql", "semgrep", "dependency scanning"],
            )
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_sast_pass",
                    message="sast_pass: missing SAST step in verification_plan",
                    evidence_ref="$.verification_plan",
                )

        elif r == "rate_limiting":
            keywords = [
                "must_include:rate_limiting",
                "rate limit",
                "rate-limiting",
                "throttle",
                "abuse",
                "429",
                "token bucket",
                "leaky bucket",
            ]
            hits = [ln for ln in vp_lines_lc if any(k in ln for k in keywords)]

            placeholder_markers = ("[todo]", "todo", "tbd", "unmapped", "placeholder")
            hits_placeholder_only = bool(hits) and all(any(m in ln for m in placeholder_markers) for ln in hits)

            ok = bool(hits) and (not hits_placeholder_only) and (not plan_placeholder)
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_rate_limiting",
                    message="rate_limiting: no concrete indication of rate limiting",
                    evidence_ref="$.verification_plan",
                )

        elif r == "load_test_plan":
            ok = _contains_any(verification_plan, ["load", "p95", "rps", "k6", "jmeter", "locust"])
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_load_test_plan",
                    message="load_test_plan: missing load/perf verification",
                    evidence_ref="$.verification_plan",
                )

        elif r == "observability":
            ok = _contains_any(
                verification_plan,
                ["metrics", "traces", "logging", "opentelemetry", "grafana", "prometheus"],
            )
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_observability",
                    message="observability: missing metrics/tracing/logging plan",
                    evidence_ref="$.verification_plan",
                )

        elif r == "backup_restore":
            ok = _contains_any(verification_plan, ["backup", "restore"])
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_backup_restore",
                    message="backup_restore: missing backup/restore tests",
                    evidence_ref="$.verification_plan",
                )

        elif r == "dr_drill_plan":
            ok = _contains_any(verification_plan, ["dr drill", "failover", "disaster recovery"])
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_dr_drill_plan",
                    message="dr_drill_plan: missing DR/failover drill",
                    evidence_ref="$.verification_plan",
                )

        elif r == "data_retention_enforcement":
            ok = _contains_any(verification_plan, ["retention", "purge", "delete"])
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_data_retention_enforcement",
                    message="data_retention_enforcement: missing retention enforcement tests",
                    evidence_ref="$.verification_plan",
                )

        elif r == "audit_completeness_tests":
            ok = _contains_any(
                verification_plan,
                ["audit completeness", "audit log completeness", "audit trail completeness", "integrity assertion"],
            )
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_audit_completeness_tests",
                    message="audit_completeness_tests: missing audit completeness checks",
                    evidence_ref="$.verification_plan",
                )

        elif r == "outbox_pattern_or_worker":
            ok = _contains_any(verification_plan, ["outbox", "worker", "queue", "idempotency"]) or any(
                "worker" in s.lower() for s in services
            )
            if plan_placeholder:
                ok = False
            if not ok:
                _add_failure(
                    failures,
                    tag="missing_outbox_pattern_or_worker",
                    message="outbox_pattern_or_worker: missing outbox/worker evidence",
                    evidence_ref="$.verification_plan",
                )

        elif r in (
            "basic_observability",
            "operability_mvp_plan",
            "service_boundaries",
            "sre_ready_observability",
            "adr_boundaries",
            "stop_condition_split_later",
            "secrets_handling",
            "mtls_plan",
            "avoid_distributed_saga_sprawl",
            "slo_sla_alignment",
        ):
            blob = " ".join(vp_lines_lc) + " " + rationale_text + " " + " ".join(s.lower() for s in services)
            if plan_placeholder:
                _add_failure(
                    failures,
                    tag=f"missing_{r}",
                    message=f"{r}: verification_plan looks placeholder-only",
                    evidence_ref="$.verification_plan",
                )
            elif r.replace("_", " ") not in blob and r not in blob:
                _add_failure(
                    failures,
                    tag=f"missing_{r}",
                    message=f"{r}: not referenced in decision artifacts",
                    evidence_ref="$.verification_plan",
                )

        else:
            _add_failure(
                failures,
                tag="unknown_must_include_token",
                message=f"{r}: unknown must_include token",
                evidence_ref="$.expected.must_include",
            )

    return failures


def _anti_camouflage_evidence_failures(task: Dict[str, Any], decision: Dict[str, Any]) -> List[MustIncludeFailure]:
    failures: List[MustIncludeFailure] = []
    min_items = 2

    evidence = decision.get("evidence", {}) if isinstance(decision, dict) else {}
    evidence_items = evidence.get("items", []) if isinstance(evidence, dict) else []
    if isinstance(evidence_items, list) and evidence_items:
        valid = 0
        for idx, item in enumerate(evidence_items):
            path = f"$.evidence.items[{idx}]"
            if not isinstance(item, dict):
                _add_failure(
                    failures,
                    tag="missing_evidence",
                    message="evidence.items entry must be an object with source_ref and claim",
                    evidence_ref=path,
                )
                continue

            source_ref = str(item.get("source_ref", "")).strip()
            claim = str(item.get("claim", "")).strip()
            if not source_ref:
                _add_failure(
                    failures,
                    tag="missing_evidence",
                    message="evidence.items[].source_ref is required",
                    evidence_ref=f"{path}.source_ref",
                )
                continue

            if not claim or not _CLAIM_CONCRETE_RE.search(claim):
                _add_failure(
                    failures,
                    tag="missing_evidence",
                    message="evidence.items[].claim must reference concrete artifacts (ADR/test-run/file/endpoint)",
                    evidence_ref=f"{path}.claim",
                )
                continue
            valid += 1

        if valid < min_items:
            _add_failure(
                failures,
                tag="missing_evidence",
                message=f"need at least {min_items} concrete evidence items; got {valid}",
                evidence_ref="$.evidence.items",
            )
        return failures

    rationale = decision.get("rationale", []) if isinstance(decision, dict) else []
    rationale_count = 0
    if isinstance(rationale, list):
        for item in rationale:
            if not isinstance(item, dict):
                continue
            ep = str(item.get("evidence_path", "")).strip()
            if ep.startswith("$."):
                rationale_count += 1

    if rationale_count < min_items:
        _add_failure(
            failures,
            tag="missing_evidence",
            message=f"need at least {min_items} concrete evidence references; got {rationale_count}",
            evidence_ref="$.rationale",
        )

    return failures


def _anti_camouflage_ops_failures(task: Dict[str, Any], decision: Dict[str, Any]) -> List[MustIncludeFailure]:
    failures: List[MustIncludeFailure] = []
    must_tokens = set(required_must_tokens(task))

    chosen_style = str(decision.get("chosen_style", "")).strip().lower()
    services_count = _services_count(decision)
    ops = decision.get("ops_readiness", {}) if isinstance(decision, dict) else {}
    if not isinstance(ops, dict):
        ops = {}

    vp_lines_lc = [line.lower() for line in _as_str_list(decision.get("verification_plan", []) or [])]

    need_runbook = "operability_mvp_plan" in must_tokens
    need_monitoring = "operability_mvp_plan" in must_tokens
    # Rollback is mandatory only for clearly distributed/service-heavy setups.
    # This avoids false hard-fails on small monolith MVP tasks.
    need_rollback = (chosen_style == "microservices" and services_count >= 5) or (services_count >= 5)
    need_slo = "slo_sla_alignment" in must_tokens

    if need_runbook:
        runbook = ops.get("runbook", {})
        steps = runbook.get("steps", []) if isinstance(runbook, dict) else []
        has_steps = isinstance(steps, list) and len([x for x in steps if str(x).strip()]) >= 2
        has_vp_runbook = _vp_has_any(vp_lines_lc, ["runbook"])
        if not has_steps and not has_vp_runbook:
            _add_failure(
                failures,
                tag="ops_missing_runbook",
                message="ops readiness requires runbook.steps (min 2) or explicit runbook verification",
                evidence_ref="$.ops_readiness.runbook",
            )

    if need_monitoring:
        monitoring = ops.get("monitoring", {})
        signals = monitoring.get("signals", []) if isinstance(monitoring, dict) else []
        has_signals = isinstance(signals, list) and len([x for x in signals if str(x).strip()]) >= 2
        has_vp_monitoring = _vp_has_any(
            vp_lines_lc,
            ["monitoring", "metrics", "traces", "logging", "dashboards", "alerts", "observability"],
        )
        if not has_signals and not has_vp_monitoring:
            _add_failure(
                failures,
                tag="ops_missing_monitoring_signals",
                message="ops readiness requires monitoring.signals (min 2) or explicit monitoring checks",
                evidence_ref="$.ops_readiness.monitoring",
            )

    if need_rollback:
        rollback = ops.get("rollback", {})
        plan = rollback.get("plan") if isinstance(rollback, dict) else None
        has_plan = isinstance(plan, str) and bool(plan.strip())
        has_vp_rollback = _vp_has_any(vp_lines_lc, ["rollback", "roll back", "revert", "canary rollback"])
        if not has_plan and not has_vp_rollback:
            _add_failure(
                failures,
                tag="ops_missing_rollback",
                message="ops readiness requires rollback.plan (or explicit rollback verification)",
                evidence_ref="$.ops_readiness.rollback",
            )

    if need_slo:
        slo = ops.get("slo", {})
        has_slo_struct = False
        if isinstance(slo, dict):
            target = slo.get("target")
            sli = slo.get("sli") or slo.get("slis") or slo.get("signals")
            has_target = isinstance(target, str) and bool(target.strip())
            has_sli = isinstance(sli, list) and len([x for x in sli if str(x).strip()]) >= 1
            has_slo_struct = has_target and has_sli
        has_vp_slo = _vp_has_any(vp_lines_lc, ["slo", "sli", "error budget"])
        if not has_slo_struct and not has_vp_slo:
            _add_failure(
                failures,
                tag="ops_missing_slo",
                message="slo_sla_alignment requires concrete SLO/SLI evidence",
                evidence_ref="$.ops_readiness.slo",
            )

    return failures


def _anti_camouflage_arch_sanity_failures(task: Dict[str, Any], decision: Dict[str, Any]) -> List[MustIncludeFailure]:
    failures: List[MustIncludeFailure] = []

    chosen_style = str(decision.get("chosen_style", "")).strip().lower()
    services_count = _services_count(decision)
    if chosen_style != "microservices" and services_count <= 2:
        return failures

    spec = task.get("spec", {}) if isinstance(task, dict) else {}
    constraints = spec.get("constraints", {}) if isinstance(spec, dict) else {}
    nfr = spec.get("nfr", {}) if isinstance(spec, dict) else {}
    security = nfr.get("security", {}) if isinstance(nfr, dict) else {}
    compliance = nfr.get("compliance", {}) if isinstance(nfr, dict) else {}
    reliability = nfr.get("reliability", {}) if isinstance(nfr, dict) else {}
    perf = (spec.get("nfr", {}) or {}).get("performance", {}) if isinstance(spec, dict) else {}

    team_size = _safe_int(constraints.get("team_size"), 999)
    mvp_days = _safe_int(constraints.get("time_to_mvp_days"), 9999)
    throughput = _safe_int(perf.get("throughput_rps"), 0)
    p95 = _safe_int(perf.get("p95_latency_ms"), 999999)
    rpo = _safe_int(reliability.get("rpo_minutes"), 999999)
    rto = _safe_int(reliability.get("rto_minutes"), 999999)
    classification = str(security.get("data_classification", "")).lower()
    pci = bool(compliance.get("pci_dss", False))

    high_scale = (throughput >= 500) or (p95 <= 250)
    strict_reliability = (rpo <= 30) or (rto <= 60)
    regulated_or_sensitive = pci or any(x in classification for x in ["confidential", "pii", "payment"])
    org_capacity = (team_size >= 8) and (mvp_days >= 60)
    hard_constraints = high_scale or strict_reliability or regulated_or_sensitive or org_capacity

    # Hard guard requested in PR-B3:
    # >2 services without hard constraints is deterministic service sprawl fail.
    if services_count > 2 and not hard_constraints:
        _add_failure(
            failures,
            tag="service_sprawl",
            message=(
                "service count is mismatched with hard constraints: "
                f"services={services_count}, team_size={team_size}, time_to_mvp_days={mvp_days}, "
                f"throughput_rps={throughput}, p95_latency_ms={p95}, rpo_minutes={rpo}, rto_minutes={rto}"
            ),
            evidence_ref="$.topology.separated_services",
        )

    return failures


def check_must_include_failures(task: Dict[str, Any], decision: Dict[str, Any]) -> List[MustIncludeFailure]:
    """
    Structured must-include failures.

    Tags are deterministic and suitable for hard-gate telemetry:
    - missing_evidence
    - ops_missing_rollback / ops_missing_runbook / ops_missing_monitoring_signals / ops_missing_slo
    - service_sprawl
    - plus token-specific must_include failures
    """
    failures: List[MustIncludeFailure] = []
    failures.extend(_must_token_failures(task, decision))
    failures.extend(_anti_camouflage_evidence_failures(task, decision))
    failures.extend(_anti_camouflage_ops_failures(task, decision))
    failures.extend(_anti_camouflage_arch_sanity_failures(task, decision))
    return _dedupe_failures(failures)


def check_must_include(task: Dict[str, Any], decision: Dict[str, Any]) -> List[str]:
    """
    Backwards-compatible list of missing requirement strings.
    """
    # Keep legacy semantics for call-sites that use must_include as a soft signal
    # (e.g. score penalties). Anti-camouflage hard checks are exposed via
    # check_must_include_failures().
    failures = _must_token_failures(task, decision)
    return [f"{item.tag}: {item.message}" for item in failures]
