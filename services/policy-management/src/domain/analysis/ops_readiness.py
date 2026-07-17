from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml
import os

_OPS_CATEGORIES = ("ci_cd", "observability", "orchestration", "secrets", "on_call")


@dataclass(frozen=True)
class OpsReadinessStatus:
    required: int
    present: int
    missing: List[str]
    signals: Dict[str, bool]
    passes: bool


@lru_cache(maxsize=1)
def load_policy_rules(path: str | None = None) -> Dict[str, Any]:
    """
    Load policy_rules.yaml with caching. Returns {} on failure.
    """
    try:
        if path is None:
            env_path = os.getenv("POLICY_RULES_PATH")
            if env_path:
                path = env_path
        if path:
            p = Path(path)
        else:
            p = Path(__file__).resolve().with_name("policy_rules.yaml")
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def ops_readiness_services_threshold(rules: Dict[str, Any], default: int = 3) -> int:
    limits = rules.get("limits", {}) if isinstance(rules, dict) else {}
    value = limits.get("ops_readiness_services_threshold", default)
    try:
        return int(value)
    except Exception:
        return int(default)


def required_ops_for_services(
    services_count: int,
    rules: Dict[str, Any] | None = None,
    default_required: int = 3,
) -> int:
    """
    Compute required ops signals based on service count.

    Uses limits.ops_readiness_required_signals_by_services if present,
    otherwise falls back to limits.ops_readiness_min_signals (or default_required).
    """
    if rules is None:
        rules = load_policy_rules()
    limits = rules.get("limits", {}) if isinstance(rules, dict) else {}
    table = limits.get("ops_readiness_required_signals_by_services", [])
    fallback = limits.get("ops_readiness_min_signals", default_required)

    try:
        fallback = int(fallback)
    except Exception:
        fallback = int(default_required)

    if isinstance(table, list):
        for row in table:
            if isinstance(row, dict):
                mx = row.get("max_services")
                req = row.get("required")
                if isinstance(mx, int) and isinstance(req, int) and services_count <= mx:
                    return req

    return fallback


def extract_ops_signals(decision: Dict[str, Any], *, allow_text_fallback: bool = False) -> Dict[str, bool]:
    """
    Extract ops readiness signals.

    Prefer structured ops_readiness if present; fall back to text heuristics.
    """
    present = {k: False for k in _OPS_CATEGORIES}

    ops = decision.get("ops_readiness")
    if isinstance(ops, dict):
        for key in _OPS_CATEGORIES:
            if _structured_actionable(ops.get(key)):
                present[key] = True
        return present

    if not allow_text_fallback:
        return present

    text = _collect_ops_text(decision)
    if text:
        for key, keywords in _OPS_KEYWORDS.items():
            if not present.get(key) and any(kw in text for kw in keywords):
                present[key] = True

    return present


def services_count_from_decision(decision: Dict[str, Any]) -> int:
    topo = decision.get("topology", {})
    if not isinstance(topo, dict):
        return 0
    ss = topo.get("separated_services", [])
    if not isinstance(ss, list):
        return 0
    return sum(1 for s in ss if isinstance(s, str) and s.strip())


def ops_readiness_status(
    decision: Dict[str, Any],
    *,
    rules: Dict[str, Any] | None = None,
) -> OpsReadinessStatus:
    """
    Compute ops readiness status from structured signals.

    Uses required_ops_for_services() and extract_ops_signals(..., allow_text_fallback=False).
    """
    services_count = services_count_from_decision(decision)
    required = required_ops_for_services(services_count, rules)
    signals = extract_ops_signals(decision, allow_text_fallback=False)
    present = sum(1 for v in signals.values() if v)
    missing = [k for k, v in signals.items() if not v]
    return OpsReadinessStatus(
        required=required,
        present=present,
        missing=missing,
        signals=signals,
        passes=present >= required,
    )


def _collect_ops_text(decision: Dict[str, Any]) -> str:
    chunks: List[str] = []
    for key in [
        "verification_plan",
        "stop_conditions",
        "rationale",
        "risks",
        "recommendations",
        "operational_plan",
        "deployment_plan",
        "security_controls",
        "operability",
        "deployment",
    ]:
        _collect_text(decision.get(key), chunks)
    return " ".join(chunks).lower()


def _collect_text(value: Any, out: List[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        out.append(value)
        return
    if isinstance(value, dict):
        for v in value.values():
            _collect_text(v, out)
        return
    if isinstance(value, list):
        for item in value:
            _collect_text(item, out)
        return


def _structured_actionable(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str) and value.strip():
        return True
    if isinstance(value, (int, float)) and value > 0:
        return True
    if isinstance(value, list):
        return any(_structured_actionable(v) for v in value)
    if isinstance(value, dict):
        return any(_structured_actionable(v) for v in value.values())
    return False


_OPS_KEYWORDS: Dict[str, List[str]] = {
    "ci_cd": [
        "ci/cd",
        "ci cd",
        "pipeline",
        "github actions",
        "gitlab ci",
        "buildkite",
        "deploy",
        "rollback",
        "blue/green",
        "canary",
    ],
    "observability": [
        "observability",
        "metrics",
        "logs",
        "logging",
        "tracing",
        "alerts",
        "slo",
        "sli",
        "dashboard",
        "opentelemetry",
        "otel",
    ],
    "orchestration": [
        "kubernetes",
        "k8s",
        "docker",
        "container",
        "containers",
        "ecs",
        "nomad",
        "orchestration",
        "helm",
        "compose",
    ],
    "secrets": [
        "vault",
        "kms",
        "secrets",
        "secret",
        "rotation",
        "key management",
        "hsm",
    ],
    "on_call": [
        "on-call",
        "oncall",
        "runbook",
        "incident",
        "pager",
        "escalation",
        "postmortem",
    ],
}
