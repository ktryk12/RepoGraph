# verify/run_suite.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from policy.perf_budget_service import budgeted_call
from policy.reason_taxonomy_service import get_reason_taxonomy_service
from verify.run_eval import run_single

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "eval" / "tasks"
GOLD_DIR = REPO_ROOT / "eval" / "gold"


def _fmt(x: Any) -> str:
    try:
        return f"{float(x):.3f}"
    except Exception:
        return str(x)


def _is_pass(out: Dict[str, Any], threshold: float) -> bool:
    scores = out.get("scores", {}) or {}
    total = float(scores.get("total", 0.0))
    must_missing = out.get("must_include_missing", []) or []
    evidence_errors = out.get("evidence_errors", []) or []
    scorecard = out.get("scorecard", {}) or {}
    hard_pass = bool(scorecard.get("hard_pass", out.get("hard_pass", True)))
    return hard_pass and (total >= threshold) and (not must_missing) and (not evidence_errors)


def _print_failure_block(out: Dict[str, Any]) -> None:
    task_id = out.get("task_id", "?")
    decision_id = out.get("decision_id", "?")
    scores = out.get("scores", {}) or {}

    print("\n" + "=" * 72)
    print(f"[FAIL] {task_id}  decision={decision_id}")
    print(
        f"  scores: total={_fmt(scores.get('total'))} "
        f"functional={_fmt(scores.get('functional'))} "
        f"security={_fmt(scores.get('security'))} "
        f"arch_fit={_fmt(scores.get('architecture_fit'))}"
    )

    penalties = out.get("penalties", []) or []
    if penalties:
        print("  penalties:")
        for p in penalties:
            print(f"    - {p}")

    must_missing = out.get("must_include_missing", []) or []
    if must_missing:
        print("  must_include_missing:")
        for m in must_missing:
            print(f"    - {m}")

    evidence_errors = out.get("evidence_errors", []) or []
    if evidence_errors:
        print("  evidence_errors:")
        for e in evidence_errors:
            print(f"    - {e}")

    hard_fail_tags = out.get("hard_fail_tags", []) or []
    if hard_fail_tags:
        print("  hard_fail_tags:")
        for tag in hard_fail_tags:
            print(f"    - {tag}")

    print("=" * 72)


def _print_rationale(out: Dict[str, Any], n: int) -> None:
    r = out.get("rationale_signals", []) or []
    if not r:
        return
    task_id = out.get("task_id", "?")
    print(f"\n  rationale_signals (top {n}) for {task_id}:")
    # sort by weight desc if weight exists
    def key(item: Dict[str, Any]) -> float:
        try:
            return float(item.get("weight", 0.0))
        except Exception:
            return 0.0

    top = sorted(r, key=key, reverse=True)[:n]
    for it in top:
        print(
            f"    - w={_fmt(it.get('weight'))} | {it.get('reason')} "
            f"| signal={it.get('signal')} | path={it.get('evidence_path')}"
        )


def main() -> None:
    import sys

    threshold = 0.85
    debug = False
    fail_fast = False
    show_rationale: Optional[int] = None

    # CLI parsing (minimalist)
    args = sys.argv[1:]
    if args:
        # first positional numeric is threshold
        if args and not args[0].startswith("--"):
            threshold = float(args[0])
            args = args[1:]

    i = 0
    while i < len(args):
        a = args[i].lower()
        if a == "--debug":
            debug = True
        elif a == "--fail-fast":
            fail_fast = True
        elif a in ("--show-rationale", "--rationale"):
            if i + 1 >= len(args):
                raise SystemExit("Usage: --show-rationale <N>")
            show_rationale = int(args[i + 1])
            i += 1
        else:
            raise SystemExit(f"Unknown arg: {args[i]}")
        i += 1

    task_files = sorted(TASKS_DIR.glob("EVAL-*.json"))
    if not task_files:
        raise SystemExit(f"No tasks found in {TASKS_DIR}")

    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    for tf in task_files:
        task_id = tf.stem  # "EVAL-002"
        decision_path = GOLD_DIR / f"arch_{task_id}.json"

        if not decision_path.exists():
            failures.append({"task_id": task_id, "error": f"missing gold decision: {decision_path}"})
            if debug:
                print(f"[MISS] {task_id}  gold decision missing: {decision_path}")
            if fail_fast:
                break
            continue

        out = budgeted_call(
            "judge.run_suite.single",
            lambda: run_single(tf, decision_path),
            metadata={"task_id": task_id},
        )
        get_reason_taxonomy_service().require(
            out.get("hard_fail_tags", []) or [],
            pack_name="judge.run_suite.hard_fail_tags",
        )
        results.append(out)

        scores = out.get("scores", {}) or {}
        total = float(scores.get("total", 0.0))
        must_missing = out.get("must_include_missing", []) or []
        evidence_errors = out.get("evidence_errors", []) or []
        hard_fail_tags = out.get("hard_fail_tags", []) or []
        ok = _is_pass(out, threshold)

        if debug:
            print(
                f"[{'OK' if ok else 'NO'}] {task_id} "
                f"total={_fmt(total)} "
                f"f={_fmt(scores.get('functional'))} "
                f"s={_fmt(scores.get('security'))} "
                f"a={_fmt(scores.get('architecture_fit'))}"
            )
            if show_rationale:
                _print_rationale(out, show_rationale)

        if not ok:
            failures.append({
                "task_id": task_id,
                "total": total,
                "must_include_missing": must_missing,
                "evidence_errors": evidence_errors,
                "penalties": out.get("penalties", []),
                "hard_fail_tags": hard_fail_tags,
            })
            if debug:
                _print_failure_block(out)
            if fail_fast:
                break

    summary = {
        "threshold": threshold,
        "tasks": len(task_files),
        "failed": len(failures),
        "avg_total": sum(float(r["scores"]["total"]) for r in results) / max(1, len(results)),
        "failures": failures,
    }

    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
