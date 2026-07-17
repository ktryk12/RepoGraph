# verify/run_redteam_suite.py
from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from policy.perf_budget_service import budgeted_call
from policy.reason_taxonomy_service import get_reason_taxonomy_service
from verify.run_eval import run_single
import babyai_shared.eval.make_bad_variant as badmut

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "eval" / "tasks"
GOLD_DIR = REPO_ROOT / "eval" / "gold"
BAD_DIR = REPO_ROOT / "eval" / "bad"


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


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dedupe(chains: List[List[str]]) -> List[List[str]]:
    seen = set()
    out: List[List[str]] = []
    for c in chains:
        t = tuple(c)
        if t not in seen:
            seen.add(t)
            out.append(c)
    return out


def _spec_get(spec: Dict[str, Any], path: List[str], default=None):
    cur: Any = spec
    for k in path:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return default
    return cur


def _pick_mutations_for_task(task: Dict[str, Any], *, known: Dict[str, Any]) -> List[List[str]]:
    """
    Vælg kun mutationer der *burde* fejle for den konkrete task.
    Ellers får du falske "unexpected pass" (fx force_microservices hvor microservices er allowed).
    """
    task_id = task.get("task_id", "UNKNOWN")
    spec = task.get("spec", {}) or {}
    expected = task.get("expected", {}) or {}

    allowed = expected.get("allowed_styles", []) or []
    forbidden = expected.get("forbidden", []) or []
    must = expected.get("must_include", []) or []

    team = int(_spec_get(spec, ["constraints", "team_size"], 999))

    chains: List[List[str]] = []

    def add(chain: List[str]) -> None:
        # Tilføj kun hvis ALLE mutationer i chain faktisk findes i badmut.MUTATIONS
        if all(m in known for m in chain):
            chains.append(chain)

    # 1) Altid en "schema breaker" (ERROR er OK for en bad variant)
    add(["strip_verification_plan"])

    # 2) Altid en "evidence breaker" (FAIL via evidence_errors)
    add(["break_evidence_paths"])

    # 3) Must-include målrettet sabotage (kun når relevant)
    if "contract_tests" in must:
        add(["remove_contract_tests"])
    if "sast_pass" in must:
        add(["remove_sast"])
    if "threat_model" in must:
        add(["remove_threat_model"])
    if ("audit_completeness_tests" in must) or ("audit_log" in must):
        add(["remove_audit"])
    if "rate_limiting" in must:
        add(["remove_rate_limiting_tests"])

    # 4) Force microservices er kun "ond", når microservices IKKE er allowed (eller er forbidden)
    if ("microservices" not in allowed) or ("microservices" in forbidden):
        add(["force_microservices"])

    # 5) Service sprawl (no-ops) should fail regardless of team size
    add(["service_sprawl_no_ops"])

    # 6) Ekstra: rationale-sabotage, men kun hvis mutation findes
    # (typisk relevant for EVAL-009, men skader ofte generelt)
    add(["weaken_rationale_weights"])

    # 7) Ekstra: hvis du senere laver "strip_rationale" osv, kan de komme her

    # (valgfrit) Sørg for mindst 3 chains (ellers kan en task ende med kun 1-2 tests)
    # Vi fylder ikke med tilfældige "force_microservices/service_sprawl", fordi de kan være legit.
    return _dedupe(chains)


def _apply_mutations(decision: Dict[str, Any], chain: List[str]) -> Dict[str, Any]:
    d = json.loads(json.dumps(decision))  # deep copy
    for m in chain:
        fn = badmut.MUTATIONS.get(m)
        if fn is None:
            raise SystemExit(f"[redteam] Unknown mutation '{m}'. Known: {sorted(badmut.MUTATIONS.keys())}")
        d = fn(d)
    return d


