from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Tuple


def _run_aesa_suite(
    *,
    out_path: Path,
    model_path: str | None,
    tasks: str | None,
    holdout: str | None,
    seed: int,
) -> Dict[str, Any]:
    env = os.environ.copy()
    if model_path:
        env["AESA_ROUTER_MODEL"] = model_path
    else:
        env.pop("AESA_ROUTER_MODEL", None)
    env["PYTHONHASHSEED"] = str(int(seed))
    env["AESA_EVAL_SEED"] = str(int(seed))

    cmd = [sys.executable, "-m", "verify.run_aesa_suite", "--report", str(out_path)]
    if tasks:
        cmd.extend(["--tasks", tasks])
    if holdout:
        cmd.extend(["--holdout", holdout])
    rc = subprocess.run(cmd, check=False, env=env).returncode
    if rc != 0:
        raise RuntimeError(f"run_aesa_suite failed rc={rc} model={model_path or '<disabled>'}")
    return json.loads(out_path.read_text(encoding="utf-8"))


def _metrics(payload: Dict[str, Any]) -> Dict[str, float]:
    swarm = payload.get("swarm")
    if isinstance(swarm, dict):
        metrics = swarm.get("metrics")
        if isinstance(metrics, dict):
            return {
                "pass_rate": float(metrics.get("pass_rate", 0.0)),
                "avg_repairs": float(metrics.get("avg_repairs", metrics.get("avg_repairs_used", 0.0))),
                "timeouts": float(metrics.get("timeouts", 0.0)),
                "scope_violations": float(metrics.get("scope_violations", 0.0)),
                "tasks_executed": float(metrics.get("tasks_executed", 0.0)),
            }
    metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
    return {
        "pass_rate": float(metrics.get("pass_rate", 0.0)),
        "avg_repairs": float(metrics.get("avg_repairs", metrics.get("avg_repairs_used", 0.0))),
        "timeouts": float(metrics.get("timeouts", 0.0)),
        "scope_violations": float(metrics.get("scope_violations", 0.0)),
        "tasks_executed": float(metrics.get("tasks_executed", payload.get("tasks_executed", 0.0))),
    }


def _evaluate(
    *,
    baseline: Dict[str, float],
    candidate: Dict[str, float],
    max_drop_pass_rate: float,
    max_increase_avg_repairs: float,
) -> Tuple[bool, list[str]]:
    reasons: list[str] = []
    pass_drop = baseline["pass_rate"] - candidate["pass_rate"]
    if pass_drop > max_drop_pass_rate:
        reasons.append(
            f"pass_rate dropped by {pass_drop:.4f} (base={baseline['pass_rate']:.4f}, cand={candidate['pass_rate']:.4f})"
        )
    avg_rep_increase = candidate["avg_repairs"] - baseline["avg_repairs"]
    if avg_rep_increase > max_increase_avg_repairs:
        reasons.append(
            f"avg_repairs increased by {avg_rep_increase:.4f} (base={baseline['avg_repairs']:.4f}, cand={candidate['avg_repairs']:.4f})"
        )
    if candidate["timeouts"] > baseline["timeouts"]:
        reasons.append(f"timeouts increased (base={baseline['timeouts']:.0f}, cand={candidate['timeouts']:.0f})")
    if candidate["scope_violations"] > baseline["scope_violations"]:
        reasons.append(
            f"scope_violations increased (base={baseline['scope_violations']:.0f}, cand={candidate['scope_violations']:.0f})"
        )
    return len(reasons) == 0, reasons


def _load_ids_from_tasks(path: str | None) -> set[str]:
    if not path:
        return set()
    p = Path(path)
    if not p.exists():
        return set()
    out: set[str] = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        task_id = row.get("task_id")
        if isinstance(task_id, str) and task_id.strip():
            out.add(task_id.strip())
    return out


