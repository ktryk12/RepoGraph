from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from babyai_shared.ops.killswitch import KillSwitchViolation, get_killswitch_service
from policy.constitution_service import get_constitution_service
from scripts.promotion_gates import (
    atomic_write_json,
    get_promotion_gate_service,
    load_promotion_policy,
    sha256_file,
)
from scripts.promotion_utils import (
    copy_candidate_to_target,
    create_promotion_artifacts,
    publish_latest_pointer,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Promote candidate router model to blessed path.")
    parser.add_argument("--trial", default="artifacts/router_trials/trial.json", help="Trial summary JSON path.")
    parser.add_argument("--candidate-model", default=None, help="Override candidate model path.")
    parser.add_argument("--target-model", default="models/router_policy_v1/model.onnx", help="Blessed model output path.")
    parser.add_argument("--latest-file", default="models/router_policy_v1/LATEST.json", help="LATEST marker file.")
    parser.add_argument("--promotions-log", default="artifacts/router_trials/promotions.jsonl", help="Promotion log JSONL.")
    parser.add_argument("--promotion-policy", default="policy/promotion_policy.yaml", help="Promotion policy YAML path.")
    parser.add_argument("--promotion-report", default=None, help="Promotion report artifact path.")
    parser.add_argument("--canary-level", default="shadow", help="Requested canary level (shadow|advice|active).")
    parser.add_argument("--judge-summary", default=None, help="Override benchmark judge_summary_latest.json.")
    parser.add_argument("--force", action="store_true", help="Promote even if trial was not accepted.")
    args = parser.parse_args()
    constitution = get_constitution_service()
    try:
        get_killswitch_service().require_write(
            operation="scripts.promote_router_model",
            scope="PROMOTE_ACTIVE",
        )
    except KillSwitchViolation as exc:
        print(f"[promote-router] killswitch violation: {exc}")
        return 2

    trial_path = Path(args.trial)
    if not trial_path.exists():
        raise SystemExit(f"trial file not found: {trial_path}")
    trial = _load_json(trial_path)
    policy_path = Path(args.promotion_policy)
    if not policy_path.exists():
        raise SystemExit(f"promotion policy not found: {policy_path}")
    policy = load_promotion_policy(path=policy_path, model_family="router_policy_v1")
    report = get_promotion_gate_service().evaluate(
        trial=trial,
        policy=policy,
        model_family="router_policy_v1",
        requested_canary_level=str(args.canary_level),
        trial_sha256=sha256_file(trial_path),
        policy_sha256=sha256_file(policy_path),
    )
    report_path = Path(args.promotion_report) if args.promotion_report else (trial_path.parent / "promotion_report.json")
    report_payload = dict(report)
    report_payload["forced"] = bool(args.force and not report.get("promotion_allowed"))
    constitution.require("write_path", {"path": report_path})
    atomic_write_json(report_path, report_payload)
    if not args.force and not bool(report.get("promotion_allowed")):
        reasons = ", ".join(str(x) for x in report.get("reasons", [])[:4]) or "no reasons"
        decision = str(report.get("decision") or "blocked")
        raise SystemExit(f"promotion blocked ({decision}): {reasons}")

    candidate = Path(args.candidate_model or trial.get("candidate_model") or "")
    if not candidate.exists():
        raise SystemExit(f"candidate model not found: {candidate}")

    target = Path(args.target_model)
    constitution.require("write_path", {"path": target})
    target.parent.mkdir(parents=True, exist_ok=True)
    raw_model_hash = copy_candidate_to_target(candidate, target)
    _copy_sidecar(candidate, target, ".meta.json")
    _copy_parent_metadata(candidate, target)
    formatted_model_hash = f"sha256:{raw_model_hash}"
    report_hash = sha256_file(report_path)
    policy_sha = sha256_file(policy_path)

    promoted_at = datetime.now(timezone.utc).isoformat()
    promotion_artifacts = create_promotion_artifacts(
        model_family="router_policy_v1",
        model_stage=str(report.get("canary", {}).get("assigned") or args.canary_level),
        model_path=target,
        model_hash=formatted_model_hash,
        trial_sha256=report.get("fingerprints", {}).get("trial_sha256"),
        evaluation_sha256=report.get("fingerprints", {}).get("evaluation_sha256"),
        policy_path=policy_path,
        policy_hash=f"sha256:{policy_sha or ''}",
        report_path=report_path,
        report_sha256=report_hash,
        canary_level=str(report.get("canary", {}).get("assigned") or args.canary_level),
        judge_summary_path=Path(args.judge_summary) if args.judge_summary else None,
    )
    latest = Path(args.latest_file)
    constitution.require("write_path", {"path": latest})
    latest.parent.mkdir(parents=True, exist_ok=True)
    publish_latest_pointer(promotion_artifacts.pointer_path, latest)

    event: Dict[str, Any] = {
        "event_type": "model_promoted",
        "model_family": "router_policy_v1",
        "model_path": target.as_posix(),
        "model_sha256": raw_model_hash,
        "candidate_model": candidate.as_posix(),
        "trial_path": trial_path.as_posix(),
        "accepted": bool(report.get("promotion_allowed")),
        "decision": str(report.get("decision") or "unknown"),
        "canary_level": str(report.get("canary", {}).get("assigned") or args.canary_level),
        "forced": bool(args.force and not report.get("promotion_allowed")),
        "promotion_report_path": report_path.as_posix(),
        "promotion_report_sha256": report_hash,
        "promotion_policy_path": policy_path.as_posix(),
        "promotion_policy_sha256": report.get("fingerprints", {}).get("promotion_policy_sha256"),
        "trial_sha256": report.get("fingerprints", {}).get("trial_sha256"),
        "equivalent_policy_used": bool(report.get("equivalent_policy", {}).get("used")),
        "feature_schema_version": trial.get("candidate_feature_schema_version"),
        "feature_dim": trial.get("candidate_feature_dim"),
        "promoted_at": promoted_at,
    }
    log_path = Path(args.promotions_log)
    constitution.require("write_path", {"path": log_path})
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=True) + "\n")
    print(json.dumps(event, ensure_ascii=True))
    return 0


def _copy_sidecar(src: Path, dst: Path, suffix: str) -> None:
    side = src.with_suffix(suffix)
    if side.exists():
        get_constitution_service().require("write_path", {"path": dst.with_suffix(suffix)})
        shutil.copy2(side, dst.with_suffix(suffix))


def _copy_parent_metadata(src: Path, dst: Path) -> None:
    src_meta = src.parent / "metadata.json"
    if src_meta.exists():
        get_constitution_service().require("write_path", {"path": dst.parent / "metadata.json"})
        shutil.copy2(src_meta, dst.parent / "metadata.json")


def _load_json(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"trial JSON must be object: {path}")
    return data


if __name__ == "__main__":
    raise SystemExit(main())
