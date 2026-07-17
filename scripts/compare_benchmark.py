from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _load(path: str) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare(
    current: Dict[str, Any],
    baseline: Dict[str, Any],
    *,
    max_drop_pass_rate: float = 0.01,
    max_increase_avg_repairs: float = 0.10,
    max_drop_coding_pass_rate: float = 0.01,
    max_increase_coding_avg_repairs: float = 0.10,
    max_increase_coding_scope_violation_rate: float = 0.05,
    include_swarm_metrics: bool = False,
    max_drop_swarm_pass_rate: float = 0.03,
    max_increase_swarm_avg_repairs: float = 0.10,
    max_increase_swarm_scope_violations: float = 0.05,
    max_increase_swarm_timeouts: float = 0.05,
) -> Tuple[bool, List[str]]:
    cur = current.get("metrics", {}) if isinstance(current, dict) else {}
    base = baseline.get("metrics", {}) if isinstance(baseline, dict) else {}

    reasons: List[str] = []
    ok = True

    try:
        cur_pr = float(cur.get("pass_rate_with_repair", 0.0))
        base_pr = float(base.get("pass_rate_with_repair", 0.0))
        drop = base_pr - cur_pr
        if drop > max_drop_pass_rate:
            ok = False
            reasons.append(
                f"pass_rate_with_repair dropped by {drop:.3f} (baseline={base_pr:.3f}, current={cur_pr:.3f})"
            )
    except Exception:
        pass

    try:
        cur_avg = float(cur.get("avg_repairs_used", 0.0))
        base_avg = float(base.get("avg_repairs_used", 0.0))
        inc = cur_avg - base_avg
        if inc > max_increase_avg_repairs:
            ok = False
            reasons.append(
                f"avg_repairs_used increased by {inc:.3f} (baseline={base_avg:.3f}, current={cur_avg:.3f})"
            )
    except Exception:
        pass

    cur_coding = cur.get("coding_suite", {}) if isinstance(cur, dict) else {}
    base_coding = base.get("coding_suite", {}) if isinstance(base, dict) else {}

    try:
        cur_pr = float(cur_coding.get("pass_rate", 0.0))
        base_pr = float(base_coding.get("pass_rate", 0.0))
        drop = base_pr - cur_pr
        if drop > max_drop_coding_pass_rate:
            ok = False
            reasons.append(
                f"coding_pass_rate dropped by {drop:.3f} (baseline={base_pr:.3f}, current={cur_pr:.3f})"
            )
    except Exception:
        pass

    try:
        cur_avg = float(cur_coding.get("avg_repairs_used", 0.0))
        base_avg = float(base_coding.get("avg_repairs_used", 0.0))
        inc = cur_avg - base_avg
        if inc > max_increase_coding_avg_repairs:
            ok = False
            reasons.append(
                f"coding_avg_repairs_used increased by {inc:.3f} (baseline={base_avg:.3f}, current={cur_avg:.3f})"
            )
    except Exception:
        pass

    try:
        cur_rate = float(cur_coding.get("scope_violation_rate", 0.0))
        base_rate = float(base_coding.get("scope_violation_rate", 0.0))
        inc = cur_rate - base_rate
        if inc > max_increase_coding_scope_violation_rate:
            ok = False
            reasons.append(
                "coding_scope_violation_rate increased by "
                f"{inc:.3f} (baseline={base_rate:.3f}, current={cur_rate:.3f})"
            )
    except Exception:
        pass

    if include_swarm_metrics:
        cur_swarm = _extract_swarm_metrics(current)
        base_swarm = _extract_swarm_metrics(baseline)

        if cur_swarm and base_swarm:
            try:
                cur_pr = float(cur_swarm.get("pass_rate", 0.0))
                base_pr = float(base_swarm.get("pass_rate", 0.0))
                drop = base_pr - cur_pr
                if drop > max_drop_swarm_pass_rate:
                    ok = False
                    reasons.append(
                        f"swarm_pass_rate dropped by {drop:.3f} (baseline={base_pr:.3f}, current={cur_pr:.3f})"
                    )
            except Exception:
                pass

            try:
                cur_avg = float(cur_swarm.get("avg_repairs", cur_swarm.get("avg_repairs_used", 0.0)))
                base_avg = float(base_swarm.get("avg_repairs", base_swarm.get("avg_repairs_used", 0.0)))
                inc = cur_avg - base_avg
                if inc > max_increase_swarm_avg_repairs:
                    ok = False
                    reasons.append(
                        f"swarm_avg_repairs increased by {inc:.3f} (baseline={base_avg:.3f}, current={cur_avg:.3f})"
                    )
            except Exception:
                pass

            try:
                cur_scope = float(cur_swarm.get("scope_violation_rate", cur_swarm.get("scope_violations", 0.0)))
                base_scope = float(base_swarm.get("scope_violation_rate", base_swarm.get("scope_violations", 0.0)))
                inc = cur_scope - base_scope
                if inc > max_increase_swarm_scope_violations:
                    ok = False
                    reasons.append(
                        "swarm_scope_violations increased by "
                        f"{inc:.3f} (baseline={base_scope:.3f}, current={cur_scope:.3f})"
                    )
            except Exception:
                pass

            try:
                cur_timeouts = float(cur_swarm.get("timeout_rate", cur_swarm.get("timeouts", 0.0)))
                base_timeouts = float(base_swarm.get("timeout_rate", base_swarm.get("timeouts", 0.0)))
                inc = cur_timeouts - base_timeouts
                if inc > max_increase_swarm_timeouts:
                    ok = False
                    reasons.append(
                        f"swarm_timeouts increased by {inc:.3f} (baseline={base_timeouts:.3f}, current={cur_timeouts:.3f})"
                    )
            except Exception:
                pass
        else:
            reasons.append("swarm_compare_warn: swarm metrics missing in current or baseline payload")

    return ok, reasons


