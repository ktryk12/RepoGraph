from __future__ import annotations

import argparse
from collections import Counter
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.aesa.scoring.aesa_score import score_task, summarize_scores


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_PATH = REPO_ROOT / "eval" / "aesa" / "tasks_mvp.jsonl"
DEFAULT_HOLDOUT_PATH = REPO_ROOT / "eval" / "aesa" / "tasks_holdout.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AESA MVP eval suite.")
    parser.add_argument("--tasks", default=str(DEFAULT_TASKS_PATH), help="Path to AESA tasks JSONL.")
    parser.add_argument(
        "--holdout",
        default=None,
        help=f"Optional holdout JSONL path. Example: {DEFAULT_HOLDOUT_PATH}",
    )
    parser.add_argument("--max-tasks", type=int, default=None, help="Limit task count.")
    parser.add_argument("--out", default=None, help="Legacy output JSON path (alias of --report).")
    parser.add_argument("--report", default=None, help="Output report JSON path.")
    parser.add_argument(
        "--print-top-failures",
        action="store_true",
        help="Print top failure reasons to stdout.",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=0.70,
        help="Fail (exit 2) if pass_rate falls below this threshold.",
    )
    args = parser.parse_args()

    run_id = _run_id()
    default_report_path = Path("artifacts") / "aesa_suite" / f"{run_id}.json"
    report_path = Path(args.report) if args.report else (Path(args.out) if args.out else default_report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    holdout_path = Path(args.holdout) if args.holdout else None

    payload = run_aesa_suite(
        tasks_path=Path(args.tasks),
        holdout_path=holdout_path,
        max_tasks=args.max_tasks,
        run_id=run_id,
    )
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    report_path.write_text(serialized, encoding="utf-8")
    if args.out and Path(args.out) != report_path:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(serialized, encoding="utf-8")

    gate_metrics = payload.get("swarm", {}).get("metrics", {}) if payload.get("holdout") else payload.get("metrics", {})
    pass_rate = float(gate_metrics.get("pass_rate", payload.get("metrics", {}).get("pass_rate", 0.0)))
    print(f"[aesa-suite] wrote {report_path}")
    print(f"PASS_RATE={pass_rate:.3f} TASKS={int(gate_metrics.get('tasks_executed', payload.get('tasks_executed', 0)))}")
    if args.print_top_failures:
        _print_top_failures(
            payload.get("swarm", {}).get("top_failure_reasons", [])
            if payload.get("holdout")
            else payload.get("top_failure_reasons", []),
            title="top_failures",
        )
    if pass_rate < float(args.min_pass_rate):
        print(
            f"[aesa-suite] gate_failed: pass_rate={pass_rate:.3f} < min_pass_rate={float(args.min_pass_rate):.3f}"
        )
        return 2
    return 0


def run_aesa_suite(
    *,
    tasks_path: Path,
    holdout_path: Optional[Path] = None,
    max_tasks: Optional[int] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    rid = run_id or _run_id()
    primary_tasks = _load_tasks(tasks_path)
    if max_tasks is not None and max_tasks > 0:
        primary_tasks = primary_tasks[: int(max_tasks)]
    primary = _score_rows(primary_tasks)

    holdout: Dict[str, Any] | None = None
    holdout_results: List[Dict[str, Any]] = []
    if holdout_path is not None:
        if not holdout_path.exists():
            raise FileNotFoundError(f"Holdout tasks file not found: {holdout_path}")
        holdout_tasks = _load_tasks(holdout_path)
        if max_tasks is not None and max_tasks > 0:
            holdout_tasks = holdout_tasks[: int(max_tasks)]
        holdout = _score_rows(holdout_tasks)
        holdout["path"] = str(holdout_path)
        holdout_results = list(holdout.get("results", []))

    combined_results = list(primary.get("results", [])) + holdout_results
    combined_metrics = summarize_scores(combined_results)
    swarm_metrics = _swarm_metrics(combined_metrics)

    return {
        "run_id": rid,
        "generated_at": _utc_now(),
        "tasks_total": int(primary.get("tasks_total", 0)),
        "tasks_executed": int(primary.get("tasks_executed", 0)),
        "tasks_passed": int(primary.get("tasks_passed", 0)),
        "metrics": primary.get("metrics", {}),
        "scoreline": primary.get("scoreline", {}),
        "top_failure_reasons": primary.get("top_failure_reasons", []),
        "results": primary.get("results", []),
        "holdout": holdout,
        "swarm": {
            "metrics": swarm_metrics,
            "top_failure_reasons": _top_failure_reasons(combined_results),
            "tasks_executed": int(combined_metrics.get("tasks_executed", 0)),
            "tasks_passed": int(combined_metrics.get("tasks_passed", 0)),
        },
    }


def _score_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    for idx, task in enumerate(rows, start=1):
        task_id = str(task.get("task_id") or f"AESA-{idx:03d}")
        signals = task.get("signals")
        if not isinstance(signals, dict):
            results.append({
                "task_id": task_id,
                "category": str(task.get("category", "unknown")),
                "passed": False,
                "reasons": ["missing_signals"],
                "scores": {
                    "tests": 0.0,
                    "lint": 0.0,
                    "scope": 0.0,
                    "patch_size": 0.0,
                    "latency": 0.0,
                    "total": 0.0,
                },
                "signals": {},
            })
            continue

        scored = score_task(task=task, signals=signals)
        results.append(scored)

    metrics = summarize_scores(results)
    scoreline = _scoreline_from_metrics(metrics)

    return {
        "tasks_total": len(results),
        "tasks_executed": int(metrics.get("tasks_executed", 0)),
        "tasks_passed": int(metrics.get("tasks_passed", 0)),
        "metrics": metrics,
        "scoreline": scoreline,
        "top_failure_reasons": _top_failure_reasons(results),
        "results": results,
    }


def _scoreline_from_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pass_rate": metrics.get("pass_rate", 0.0),
        "avg_total_score": metrics.get("avg_total_score", 0.0),
        "scope_violation_rate": metrics.get("scope_violation_rate", 0.0),
        "avg_latency_ms": metrics.get("avg_latency_ms", 0.0),
        "avg_repairs": metrics.get("avg_repairs", metrics.get("avg_repairs_used", 0.0)),
        "scope_violations": metrics.get("scope_violations", 0),
        "timeouts": metrics.get("timeouts", 0),
    }


def _swarm_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "pass_rate": float(metrics.get("pass_rate", 0.0)),
        "avg_repairs": float(metrics.get("avg_repairs", metrics.get("avg_repairs_used", 0.0))),
        "scope_violations": int(metrics.get("scope_violations", 0)),
        "scope_violation_rate": float(metrics.get("scope_violation_rate", 0.0)),
        "timeouts": int(metrics.get("timeouts", 0)),
        "timeout_rate": float(metrics.get("timeout_rate", 0.0)),
        "tasks_executed": int(metrics.get("tasks_executed", 0)),
        "tasks_passed": int(metrics.get("tasks_passed", 0)),
    }


def _top_failure_reasons(results: List[Dict[str, Any]], *, limit: int = 5) -> List[Dict[str, Any]]:
    failures = [r for r in results if isinstance(r, dict) and not bool(r.get("passed"))]
    if not failures:
        return []

    counts: Counter[str] = Counter()
    for row in failures:
        reasons = row.get("reasons")
        if isinstance(reasons, list) and reasons:
            for reason in reasons:
                txt = str(reason).strip()
                if txt:
                    counts[txt] += 1
        else:
            counts["unknown_failure"] += 1

    total = sum(counts.values()) or 1
    out: List[Dict[str, Any]] = []
    for reason, count in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[: max(1, int(limit))]:
        out.append(
            {
                "reason": reason,
                "count": int(count),
                "share": round(float(count) / float(total), 3),
            }
        )
    return out


def _print_top_failures(top: List[Dict[str, Any]], *, title: str) -> None:
    print(f"[aesa-suite] {title}:")
    if not top:
        print("- (none)")
        return
    for item in top:
        print(f"- {item.get('reason')}: count={item.get('count')} share={item.get('share')}")


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _run_id() -> str:
    return f"aesa-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
