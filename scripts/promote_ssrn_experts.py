from __future__ import annotations

import argparse
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from aesa.experts.ssrn.model_registry import (
    DEFAULT_PIN_NAME,
    MODEL_REGISTRY_FILENAME,
    upsert_registry_entry,
)
from babyai_shared.ops.killswitch import KillSwitchViolation, get_killswitch_service
from policy.constitution_service import get_constitution_service
from verify.artifacts.registry import write_artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Promote SSRN expert model artifacts with latest.json pointer.")
    parser.add_argument("--expert", default="repair_hint_ssrn", help="Expert name under models/production.")
    parser.add_argument("--version", default="v1.0.0", help="Version tag to promote (e.g. v1.0.0).")
    parser.add_argument(
        "--source-dir",
        default="models/ssrn/repair_hint/dev",
        help="Source directory containing trained artifacts to promote.",
    )
    parser.add_argument("--models-root", default="models/production", help="Root production models directory.")
    parser.add_argument("--model-filename", default="model.onnx", help="Model file expected under the promoted version.")
    parser.add_argument(
        "--latest-file",
        default=None,
        help="Optional explicit latest.json path. Default: <models-root>/<expert>/latest.json",
    )
    parser.add_argument(
        "--registry-file",
        default=None,
        help=(
            "Optional explicit model registry path. "
            "Default: <models-root>/<expert>/model_registry.json"
        ),
    )
    parser.add_argument(
        "--pin-name",
        default=DEFAULT_PIN_NAME,
        help="Registry pin name to update (e.g. production, staging).",
    )
    parser.add_argument(
        "--promotions-log",
        default="artifacts/ssrn_experts/promotions.jsonl",
        help="Promotion log JSONL path.",
    )
    parser.add_argument(
        "--promoted-at",
        default=None,
        help="Optional deterministic promoted_at override (ISO-8601).",
    )
    parser.add_argument("--trial", default=None, help="Optional trial summary JSON with accepted=true check.")
    parser.add_argument("--force", action="store_true", help="Promote even when trial.accepted=false.")
    args = parser.parse_args(argv)
    constitution = get_constitution_service()
    try:
        get_killswitch_service().require_write(
            operation="scripts.promote_ssrn_experts",
            scope="PROMOTE_ACTIVE",
        )
    except KillSwitchViolation as exc:
        print(f"[promote-ssrn] killswitch violation: {exc}")
        return 2

    if args.trial:
        trial = _load_json(Path(args.trial))
        accepted = bool(trial.get("accepted"))
        decision = str(trial.get("decision") or "")
        if not args.force and not accepted:
            raise SystemExit(f"trial not accepted (decision={decision or 'UNKNOWN'})")

    source_dir = Path(args.source_dir)
    if not source_dir.exists() or not source_dir.is_dir():
        raise SystemExit(f"source directory not found: {source_dir}")

    expert = str(args.expert).strip()
    version = str(args.version).strip()
    if not expert:
        raise SystemExit("expert must be non-empty")
    if not version:
        raise SystemExit("version must be non-empty")

    target_dir = Path(args.models_root) / expert / version
    constitution.require("write_path", {"path": target_dir})
    target_dir.mkdir(parents=True, exist_ok=True)
    _copy_tree_contents(source_dir, target_dir)
    model_filename = str(args.model_filename).strip()
    if not model_filename:
        raise SystemExit("model-filename must be non-empty")
    promoted_model = target_dir / model_filename
    if not promoted_model.exists():
        raise SystemExit(f"promoted model file not found: {promoted_model}")

    latest_path = (
        Path(args.latest_file)
        if isinstance(args.latest_file, str) and args.latest_file.strip()
        else Path(args.models_root) / expert / "latest.json"
    )
    constitution.require("write_path", {"path": latest_path})
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(latest_path, {"version": version})

    registry_path = (
        Path(args.registry_file)
        if isinstance(args.registry_file, str) and args.registry_file.strip()
        else Path(args.models_root) / expert / MODEL_REGISTRY_FILENAME
    )
    constitution.require("write_path", {"path": registry_path})
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    model_relative_path = f"{version}/{model_filename}"
    promoted_at = _resolve_promoted_at(
        registry_path=registry_path,
        version=version,
        model_filename=model_filename,
        model_relative_path=model_relative_path,
        source_dir=source_dir.as_posix(),
        explicit_promoted_at=(str(args.promoted_at).strip() if args.promoted_at is not None else None),
    )
    registry_payload = upsert_registry_entry(
        registry_path=registry_path,
        expert_name=expert,
        version=version,
        model_filename=model_filename,
        model_relative_path=model_relative_path,
        promoted_at=promoted_at,
        source_dir=source_dir.as_posix(),
        pin_name=str(args.pin_name or DEFAULT_PIN_NAME),
    )
    _atomic_write_json(registry_path, registry_payload)

    event: Dict[str, Any] = {
        "event_type": "ssrn_model_promoted",
        "expert": expert,
        "version": version,
        "source_dir": source_dir.as_posix(),
        "target_dir": target_dir.as_posix(),
        "model_file": promoted_model.as_posix(),
        "latest_file": latest_path.as_posix(),
        "registry_file": registry_path.as_posix(),
        "pin_name": str(args.pin_name or DEFAULT_PIN_NAME),
        "promoted_at": promoted_at,
    }
    log_path = Path(args.promotions_log)
    constitution.require("write_path", {"path": log_path})
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=True) + "\n")

    print(json.dumps(event, ensure_ascii=True))
    return 0


