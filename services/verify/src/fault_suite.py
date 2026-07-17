from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping


FAULT_SUITE_SCHEMA_VERSION = 2

POLICY_FAIL_CLOSED = "fail_closed"
POLICY_FALLBACK_LOCAL = "fallback_local"
POLICY_VERBATIM_ONLY = "verbatim_only"
ALLOWED_POLICIES = {
    POLICY_FAIL_CLOSED,
    POLICY_FALLBACK_LOCAL,
    POLICY_VERBATIM_ONLY,
}

FAULT_TYPES = (
    "timeout",
    "partial_output",
    "disk_full",
    "net_partition",
    "auth_failure",
    "slow_judge",
)


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_policy(value: Any, *, default: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in ALLOWED_POLICIES:
        return raw
    if raw == "fallback_default":
        return POLICY_FALLBACK_LOCAL
    return default


def resolve_failure_policies(env: Mapping[str, str] | None = None) -> Dict[str, str]:
    source = env if env is not None else {}
    return {
        "context_plane": _normalize_policy(
            source.get("CONTEXT_PLANE_FAILURE_MODE"),
            default=POLICY_FALLBACK_LOCAL,
        ),
        "tool_runtime": _normalize_policy(
            source.get("TOOL_RUNTIME_FAILURE_MODE"),
            default=POLICY_FAIL_CLOSED,
        ),
        "judge": _normalize_policy(
            source.get("JUDGE_FAILURE_MODE"),
            default=POLICY_FAIL_CLOSED,
        ),
    }


def _stable_jitter(case_id: str, *, span: float) -> float:
    seed = hashlib.sha256(case_id.encode("utf-8")).hexdigest()
    bucket = int(seed[:8], 16) % 1000
    centered = (bucket / 999.0) - 0.5
    return centered * span


def _effective_policy(*, fault_type: str, configured_policy: str) -> str:
    # Auth failures are always fail-closed.
    if fault_type == "auth_failure":
        return POLICY_FAIL_CLOSED
    # Disk-full should avoid local write-heavy fallback paths.
    if fault_type == "disk_full" and configured_policy == POLICY_FALLBACK_LOCAL:
        return POLICY_VERBATIM_ONLY
    return configured_policy


def _hard_pass(*, fault_type: str, effective_policy: str) -> bool:
    if effective_policy == POLICY_FAIL_CLOSED:
        return False
    if fault_type in {"auth_failure", "partial_output", "disk_full"}:
        return False
    return True


def _contained(*, fault_type: str, stop_reason: str) -> bool:
    if fault_type == "auth_failure":
        return stop_reason == POLICY_FAIL_CLOSED
    if fault_type == "disk_full":
        return stop_reason in {POLICY_FAIL_CLOSED, POLICY_VERBATIM_ONLY}
    return stop_reason in {POLICY_FAIL_CLOSED, POLICY_FALLBACK_LOCAL, POLICY_VERBATIM_ONLY, "no_progress"}


def _repair_steps(case_id: str, *, max_repair_steps: int) -> int:
    capped = max(1, int(max_repair_steps))
    seed = hashlib.sha256(f"repairs:{case_id}".encode("utf-8")).hexdigest()
    return 1 + (int(seed[-8:], 16) % capped)


def _soft_score(case_id: str, *, fault_type: str) -> float:
    base = {
        "timeout": 0.89,
        "partial_output": 0.93,
        "disk_full": 0.78,
        "net_partition": 0.87,
        "auth_failure": 0.96,
        "slow_judge": 0.90,
    }.get(fault_type, 0.85)
    return max(0.0, min(1.0, base + _stable_jitter(case_id, span=0.04)))


def _service_order(service_policies: Mapping[str, str]) -> List[str]:
    preferred = ["context_plane", "tool_runtime", "judge"]
    seen = set()
    ordered: List[str] = []
    for name in preferred:
        if name in service_policies:
            seen.add(name)
            ordered.append(name)
    for name in sorted(service_policies.keys()):
        if name not in seen:
            ordered.append(name)
    return ordered


def _build_matrix(*, service_policies: Mapping[str, str], max_cases: int) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    services = _service_order(service_policies)
    if not services:
        return rows

    for service in services:
        configured_policy = _normalize_policy(service_policies.get(service), default=POLICY_FAIL_CLOSED)
        for fault_type in FAULT_TYPES:
            rows.append(
                {
                    "service": service,
                    "fault_type": fault_type,
                    "configured_policy": configured_policy,
                }
            )

    cap = max(1, int(max_cases))
    if len(rows) >= cap:
        return rows[:cap]

    # Deterministic expansion when max_cases > base matrix.
    expanded: List[Dict[str, str]] = list(rows)
    idx = 0
    while len(expanded) < cap:
        base = rows[idx % len(rows)]
        alt_policy = [POLICY_FAIL_CLOSED, POLICY_FALLBACK_LOCAL, POLICY_VERBATIM_ONLY][idx % 3]
        expanded.append(
            {
                "service": base["service"],
                "fault_type": base["fault_type"],
                "configured_policy": alt_policy,
            }
        )
        idx += 1
    return expanded


def run_fault_suite(
    *,
    run_id: str,
    max_cases: int = 18,
    max_repair_steps: int = 3,
    soft_pass_threshold: float = 0.85,
    service_policies: Mapping[str, str] | None = None,
    created_at_utc: str | None = None,
) -> Dict[str, Any]:
    normalized_policies: Dict[str, str] = {}
    for service, policy in dict(service_policies or resolve_failure_policies()).items():
        normalized_policies[str(service)] = _normalize_policy(policy, default=POLICY_FAIL_CLOSED)

    rows = _build_matrix(service_policies=normalized_policies, max_cases=max_cases)
    cases: List[Dict[str, Any]] = []
    stop_counts: Counter[str] = Counter()
    fault_counts: Counter[str] = Counter()
    policy_counts: Counter[str] = Counter()
    contained_count = 0
    fail_closed_count = 0
    hard_fail_count = 0
    repair_steps_sum = 0

    for idx, row in enumerate(rows, start=1):
        service = row["service"]
        fault_type = row["fault_type"]
        configured_policy = row["configured_policy"]
        case_id = f"fault-{idx:03d}:{service}:{fault_type}"
        effective = _effective_policy(fault_type=fault_type, configured_policy=configured_policy)
        repair_steps = _repair_steps(case_id, max_repair_steps=max_repair_steps)
        soft_score = _soft_score(case_id, fault_type=fault_type)
        hard_pass = _hard_pass(fault_type=fault_type, effective_policy=effective)
        stop_reason = "no_progress" if (not hard_pass and repair_steps >= max(1, int(max_repair_steps))) else effective
        contained = _contained(fault_type=fault_type, stop_reason=stop_reason)
        overall_pass = bool(hard_pass and soft_score >= float(soft_pass_threshold))

        cases.append(
            {
                "case_id": case_id,
                "service": service,
                "fault_type": fault_type,
                "configured_policy": configured_policy,
                "effective_policy": effective,
                "stop_reason": stop_reason,
                "repair_steps": int(repair_steps),
                "soft_score": round(float(soft_score), 3),
                "hard_pass": bool(hard_pass),
                "overall_pass": bool(overall_pass),
                "contained": bool(contained),
            }
        )
        stop_counts[stop_reason] += 1
        fault_counts[fault_type] += 1
        policy_counts[effective] += 1
        if contained:
            contained_count += 1
        if effective == POLICY_FAIL_CLOSED or stop_reason == POLICY_FAIL_CLOSED:
            fail_closed_count += 1
        if not hard_pass:
            hard_fail_count += 1
        repair_steps_sum += int(repair_steps)

    total = len(cases)
    summary = {
        "total_cases": int(total),
        "hard_fail_cases": int(hard_fail_count),
        "hard_fail_rate": round((hard_fail_count / total) if total else 0.0, 3),
        "fail_closed_count": int(fail_closed_count),
        "fail_closed_rate": round((fail_closed_count / total) if total else 0.0, 3),
        "fault_containment_pass_count": int(contained_count),
        "fault_containment_pass_rate": round((contained_count / total) if total else 0.0, 3),
        "mean_repair_steps": round((repair_steps_sum / total) if total else 0.0, 3),
        "stop_reason_counts": [
            {"stop_reason": reason, "count": count}
            for reason, count in sorted(stop_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "fault_type_counts": [
            {"fault_type": fault, "count": count}
            for fault, count in sorted(fault_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "effective_policy_counts": [
            {"policy": policy, "count": count}
            for policy, count in sorted(policy_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }

    return {
        "schema_version": int(FAULT_SUITE_SCHEMA_VERSION),
        "run_id": str(run_id),
        "created_at_utc": str(created_at_utc or _now_utc_iso()),
        "config": {
            "max_cases": int(max_cases),
            "max_repair_steps": int(max_repair_steps),
            "soft_pass_threshold": float(soft_pass_threshold),
            "service_policies": normalized_policies,
        },
        "cases": cases,
        "summary": summary,
    }


def extract_fault_metrics(report: Mapping[str, Any]) -> Dict[str, Any]:
    summary = report.get("summary") if isinstance(report, Mapping) else None
    if not isinstance(summary, Mapping):
        return {
            "fail_closed_rate": 0.0,
            "fault_containment_pass_rate": 0.0,
            "mean_repair_steps": 0.0,
        }
    return {
        "fail_closed_rate": float(summary.get("fail_closed_rate", 0.0) or 0.0),
        "fault_containment_pass_rate": float(summary.get("fault_containment_pass_rate", 0.0) or 0.0),
        "mean_repair_steps": float(summary.get("mean_repair_steps", 0.0) or 0.0),
    }


def normalize_fault_suite_report(payload: Mapping[str, Any]) -> Dict[str, Any]:
    schema_version = int(payload.get("schema_version", 1)) if isinstance(payload, Mapping) else 1
    if schema_version >= FAULT_SUITE_SCHEMA_VERSION:
        return dict(payload)

    run_id = str(payload.get("run_id") or "legacy-fault-suite")
    created_at = str(payload.get("created_at_utc") or _now_utc_iso())
    rows = payload.get("cases") if isinstance(payload.get("cases"), list) else payload.get("results")
    if not isinstance(rows, list):
        rows = []

    cases: List[Dict[str, Any]] = []
    for idx, raw in enumerate(rows, start=1):
        if not isinstance(raw, Mapping):
            continue
        case_id = str(raw.get("case_id") or raw.get("id") or f"legacy-{idx:03d}")
        service = str(raw.get("service") or "unknown")
        fault_type = str(raw.get("fault_type") or raw.get("fault") or "unknown")
        configured_policy = _normalize_policy(raw.get("configured_policy") or raw.get("policy"), default=POLICY_FAIL_CLOSED)
        effective_policy = _normalize_policy(raw.get("effective_policy"), default=configured_policy)
        stop_reason = str(raw.get("stop_reason") or effective_policy)
        repair_steps = int(raw.get("repair_steps") or raw.get("repairs_used") or 0)
        soft_score = float(raw.get("soft_score") or raw.get("score") or 0.0)
        hard_pass = bool(raw.get("hard_pass", False))
        overall_pass = bool(raw.get("overall_pass", hard_pass and soft_score >= 0.85))
        contained = bool(raw.get("contained", _contained(fault_type=fault_type, stop_reason=stop_reason)))
        cases.append(
            {
                "case_id": case_id,
                "service": service,
                "fault_type": fault_type,
                "configured_policy": configured_policy,
                "effective_policy": effective_policy,
                "stop_reason": stop_reason,
                "repair_steps": repair_steps,
                "soft_score": round(soft_score, 3),
                "hard_pass": hard_pass,
                "overall_pass": overall_pass,
                "contained": contained,
            }
        )

    max_steps = max(1, max((int(case.get("repair_steps", 1)) for case in cases), default=1))
    total = len(cases)
    stop_counts: Counter[str] = Counter()
    fault_counts: Counter[str] = Counter()
    policy_counts: Counter[str] = Counter()
    contained_count = 0
    fail_closed_count = 0
    hard_fail_count = 0
    repair_steps_sum = 0
    for case in cases:
        stop = str(case.get("stop_reason") or "")
        fault = str(case.get("fault_type") or "")
        policy = str(case.get("effective_policy") or "")
        stop_counts[stop] += 1
        fault_counts[fault] += 1
        policy_counts[policy] += 1
        if bool(case.get("contained", False)):
            contained_count += 1
        if stop == POLICY_FAIL_CLOSED or policy == POLICY_FAIL_CLOSED:
            fail_closed_count += 1
        if not bool(case.get("hard_pass", False)):
            hard_fail_count += 1
        repair_steps_sum += int(case.get("repair_steps", 0) or 0)

    summary = {
        "total_cases": int(total),
        "hard_fail_cases": int(hard_fail_count),
        "hard_fail_rate": round((hard_fail_count / total) if total else 0.0, 3),
        "fail_closed_count": int(fail_closed_count),
        "fail_closed_rate": round((fail_closed_count / total) if total else 0.0, 3),
        "fault_containment_pass_count": int(contained_count),
        "fault_containment_pass_rate": round((contained_count / total) if total else 0.0, 3),
        "mean_repair_steps": round((repair_steps_sum / total) if total else 0.0, 3),
        "stop_reason_counts": [
            {"stop_reason": reason, "count": count}
            for reason, count in sorted(stop_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "fault_type_counts": [
            {"fault_type": fault, "count": count}
            for fault, count in sorted(fault_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "effective_policy_counts": [
            {"policy": policy, "count": count}
            for policy, count in sorted(policy_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }
    return {
        "schema_version": int(FAULT_SUITE_SCHEMA_VERSION),
        "run_id": run_id,
        "created_at_utc": created_at,
        "config": {
            "max_cases": int(max(1, len(cases))),
            "max_repair_steps": int(max_steps),
            "soft_pass_threshold": 0.85,
            "service_policies": {},
        },
        "cases": cases,
        "summary": summary,
    }
