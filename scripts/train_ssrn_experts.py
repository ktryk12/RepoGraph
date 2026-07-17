from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from aesa.experts.ssrn.mini_model_trainer import SSRNMiniModelTrainer
from aesa.utils.seed import set_global_seed
from babyai_shared.ops.killswitch import KillSwitchViolation, get_killswitch_service
from policy.constitution_service import get_constitution_service
from policy.training_curator_service import TrainingCuratorService, load_jsonl_rows
from policy.training_judge_gate_service import TrainingJudgeGateService, TrainingJudgeGateViolation
from babyai_shared.provenance.store import ProvenanceStore
from babyai_shared.storage.artifact_store import FileArtifactStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Train SSRN action-selection mini-model.")
    parser.add_argument("--jsonl", default="eval/aesa/ssrn_train.jsonl", help="Training dataset JSONL path.")
    parser.add_argument(
        "--train-jsonl",
        default=None,
        help="Explicit training dataset JSONL path. Overrides --jsonl when provided.",
    )
    parser.add_argument(
        "--eval-jsonl",
        default=None,
        help="Optional explicit eval dataset JSONL path.",
    )
    parser.add_argument("--out-dir", default="models/ssrn/repair_hint/dev", help="Output directory for model artifacts.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic seed.")
    parser.add_argument("--val-fraction", type=float, default=0.2, help="Validation split fraction in (0,1).")
    parser.add_argument(
        "--feature-keys",
        default=None,
        help="Comma-separated feature keys in stable order.",
    )
    parser.add_argument(
        "--feature-keys-file",
        default=None,
        help="Path to JSON file with feature_keys list or {feature_keys:[...]}",
    )
    parser.add_argument("--run-id", default=None, help="Optional stable tool_run id for provenance.")
    parser.add_argument("--artifact-root", default="artifacts", help="Artifact store root.")
    parser.add_argument("--provenance-db", default="provenance/provenance.sqlite", help="Provenance sqlite path.")
    args = parser.parse_args()
    constitution = get_constitution_service()
    try:
        get_killswitch_service().require_write(
            operation="scripts.train_ssrn_experts",
            scope="TRAIN_WRITE",
        )
    except KillSwitchViolation as exc:
        print(f"[ssrn-train] killswitch violation: {exc}")
        return 2

    train_dataset = args.train_jsonl if isinstance(args.train_jsonl, str) and args.train_jsonl.strip() else args.jsonl
    eval_dataset = args.eval_jsonl if isinstance(args.eval_jsonl, str) and args.eval_jsonl.strip() else None
    try:
        constitution.require("training_dataset", {"dataset_path": train_dataset})
        if eval_dataset:
            constitution.require("training_dataset", {"dataset_path": eval_dataset})
    except Exception as exc:
        print(f"[ssrn-train] constitution violation: {exc}")
        return 2

    seed_value = int(args.seed)
    seed_report = set_global_seed(seed_value)
    feature_keys = _resolve_feature_keys(args.feature_keys, args.feature_keys_file)
    curator = TrainingCuratorService(required_fields=("features", "label"))
    judge_gate = TrainingJudgeGateService()
    try:
        train_rows = load_jsonl_rows(train_dataset)
        train_curation = curator.curate_rows(train_rows)
        train_judge_verdict = judge_gate.require_accept_only(train_curation.curated_rows)
        eval_curation = None
        eval_judge_verdict = None
        if eval_dataset:
            eval_rows = load_jsonl_rows(eval_dataset)
            eval_curation = curator.curate_rows(eval_rows)
            eval_judge_verdict = judge_gate.require_accept_only(eval_curation.curated_rows)
    except TrainingJudgeGateViolation as exc:
        print(f"[ssrn-train] training judge gate violation: {exc}")
        return 2
    except Exception as exc:
        print(f"[ssrn-train] curation/gate error: {exc}")
        return 2

    trainer = SSRNMiniModelTrainer(
        feature_keys=feature_keys,
        seed=seed_value,
        val_fraction=float(args.val_fraction),
    )
    constitution.require("write_path", {"path": Path(args.out_dir)})
    artifacts = trainer.train(
        train_curation.curated_rows,
        out_dir=args.out_dir,
        eval_rows=(eval_curation.curated_rows if eval_curation is not None else None),
    )

    metrics = artifacts.metrics
    run_id = (
        str(args.run_id).strip()
        if isinstance(args.run_id, str) and args.run_id.strip()
        else f"ssrn_train_{metrics['dataset_sha256'][:12]}_{int(metrics['seed'])}"
    )
    decision_id = f"ssrn_training:{run_id}"

    store = FileArtifactStore(root=args.artifact_root)
    prov = ProvenanceStore(path=args.provenance_db)

    prov.append_edge(
        src_type="decision",
        src_id=decision_id,
        dst_type="tool_run",
        dst_id=run_id,
        meta={
            "dataset_sha256": metrics.get("dataset_sha256"),
            "seed": metrics.get("seed"),
            "feature_keys_sha256": metrics.get("feature_keys_sha256"),
            "code_version": metrics.get("code_version"),
            "seed_report": seed_report,
        },
    )

    refs = _store_training_artifacts(
        store=store,
        prov=prov,
        run_id=run_id,
        metrics=metrics,
        model_path=Path(artifacts.model_path),
        metrics_path=Path(artifacts.metrics_path),
        feature_keys_path=Path(artifacts.feature_keys_path),
    )

    bundle = {
        "run_id": run_id,
        "decision_id": decision_id,
        "dataset_sha256": metrics.get("dataset_sha256"),
        "train_dataset_sha256": metrics.get("train_dataset_sha256"),
        "eval_dataset_sha256": metrics.get("eval_dataset_sha256"),
        "seed": metrics.get("seed"),
        "seed_report": seed_report,
        "feature_keys_sha256": metrics.get("feature_keys_sha256"),
        "split_mode": metrics.get("split_mode"),
        "artifacts": refs,
        "local_paths": {
            "model": artifacts.model_path,
            "metrics": artifacts.metrics_path,
            "feature_keys": artifacts.feature_keys_path,
        },
        "input_paths": {
            "train_jsonl": str(train_dataset),
            "eval_jsonl": str(eval_dataset) if eval_dataset else None,
        },
        "curation": {
            "train": train_curation.to_dict(),
            "eval": (eval_curation.to_dict() if eval_curation is not None else None),
        },
        "training_judge_gate": {
            "train": train_judge_verdict.to_dict(),
            "eval": (eval_judge_verdict.to_dict() if eval_judge_verdict is not None else None),
        },
    }
    bundle_path = Path(args.out_dir) / "artifact_bundle.json"
    constitution.require("write_path", {"path": bundle_path})
    bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps(bundle, ensure_ascii=True))
    return 0