def _extract_swarm_metrics(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    swarm = payload.get("swarm")
    if isinstance(swarm, dict):
        metrics = swarm.get("metrics")
        if isinstance(metrics, dict):
            return metrics

    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        keys = set(metrics.keys())
        if {"pass_rate", "avg_repairs", "scope_violations", "timeouts"}.issubset(keys):
            return metrics

    return {}


def main() -> int:
    p = argparse.ArgumentParser(description="Compare CI benchmark scorecards.")
    p.add_argument("--current", required=True, help="Path to current benchmark JSON.")
    p.add_argument("--baseline", required=True, help="Path to baseline benchmark JSON.")
    p.add_argument(
        "--max-drop-pass-rate",
        type=float,
        default=0.01,
        help="Fail if pass_rate_with_repair drops more than this vs baseline.",
    )
    p.add_argument(
        "--max-increase-avg-repairs",
        type=float,
        default=0.10,
        help="Fail if avg_repairs_used increases more than this vs baseline.",
    )
    p.add_argument(
        "--max-drop-coding-pass-rate",
        type=float,
        default=0.01,
        help="Fail if coding pass_rate drops more than this vs baseline.",
    )
    p.add_argument(
        "--max-increase-coding-avg-repairs",
        type=float,
        default=0.10,
        help="Fail if coding avg_repairs_used increases more than this vs baseline.",
    )
    p.add_argument(
        "--max-increase-coding-scope-violation-rate",
        type=float,
        default=0.05,
        help="Fail if coding scope_violation_rate increases more than this vs baseline.",
    )
    p.add_argument(
        "--include-swarm-metrics",
        action="store_true",
        help="Also compare swarm metrics (pass_rate, avg_repairs, scope_violations, timeouts).",
    )
    p.add_argument(
        "--max-drop-swarm-pass-rate",
        type=float,
        default=0.03,
        help="Fail if swarm pass_rate drops more than this vs baseline.",
    )
    p.add_argument(
        "--max-increase-swarm-avg-repairs",
        type=float,
        default=0.10,
        help="Fail if swarm avg_repairs increases more than this vs baseline.",
    )
    p.add_argument(
        "--max-increase-swarm-scope-violations",
        type=float,
        default=0.05,
        help="Fail if swarm scope_violations (rate if available) increases more than this vs baseline.",
    )
    p.add_argument(
        "--max-increase-swarm-timeouts",
        type=float,
        default=0.05,
        help="Fail if swarm timeouts (rate if available) increases more than this vs baseline.",
    )
    args = p.parse_args()

    current = _load(args.current)
    baseline = _load(args.baseline)

    ok, reasons = compare(
        current,
        baseline,
        max_drop_pass_rate=args.max_drop_pass_rate,
        max_increase_avg_repairs=args.max_increase_avg_repairs,
        max_drop_coding_pass_rate=args.max_drop_coding_pass_rate,
        max_increase_coding_avg_repairs=args.max_increase_coding_avg_repairs,
        max_increase_coding_scope_violation_rate=args.max_increase_coding_scope_violation_rate,
        include_swarm_metrics=bool(args.include_swarm_metrics),
        max_drop_swarm_pass_rate=args.max_drop_swarm_pass_rate,
        max_increase_swarm_avg_repairs=args.max_increase_swarm_avg_repairs,
        max_increase_swarm_scope_violations=args.max_increase_swarm_scope_violations,
        max_increase_swarm_timeouts=args.max_increase_swarm_timeouts,
    )

    payload = {"ok": ok, "reasons": reasons}
    print(f"[benchmark] compare: {json.dumps(payload, ensure_ascii=True)}")

    if not ok:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
