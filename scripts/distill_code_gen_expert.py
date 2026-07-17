from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from aesa.experts.ssrn.distillation import DistillationPipeline, load_jsonl_rows, rows_from_telemetry, synthetic_rows
from babyai_shared.ops.killswitch import KillSwitchViolation, get_killswitch_service
from policy.constitution_service import get_constitution_service


def main() -> int:
    return main_with_defaults(
        expert_id="code_gen",
        default_out_dir="models/ssrn/code_gen/distill",
    )


def main_with_defaults(*, expert_id: str, default_out_dir: str) -> int:
    parser = argparse.ArgumentParser(description="Build verified SSRN distillation dataset for code_gen expert.")
    parser.add_argument("--expert", default=expert_id, help="Expert id for metadata/provenance.")
    parser.add_argument("--tasks", default=None, help="JSONL tasks/cases input path.")
    parser.add_argument("--telemetry", default=None, help="JSONL telemetry input path.")
    parser.add_argument("--synthetic", action="store_true", help="Include deterministic synthetic samples.")
    parser.add_argument("--synthetic-count", type=int, default=16, help="Synthetic sample count.")
    parser.add_argument("--out-dir", default=default_out_dir, help="Output dataset directory.")
    parser.add_argument("--repo-root", default=".", help="Repository root used by verification tools.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic seed.")
    parser.add_argument("--eval-fraction", type=float, default=0.2, help="Eval split fraction in (0,1).")
    parser.add_argument("--teacher", choices=("mock", "live"), default=None, help="Teacher mode override.")
    parser.add_argument(
        "--teacher-callable",
        default="ml.generator:generate_decision",
        help="Live teacher callable in module:function format.",
    )
    parser.add_argument("--max-tasks", type=int, default=None, help="Optional cap on processed rows.")
    parser.add_argument("--no-verify", action="store_true", help="Skip tool-runtime gate verification.")
    args = parser.parse_args()
    try:
        get_killswitch_service().require_write(
            operation="scripts.distill_code_gen_expert",
            scope="TRAIN_WRITE",
        )
    except KillSwitchViolation as exc:
        print(f"[distill-code-gen] killswitch violation: {exc}")
        return 2

    rows = _collect_rows(
        tasks_path=args.tasks,
        telemetry_path=args.telemetry,
        include_synthetic=bool(args.synthetic),
        synthetic_count=int(args.synthetic_count),
        seed=int(args.seed),
    )
    if not rows:
        raise SystemExit("no input rows found; provide --tasks and/or --telemetry or --synthetic")

    pipeline = DistillationPipeline(
        out_dir=args.out_dir,
        repo_root=args.repo_root,
        seed=int(args.seed),
        eval_fraction=float(args.eval_fraction),
        teacher_mode=args.teacher,
        teacher_callable=args.teacher_callable,
        verify_enabled=not bool(args.no_verify),
        expert_id=str(args.expert).strip() or expert_id,
    )
    constitution = get_constitution_service()
    constitution.require("write_path", {"path": Path(args.out_dir)})
    artifacts = pipeline.distill(rows, max_rows=args.max_tasks)

    payload: Dict[str, Any] = {
        "out_dir": Path(args.out_dir).as_posix(),
        "rows_input": len(rows),
        "rows_processed": artifacts.stats.get("total_rows"),
        "train_path": artifacts.train_path,
        "eval_path": artifacts.eval_path,
        "verified_path": artifacts.verified_path,
        "rejected_path": artifacts.rejected_path,
        "feature_keys_path": artifacts.feature_keys_path,
        "stats_path": artifacts.stats_path,
        "teacher_mode": artifacts.stats.get("teacher_mode"),
        "label_distribution": artifacts.stats.get("label_distribution"),
        "outcome_distribution": artifacts.stats.get("outcome_distribution"),
    }
    manifest_path = Path(args.out_dir) / "distillation_manifest.json"
    constitution.require("write_path", {"path": manifest_path})
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=True))
    return 0


def _collect_rows(
    *,
    tasks_path: str | None,
    telemetry_path: str | None,
    include_synthetic: bool,
    synthetic_count: int,
    seed: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(tasks_path, str) and tasks_path.strip():
        rows.extend(load_jsonl_rows(tasks_path.strip()))
    if isinstance(telemetry_path, str) and telemetry_path.strip():
        events = load_jsonl_rows(telemetry_path.strip())
        rows.extend(rows_from_telemetry(events))
    if include_synthetic:
        rows.extend(synthetic_rows(count=max(1, int(synthetic_count)), seed=int(seed)))
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
