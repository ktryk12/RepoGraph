from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

from aesa.experts.ssrn.repair_policy_features import FEATURE_SCHEMA_VERSION, INPUT_DIM
from aesa.experts.ssrn.repair_policy_model import (
    RepairPolicyMiniModel,
    build_examples_from_failure_logs,
)
from babyai_shared.ops.killswitch import KillSwitchViolation, get_killswitch_service
from policy.constitution_service import get_constitution_service
from policy.training_curator_service import TrainingCuratorService, load_jsonl_rows, write_curated_jsonl
from policy.training_judge_gate_service import TrainingJudgeGateService, TrainingJudgeGateViolation
from scripts.export_repair_policy_onnx import export_onnx


def main() -> int:
    parser = argparse.ArgumentParser(description="Train SSRN repair policy mini-model from failures JSONL.")
    parser.add_argument("--jsonl", default="eval/aesa/repair_policy_train.jsonl", help="Input failures JSONL path.")
    parser.add_argument(
        "--out",
        default="models/repair_policy_v1/model.npz",
        help="Output model path (.npz).",
    )
    parser.add_argument("--epochs", type=int, default=12, help="Training epochs.")
    parser.add_argument("--hidden-dim", type=int, default=96, help="Hidden layer width.")
    parser.add_argument("--lr", type=float, default=1e-2, help="Learning rate.")
    parser.add_argument("--l2", type=float, default=1e-5, help="L2 regularization.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument(
        "--onnx-out",
        default=None,
        help="Optional ONNX output path. Default: <out-dir>/model.onnx",
    )
    parser.add_argument("--skip-onnx", action="store_true", help="Skip ONNX export.")
    args = parser.parse_args()
    constitution = get_constitution_service()
    try:
        get_killswitch_service().require_write(
            operation="scripts.train_repair_policy",
            scope="TRAIN_WRITE",
        )
    except KillSwitchViolation as exc:
        print(f"[repair-policy] killswitch violation: {exc}")
        return 2

    try:
        constitution.require("training_dataset", {"dataset_path": args.jsonl})
    except Exception as exc:
        print(f"[repair-policy] constitution violation: {exc}")
        return 2

    out_path = Path(args.out)
    curated_input_path = out_path.parent / "train.curated.jsonl"
    try:
        raw_rows = load_jsonl_rows(args.jsonl)
        curation = TrainingCuratorService(required_fields=("event_type",)).curate_rows(raw_rows)
        judge_verdict = TrainingJudgeGateService().require_accept_only(curation.curated_rows)
    except TrainingJudgeGateViolation as exc:
        print(f"[repair-policy] training judge gate violation: {exc}")
        return 2
    except Exception as exc:
        print(f"[repair-policy] curation/gate error: {exc}")
        return 2

    constitution.require("write_path", {"path": curated_input_path})
    write_curated_jsonl(curated_input_path, curation.curated_rows)
    examples = build_examples_from_failure_logs(curated_input_path)
    if not examples:
        print(f"[repair-policy] no training examples found in {args.jsonl}")
        return 2

    model = RepairPolicyMiniModel(
        hidden_dim=args.hidden_dim,
        learning_rate=args.lr,
        l2=args.l2,
        seed=args.seed,
    )
    stats = model.train(examples, epochs=args.epochs)

    constitution.require("write_path", {"path": out_path})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(out_path)
    out_hash = _sha256_file(out_path)

    onnx_out = Path(args.onnx_out) if args.onnx_out else out_path.with_name("model.onnx")
    constitution.require("write_path", {"path": onnx_out})
    onnx_hash = None
    onnx_exported = False
    onnx_error = None
    if not args.skip_onnx:
        try:
            export_onnx(npz_path=out_path, onnx_path=onnx_out)
            onnx_exported = True
            onnx_hash = _sha256_file(onnx_out)
        except Exception as exc:
            onnx_error = f"{type(exc).__name__}: {exc}"

    meta = {
        "model_version": model.version,
        "feature_dim": int(INPUT_DIM),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "examples": len(examples),
        "train": stats,
        "input_jsonl": str(args.jsonl),
        "curated_input_jsonl": str(curated_input_path),
        "dataset_fingerprint": curation.dataset_fingerprint,
        "curation": curation.to_dict(),
        "training_judge_gate": judge_verdict.to_dict(),
        "model_path": str(out_path),
        "model_sha256": out_hash,
        "onnx_path": str(onnx_out) if onnx_exported else None,
        "onnx_sha256": onnx_hash,
        "onnx_exported": bool(onnx_exported),
        "onnx_error": onnx_error,
    }
    metadata_path = out_path.parent / "metadata.json"
    npz_meta_path = out_path.with_suffix(".meta.json")
    metadata_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")
    npz_meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")
    if onnx_exported:
        onnx_meta_path = onnx_out.with_suffix(".meta.json")
        onnx_meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8")

    print(f"[repair-policy] trained examples={len(examples)} loss={stats.get('loss', 0.0):.4f}")
    print(f"[repair-policy] model={out_path}")
    if onnx_exported:
        print(f"[repair-policy] onnx={onnx_out}")
    elif onnx_error:
        print(f"[repair-policy] onnx_export_skipped error={onnx_error}")
        return 2
    print(f"[repair-policy] metadata={metadata_path}")
    return 0


def _sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