def _safe_run(task_path: Path, decision_path: Path, *, debug: bool = False) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Returns (out, error). If schema validation explodes, we capture the exception string.
    """
    try:
        out = budgeted_call(
            "court.run_redteam.single",
            lambda: run_single(task_path, decision_path),
            metadata={"task_path": str(task_path), "decision_path": str(decision_path)},
        )
        return out, None
    except Exception as e:
        if debug:
            return None, f"{type(e).__name__}: {e}\n\n{traceback.format_exc()}"
        return None, f"{type(e).__name__}: {e}"


def main() -> None:
    import sys

    threshold = 0.85
    debug = False
    fail_fast = False

    args = sys.argv[1:]
    if args and not args[0].startswith("--"):
        threshold = float(args[0])
        args = args[1:]

    for a in args:
        if a.lower() == "--debug":
            debug = True
        elif a.lower() == "--fail-fast":
            fail_fast = True
        else:
            raise SystemExit("Usage: python -m verify.run_redteam_suite [threshold] [--debug] [--fail-fast]")

    task_files = sorted(TASKS_DIR.glob("EVAL-*.json"))
    if not task_files:
        raise SystemExit(f"No tasks found in {TASKS_DIR}")

    BAD_DIR.mkdir(parents=True, exist_ok=True)

    report: Dict[str, Any] = {
        "threshold": threshold,
        "tasks": len(task_files),
        "gold_failed": 0,
        "bad_variants_unexpectedly_passed": 0,
        "failed": 0,
        "failures": [],
        "details": [],
    }

    hard_failures: List[Dict[str, Any]] = []

    for tf in task_files:
        task = _load_json(tf)
        task_id = task.get("task_id") or tf.stem
        gold_path = GOLD_DIR / f"arch_{task_id}.json"

        if not gold_path.exists():
            report["gold_failed"] += 1
            hard_failures.append({"task_id": task_id, "error": f"missing gold decision: {gold_path}"})
            if debug:
                print(f"[MISS] {task_id} gold missing: {gold_path}")
            if fail_fast:
                break
            continue

        gold_out, gold_err = _safe_run(tf, gold_path, debug=debug)
        if gold_err:
            gold_ok = False
            report["gold_failed"] += 1
            if debug:
                print(f"[GOLD NO] {task_id} ERROR:\n{gold_err}")
        else:
            gold_ok = _is_pass(gold_out or {}, threshold)
            if debug:
                s = (gold_out or {}).get("scores", {}) or {}
                print(
                    f"[GOLD {'OK' if gold_ok else 'NO'}] {task_id} "
                    f"total={_fmt(s.get('total'))} f={_fmt(s.get('functional'))} "
                    f"s={_fmt(s.get('security'))} a={_fmt(s.get('architecture_fit'))}"
                )

        gold_decision = _load_json(gold_path)

        mutation_chains = _pick_mutations_for_task(task, known=badmut.MUTATIONS)
        if debug:
            print(f"  [MUTS] {task_id}: {mutation_chains}")

        bad_results: List[Dict[str, Any]] = []
        bad_passed: List[Dict[str, Any]] = []

        for chain in mutation_chains:
            mutated = _apply_mutations(gold_decision, chain)
            bad_path = BAD_DIR / f"arch_{task_id}__{'__'.join(chain)}.json"
            _write_json(bad_path, mutated)

            out, err = _safe_run(tf, bad_path, debug=debug)

            if err:
                # For a bad variant, ERROR is acceptable (schema validation or crash)
                rec = {
                    "mutations": chain,
                    "path": str(bad_path),
                    "expected": "FAIL",
                    "actual": "ERROR",
                    "error": err,
                    "total": 0.0,
                    "must_include_missing": [],
                    "evidence_errors": [],
                    "penalties": [],
                }
                bad_results.append(rec)
                if debug:
                    print(f"  [BAD OK] {task_id} mut={' + '.join(chain)} ERROR (expected): {err.splitlines()[0]}")
                continue

            scores = (out or {}).get("scores", {}) or {}
            if isinstance(out, dict):
                get_reason_taxonomy_service().require(
                    out.get("hard_fail_tags", []) or [],
                    pack_name="court.run_redteam.hard_fail_tags",
                )
            total = float(scores.get("total", 0.0))
            must_missing = (out or {}).get("must_include_missing", []) or []
            evidence_errors = (out or {}).get("evidence_errors", []) or []
            penalties = (out or {}).get("penalties", []) or []

            passed = _is_pass(out or {}, threshold)

            rec = {
                "mutations": chain,
                "path": str(bad_path),
                "expected": "FAIL",
                "actual": "PASS" if passed else "FAIL",
                "total": total,
                "must_include_missing": must_missing,
                "evidence_errors": evidence_errors,
                "hard_fail_tags": (out or {}).get("hard_fail_tags", []) or [],
                "penalties": penalties,
            }
            bad_results.append(rec)

            if passed:
                bad_passed.append(rec)
                report["bad_variants_unexpectedly_passed"] += 1

            if debug:
                print(
                    f"  [BAD {'NO' if passed else 'OK'}] {task_id} mut={' + '.join(chain)} "
                    f"total={_fmt(total)} must_missing={len(must_missing)} evidence_errors={len(evidence_errors)}"
                )
                if passed and penalties:
                    print(f"    penalties={penalties}")

            if fail_fast and (not gold_ok or bad_passed):
                break

        report["details"].append({
            "task_id": task_id,
            "gold": {
                "passed": gold_ok,
                "error": gold_err,
                "total": float(((gold_out or {}).get("scores", {}) or {}).get("total", 0.0)),
                "must_include_missing": (gold_out or {}).get("must_include_missing", []) or [],
                "evidence_errors": (gold_out or {}).get("evidence_errors", []) or [],
                "penalties": (gold_out or {}).get("penalties", []) or [],
            },
            "bad_variants": bad_results,
            "bad_variants_passed": bad_passed,
        })

        if (not gold_ok) or bad_passed:
            hard_failures.append({
                "task_id": task_id,
                "gold_failed": not gold_ok,
                "bad_variants_passed": bad_passed,
            })
            if fail_fast:
                break

    report["failed"] = len(hard_failures)
    report["failures"] = hard_failures

    print(json.dumps(report, indent=2, ensure_ascii=False))

    if hard_failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