def _copy_tree_contents(source: Path, target: Path) -> None:
    constitution = get_constitution_service()
    for src_item in sorted(source.rglob("*"), key=lambda p: p.as_posix()):
        rel = src_item.relative_to(source)
        dst_item = target / rel
        if src_item.is_dir():
            constitution.require("write_path", {"path": dst_item})
            dst_item.mkdir(parents=True, exist_ok=True)
            continue
        constitution.require("write_path", {"path": dst_item})
        dst_item.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_item, dst_item)


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"trial file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"trial JSON must be object: {path}")
    return payload


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    constitution = get_constitution_service()
    write_artifact(
        "ssrn_promotion_json",
        dict(payload),
        path,
        metadata={
            "source_ref": "scripts.promote_ssrn_experts",
            "constitution_version": constitution.state.version,
        },
    )


def _resolve_promoted_at(
    *,
    registry_path: Path,
    version: str,
    model_filename: str,
    model_relative_path: str,
    source_dir: str | None,
    explicit_promoted_at: str | None,
) -> str:
    explicit = str(explicit_promoted_at or "").strip()
    if explicit:
        return explicit

    existing = _existing_registry_version(
        registry_path=registry_path,
        version=version,
    )
    if existing is not None:
        existing_model_filename = str(existing.get("model_filename") or "").strip()
        existing_rel_path = str(existing.get("relative_model_path") or existing.get("model_path") or "").strip()
        existing_source_dir = str(existing.get("source_dir") or "").strip()
        if (
            existing_model_filename == str(model_filename).strip()
            and existing_rel_path == str(model_relative_path).strip()
            and existing_source_dir == str(source_dir or "").strip()
        ):
            previous_promoted_at = str(existing.get("promoted_at") or "").strip()
            if previous_promoted_at:
                return previous_promoted_at

    return datetime.now(timezone.utc).isoformat()


def _existing_registry_version(*, registry_path: Path, version: str) -> Mapping[str, Any] | None:
    if not registry_path.exists():
        return None
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    versions = payload.get("versions")
    if not isinstance(versions, dict):
        return None
    row = versions.get(str(version))
    if isinstance(row, dict):
        return row
    return None


if __name__ == "__main__":
    raise SystemExit(main())