def _resolve_feature_keys(raw: str | None, path: str | None) -> List[str]:
    if isinstance(path, str) and path.strip():
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            loaded = loaded.get("feature_keys")
        if not isinstance(loaded, list):
            raise SystemExit(f"feature-keys-file must contain list or {{feature_keys:[...]}}: {path}")
        keys = [str(k).strip() for k in loaded if str(k).strip()]
    else:
        keys = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    if not keys:
        raise SystemExit("feature keys are required (--feature-keys or --feature-keys-file)")
    return keys


def _store_training_artifacts(
    *,
    store: FileArtifactStore,
    prov: ProvenanceStore,
    run_id: str,
    metrics: Dict[str, Any],
    model_path: Path,
    metrics_path: Path,
    feature_keys_path: Path,
) -> Dict[str, str]:
    refs: Dict[str, str] = {}
    common_meta = {
        "dataset_sha256": metrics.get("dataset_sha256"),
        "seed": metrics.get("seed"),
        "seed_report": metrics.get("seed_report"),
        "feature_keys_sha256": metrics.get("feature_keys_sha256"),
        "code_version": metrics.get("code_version"),
    }

    for name, path in (
        ("model", model_path),
        ("metrics", metrics_path),
        ("feature_keys", feature_keys_path),
    ):
        data = path.read_bytes()
        artifact = store.put(
            data,
            context_id=run_id,
            name=f"ssrn/{name}",
            metadata={"file_path": path.as_posix(), "artifact_kind": name, **common_meta},
        )
        refs[name] = artifact.ref
        prov.append_edge(
            src_type="tool_run",
            src_id=run_id,
            dst_type="artifact",
            dst_id=artifact.ref,
            meta={"file_path": path.as_posix(), "artifact_kind": name, **common_meta},
        )
    return refs


if __name__ == "__main__":
    raise SystemExit(main())
