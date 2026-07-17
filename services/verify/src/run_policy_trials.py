from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Tuple

from scripts.compare_benchmark import compare


DEFAULT_BASE_RULES = "policy/policy_rules.yaml"


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def _merge_candidate_rules(base_rules: Dict[str, Any], candidate_payload: Dict[str, Any]) -> Dict[str, Any]:
    merged = deepcopy(base_rules)
    overrides = candidate_payload.get("policy_overrides")
    if isinstance(overrides, dict):
        merged = _deep_merge(merged, overrides)
    rules = candidate_payload.get("rules")
    if isinstance(rules, list):
        merged["autogen_rules"] = rules
    return merged


def _run_benchmark(
    *,
    tasks_dir: str,
    split: str | None,
    generator: str,
    out_path: Path,
    log_path: Path,
    rules_path: str,
) -> int:
    env = os.environ.copy()
    env["POLICY_RULES_PATH"] = rules_path
    cmd = [
        "python",
        "-m",
        "verify.run_ci_benchmark",
        "--tasks-dir",
        tasks_dir,
        "--generator",
        generator,
        "--out",
        str(out_path),
        "--log-path",
        str(log_path),
        "--no-print-json-line",
    ]
    if split:
        cmd.extend(["--split", split])
    return subprocess.run(cmd, check=False, env=env).returncode


def _evaluate(
    base: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    max_drop_pass_rate: float,
    max_increase_avg_repairs: float,
) -> Tuple[bool, List[str], bool]:
    ok, reasons = compare(
        candidate,
        base,
        max_drop_pass_rate=max_drop_pass_rate,
        max_increase_avg_repairs=max_increase_avg_repairs,
    )

    cur = candidate.get("metrics", {}) if isinstance(candidate, dict) else {}
    base_metrics = base.get("metrics", {}) if isinstance(base, dict) else {}
    improved = False
    try:
        cur_pr = float(cur.get("pass_rate_with_repair", 0.0))
        base_pr = float(base_metrics.get("pass_rate_with_repair", 0.0))
        cur_avg = float(cur.get("avg_repairs_used", 0.0))
        base_avg = float(base_metrics.get("avg_repairs_used", 0.0))
        if cur_pr > base_pr or cur_avg < base_avg:
            improved = True
    except Exception:
        improved = False

    return ok, reasons, improved


def main() -> int:
    p = argparse.ArgumentParser(description="Run policy trials (base vs candidate).")
    p.add_argument("--candidate", required=True, help="Path to policy proposal file (JSON/YAML).")
    p.add_argument("--tasks-dir", default="eval/tasks", help="Directory with eval task JSON files.")
    p.add_argument("--split", default=None, help="Optional split file of task ids.")
    p.add_argument(
        "--generator",
        default="ml.hybrid_generator:generate_decision",
        help="Callable to use, format 'module:function'.",
    )
    p.add_argument("--base-rules", default=DEFAULT_BASE_RULES, help="Base policy rules file.")
    p.add_argument("--base-out", default="artifacts/policy_trials/base_score.json", help="Base score output.")
    p.add_argument(
        "--candidate-out",
        default="artifacts/policy_trials/candidate_score.json",
        help="Candidate score output.",
    )
    p.add_argument(
        "--trial-out",
        default="artifacts/policy_trials/latest_trial.json",
        help="Trial summary output path.",
    )
    p.add_argument("--log-base", default="logs/policy_trials_base.jsonl", help="Base telemetry log.")
    p.add_argument("--log-candidate", default="logs/policy_trials_candidate.jsonl", help="Candidate telemetry log.")
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
        "--enabled-dir",
        default="policy/rules_enabled",
        help="Write accepted candidates here (repo-visible).",
    )
    args = p.parse_args()

    candidate_path = Path(args.candidate)
    candidate_payload = _load_json(candidate_path)
    base_rules = _load_json(Path(args.base_rules))

    merged_rules = _merge_candidate_rules(base_rules, candidate_payload)
    merged_path = Path(args.candidate_out).with_suffix(".merged_rules.json")
    _write_json(merged_path, merged_rules)

    base_out = Path(args.base_out)
    cand_out = Path(args.candidate_out)
    log_base = Path(args.log_base)
    log_candidate = Path(args.log_candidate)

    base_rc = _run_benchmark(
        tasks_dir=args.tasks_dir,
        split=args.split,
        generator=args.generator,
        out_path=base_out,
        log_path=log_base,
        rules_path=args.base_rules,
    )
    cand_rc = _run_benchmark(
        tasks_dir=args.tasks_dir,
        split=args.split,
        generator=args.generator,
        out_path=cand_out,
        log_path=log_candidate,
        rules_path=str(merged_path),
    )

    base_score = _load_json(base_out)
    cand_score = _load_json(cand_out)

    ok, reasons, improved = _evaluate(
        base_score,
        cand_score,
        max_drop_pass_rate=args.max_drop_pass_rate,
        max_increase_avg_repairs=args.max_increase_avg_repairs,
    )

    accepted = ok and improved
    enabled_path = None
    if accepted:
        enabled_dir = Path(args.enabled_dir)
        enabled_dir.mkdir(parents=True, exist_ok=True)
        enabled_path = enabled_dir / candidate_path.name
        shutil.copy(candidate_path, enabled_path)

    summary = {
        "base_rc": base_rc,
        "candidate_rc": cand_rc,
        "accepted": accepted,
        "compare_ok": ok,
        "improved": improved,
        "reasons": reasons,
        "base_score": str(base_out),
        "candidate_score": str(cand_out),
        "candidate_rules": str(merged_path),
        "enabled_path": str(enabled_path) if enabled_path else None,
    }
    _write_json(Path(args.trial_out), summary)

    print(f"[policy-trial] result: {json.dumps(summary, ensure_ascii=True)}")
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
