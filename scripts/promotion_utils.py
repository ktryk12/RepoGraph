from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence
import json
import shutil

from babyai_shared.fingerprint import sha256_file, sha256_json
from policy.promotion_gate import can_promote
from policy.promotion_record import build_promotion_record, compute_policy_hash
from verify.artifacts.writer import write_artifact


DEFAULT_ARTIFACT_DIR = Path("artifacts") / "promotions"


@dataclass(frozen=True)
class PromotionArtifacts:
    model_record: Dict[str, Any]
    promotion_record: Dict[str, Any]
    pointer_record: Dict[str, Any]
    pointer_path: Path


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_canary_level(value: str | None) -> int:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return 0
    if normalized in {"stable", "shadow", "0"}:
        return 0
    if normalized in {"advice", "1"}:
        return 1
    if normalized in {"active", "2"}:
        return 2
    try:
        return max(0, int(normalized))
    except Exception:
        return 1


def _load_judge_summary(path: Path | None) -> dict | None:
    if not path:
        return None
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _register_artifact(
    *,
    payload: Mapping[str, Any],
    artifact_type: str,
    output_path: Path,
    metadata: Optional[Mapping[str, Any]] = None,
    registry_manifest: Path | None = None,
) -> Dict[str, Any]:
    return write_artifact(
        artifact_type=artifact_type,
        payload=payload,
        output_path=output_path,
        metadata=metadata,
        registry_manifest=registry_manifest,
        gate_operation=f"promotion:{artifact_type}",
    )


def copy_candidate_to_target(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return sha256_file(target)


def publish_latest_pointer(pointer_path: Path, latest_path: Path) -> None:
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    latest_path.write_text(pointer_path.read_text(encoding="utf-8"), encoding="utf-8")


def create_promotion_artifacts(
    *,
    model_family: str,
    model_stage: str,
    model_path: Path,
    model_hash: str,
    trial_sha256: str | None,
    evaluation_sha256: str | None,
    evaluation_refs: Sequence[str] | None = None,
    policy_path: Path,
    policy_hash: str,
    report_path: Path,
    report_sha256: str | None,
    canary_level: str | None,
    judge_summary_path: Path | None = None,
    registry_manifest: Path | None = None,
    artifact_root: Path | None = None,
) -> PromotionArtifacts:
    artifact_root = artifact_root or DEFAULT_ARTIFACT_DIR
    judge_summary_path = judge_summary_path or Path("artifacts/benchmark/judge_summary_latest.json")
    summary = _load_judge_summary(judge_summary_path)
    canary_level_int = _parse_canary_level(canary_level)
    mode = "canary" if canary_level_int > 0 else "stable"
    if summary is None:
        default_reason = "PROMOTION_ALLOWED_CANARY" if mode == "canary" else "PROMOTION_ALLOWED_STABLE"
        decision = can_promote(
            {
                "schema_version": 1,
                "kind": "JudgeSummary",
                "totals": {"total": 0, "by_verdict": {"PASS": 0, "FAIL": 0, "UNKNOWN": 0}},
                "reasons": {"top": [], "by_severity": {"info": 0, "warning": 0, "error": 0}},
                "notes": {"fallback": "summary_missing"},
            },
            mode=mode,
            canary_level=canary_level_int,
        )
        if decision.allowed and not decision.reasons:
            decision = type(decision)(allowed=True, reasons=[default_reason])
    else:
        decision = can_promote(summary, mode=mode, canary_level=canary_level_int)
    if not decision.allowed:
        raise RuntimeError(f"promotion blocked: {decision.reasons}")
    now = _timestamp()
    pointer_dir = artifact_root / model_family
    pointer_filename = "canary_pointer.json" if mode == "canary" else "latest_pointer.json"
    pointer_path = pointer_dir / pointer_filename
    model_record_payload = {
        "schema_version": 1,
        "model_family": model_family,
        "model_path": str(model_path),
        "model_hash": model_hash,
        "stage": model_stage,
    }
    safe_hash = model_hash.replace(":", "_")
    model_record_path = pointer_dir / f"model_record_{safe_hash[:12]}.json"
    model_record = _register_artifact(
        payload=model_record_payload,
        artifact_type="promotion_model",
        output_path=model_record_path,
        metadata={"timestamp": now},
        registry_manifest=registry_manifest,
    )

    resolved_policy_hash = str(policy_hash or "").strip() or compute_policy_hash(policy_path)
    summary_fingerprint = sha256_json(summary or {})
    promotion_record_payload = build_promotion_record(
        family=model_family,
        mode=mode,
        canary_level=canary_level_int,
        promoted_model_fingerprint=model_hash,
        judge_summary_fingerprint=summary_fingerprint,
        gate_reasons=decision.reasons,
        policy_hash=resolved_policy_hash,
        evaluation_refs=evaluation_refs,
        notes={
            "model_stage": model_stage,
            "report_path": str(report_path),
        },
    )
    promotion_record_path = pointer_dir / f"promotion_record_{safe_hash[:8]}_{now.replace(':', '')}.json"
    promotion_record = _register_artifact(
        payload=promotion_record_payload,
        artifact_type="promotion_record",
        output_path=promotion_record_path,
        metadata={"timestamp": now},
        registry_manifest=registry_manifest,
    )
    pointer_payload = {
        "schema_version": 1,
        "kind": "promotion_pointer",
        "name": f"{model_family}:{model_stage}",
        "model_family": model_family,
        "stage": model_stage,
        "canary_level": str(canary_level or model_stage),
        "model_path": str(model_path),
        "model_hash": model_hash,
        "target_fingerprint": model_hash,
        "promotion_record_fingerprint": promotion_record["fingerprint"],
        "policy_hash": resolved_policy_hash,
        "evaluation_refs": list(evaluation_refs or []),
        "model_record_path": str(model_record_path),
        "report_path": str(report_path),
        "promotion_report_sha256": str(report_sha256 or ""),
    }
    pointer_record = _register_artifact(
        payload=pointer_payload,
        artifact_type="promotion_pointer",
        output_path=pointer_path,
        metadata={"timestamp": now},
        registry_manifest=registry_manifest,
    )

    return PromotionArtifacts(
        model_record=model_record,
        promotion_record=promotion_record,
        pointer_record=pointer_record,
        pointer_path=pointer_path,
    )