def _load_ids_from_training_jsonl(path: str | None) -> set[str]:
    if not path:
        return set()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"training dataset not found: {path}")
    out: set[str] = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        for key in ("task_id", "case_id", "context_id"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                out.add(value.strip())
    return out


def _sha256_file(path: str | Path) -> str | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return sha256(p.read_bytes()).hexdigest()
    except Exception:
        return None


def _load_candidate_metadata(candidate_model: str) -> Dict[str, Any]:
    p = Path(candidate_model)
    for meta_path in (p.with_suffix(".meta.json"), p.parent / "metadata.json"):
        if not meta_path.exists():
            continue
        try:
            row = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(row, dict):
            return row
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="A/B trials for router policy model.")
    parser.add_argument("--candidate-model", default=None, help="Candidate model path (.onnx or .npz).")
    parser.add_argument("--tasks", default=None, help="Optional tasks JSONL for verify.run_aesa_suite.")
    parser.add_argument("--holdout", default=None, help="Optional holdout JSONL for verify.run_aesa_suite.")
    parser.add_argument("--train-jsonl", default=None, help="Training JSONL path for leakage checks.")
    parser.add_argument("--strict-no-leakage", action="store_true", help="Fail when train/eval IDs overlap.")
    parser.add_argument("--out-dir", default="artifacts/router_trials", help="Output directory.")
    parser.add_argument("--seed", type=int, default=42, help="Determinism seed.")
    parser.add_argument("--max-drop-pass-rate", type=float, default=0.0, help="Allowable pass-rate drop.")
    parser.add_argument("--max-increase-avg-repairs", type=float, default=0.0, help="Allowable avg_repairs increase.")
    args = parser.parse_args()

    candidate_model = args.candidate_model or os.getenv("AESA_ROUTER_MODEL")
    if not candidate_model:
        raise SystemExit("candidate model required via --candidate-model or AESA_ROUTER_MODEL")
    if not Path(candidate_model).exists():
        raise SystemExit(f"candidate model does not exist: {candidate_model}")

    if args.strict_no_leakage:
        eval_ids = _load_ids_from_tasks(args.tasks) | _load_ids_from_tasks(args.holdout)
        train_ids = _load_ids_from_training_jsonl(args.train_jsonl)
        overlap = sorted(eval_ids & train_ids)
        if overlap:
            raise SystemExit(f"data leakage detected: {len(overlap)} overlapping ids (sample={overlap[:5]})")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_path = out_dir / "baseline.json"
    cand_path = out_dir / "candidate.json"
    compare_path = out_dir / "compare.json"
    trial_path = out_dir / "trial.json"
    trial_md_path = out_dir / "trial.md"

    baseline_payload = _run_aesa_suite(
        out_path=base_path,
        model_path=None,
        tasks=args.tasks,
        holdout=args.holdout,
        seed=int(args.seed),
    )
    candidate_payload = _run_aesa_suite(
        out_path=cand_path,
        model_path=candidate_model,
        tasks=args.tasks,
        holdout=args.holdout,
        seed=int(args.seed),
    )
    baseline_metrics = _metrics(baseline_payload)
    candidate_metrics = _metrics(candidate_payload)
    deltas = {
        "pass_rate": candidate_metrics["pass_rate"] - baseline_metrics["pass_rate"],
        "avg_repairs": candidate_metrics["avg_repairs"] - baseline_metrics["avg_repairs"],
        "timeouts": candidate_metrics["timeouts"] - baseline_metrics["timeouts"],
        "scope_violations": candidate_metrics["scope_violations"] - baseline_metrics["scope_violations"],
    }
    ok, reasons = _evaluate(
        baseline=baseline_metrics,
        candidate=candidate_metrics,
        max_drop_pass_rate=float(args.max_drop_pass_rate),
        max_increase_avg_repairs=float(args.max_increase_avg_repairs),
    )
    compare = {
        "thresholds": {
            "pass_rate_min_delta": -float(args.max_drop_pass_rate),
            "avg_repairs_max_delta": float(args.max_increase_avg_repairs),
            "timeouts_max_delta": 0.0,
            "scope_violations_max_delta": 0.0,
        },
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
        "delta": deltas,
        "decision": "PASS" if ok else "FAIL",
        "accepted": bool(ok),
        "reasons": reasons,
    }
    compare_path.write_text(json.dumps(compare, indent=2, ensure_ascii=True), encoding="utf-8")

    meta = _load_candidate_metadata(candidate_model)
    model_hash = _sha256_file(candidate_model)
    summary = {
        "accepted": bool(ok),
        "decision": compare["decision"],
        "candidate_model": candidate_model,
        "candidate_model_hash": model_hash,
        "candidate_model_version": meta.get("model_version"),
        "candidate_feature_dim": meta.get("feature_dim"),
        "candidate_feature_schema_version": meta.get("feature_schema_version"),
        "training_jsonl": args.train_jsonl,
        "strict_no_leakage": bool(args.strict_no_leakage),
        "seed": int(args.seed),
        "thresholds": compare["thresholds"],
        "baseline_metrics": baseline_metrics,
        "candidate_metrics": candidate_metrics,
        "delta_metrics": deltas,
        "reasons": reasons,
        "artifacts": {
            "baseline_report": str(base_path),
            "candidate_report": str(cand_path),
            "compare_report": str(compare_path),
        },
    }
    trial_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
    trial_md_path.write_text(
        (
            "# Router Trial\n\n"
            f"- accepted: {summary['accepted']}\n"
            f"- candidate_model: {candidate_model}\n"
            f"- candidate_model_hash: {model_hash}\n"
            f"- candidate_feature_dim: {summary.get('candidate_feature_dim')}\n"
            f"- candidate_feature_schema_version: {summary.get('candidate_feature_schema_version')}\n"
            f"- pass_rate: {baseline_metrics['pass_rate']:.4f} -> {candidate_metrics['pass_rate']:.4f} (delta {deltas['pass_rate']:+.4f})\n"
            f"- avg_repairs: {baseline_metrics['avg_repairs']:.4f} -> {candidate_metrics['avg_repairs']:.4f} (delta {deltas['avg_repairs']:+.4f})\n"
            f"- timeouts: {baseline_metrics['timeouts']:.0f} -> {candidate_metrics['timeouts']:.0f} (delta {deltas['timeouts']:+.0f})\n"
            f"- scope_violations: {baseline_metrics['scope_violations']:.0f} -> {candidate_metrics['scope_violations']:.0f} (delta {deltas['scope_violations']:+.0f})\n"
            f"- reasons: {', '.join(reasons) if reasons else '(none)'}\n"
        ),
        encoding="utf-8",
    )
    print(f"[router-trial] {json.dumps(summary, ensure_ascii=True)}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
