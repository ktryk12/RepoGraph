# policy/musts.py
from __future__ import annotations

from typing import Any, Dict, List, Mapping


def _spec_get(spec: Mapping[str, Any], path: List[str], default=None):
    cur: Any = spec
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def derive_must_from_spec(spec: Mapping[str, Any]) -> List[str]:
    """
    Spec-driven requirements.
    Keep in sync with policy/must_include_checks.py.
    Only derive musts that spec explicitly requires.
    """
    must: List[str] = []

    if bool(_spec_get(spec, ["nfr", "operability", "audit_log_required"], False)):
        must.append("audit_completeness_tests")

    if bool(_spec_get(spec, ["nfr", "security", "threat_model_required"], False)):
        must.append("threat_model")

    if bool(_spec_get(spec, ["nfr", "security", "rate_limiting_required"], False)):
        must.append("rate_limiting")

    return must


def dedupe_keep_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out


def required_must_tokens(task: Dict[str, Any]) -> List[str]:
    """
    Union(task.expected.must_include + derive_must_from_spec(task.spec)) with stable order.
    """
    must_task = (task.get("expected", {}) or {}).get("must_include", []) or []
    spec = task.get("spec", {}) or {}
    must_spec = derive_must_from_spec(spec)
    return dedupe_keep_order([*must_task, *must_spec])
