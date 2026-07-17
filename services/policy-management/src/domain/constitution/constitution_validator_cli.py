from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml

from policy.constitution_service import ConstitutionService


REQUIRED_RULES = {
    "no_unapproved_training_data",
    "decision_requires_provenance",
    "no_self_modification_without_human_approval",
    "artifact_fingerprint_required",
    "stagnation_must_terminate",
}


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: List[str]
    summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "errors": list(self.errors),
            "summary": dict(self.summary),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate constitution file and diff versions.")
    parser.add_argument("--current", default="policy/constitution.yaml", help="Current constitution YAML.")
    parser.add_argument("--previous", default=None, help="Optional previous constitution YAML for diff.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    args = parser.parse_args()

    current_path = Path(args.current)
    previous_path = Path(args.previous) if isinstance(args.previous, str) and args.previous.strip() else None

    result = validate_constitution_file(current_path)
    diff_payload = diff_constitutions(previous_path, current_path) if previous_path else None

    payload: Dict[str, Any] = {
        "validation": result.to_dict(),
        "diff": diff_payload,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        _print_human(payload)

    if not result.ok:
        return 2
    return 0


def validate_constitution_file(path: Path) -> ValidationResult:
    errors: List[str] = []
    data = _load_yaml_dict(path)

    schema_version = data.get("schema_version")
    if not isinstance(schema_version, int) or schema_version < 1:
        errors.append("schema_version must be integer >= 1")

    version = str(data.get("version") or "").strip()
    if not version:
        errors.append("version is required")

    effective_from = str(data.get("effective_from") or "").strip()
    if not effective_from:
        errors.append("effective_from is required")
    else:
        if _parse_iso(effective_from) is None:
            errors.append("effective_from must be ISO-8601 UTC timestamp")

    updated_at = str(data.get("updated_at") or "").strip()
    if not updated_at:
        errors.append("updated_at is required")
    else:
        if _parse_iso(updated_at) is None:
            errors.append("updated_at must be ISO-8601 UTC timestamp")

    rules = data.get("rules")
    if not isinstance(rules, dict):
        errors.append("rules must be a mapping")
        rules = {}

    missing_rules = sorted(REQUIRED_RULES - set(rules.keys()))
    if missing_rules:
        errors.append(f"missing required rules: {missing_rules}")

    try:
        service = ConstitutionService(path=path)
        summary = {
            "path": path.as_posix(),
            "constitution_version": service.state.version,
            "constitution_fingerprint": service.state.fingerprint,
            "effective_from": service.state.effective_from,
            "rule_count": len(service.state.rules),
        }
    except Exception as exc:
        errors.append(f"failed to load constitution service: {type(exc).__name__}: {exc}")
        summary = {"path": path.as_posix()}

    return ValidationResult(ok=not errors, errors=errors, summary=summary)


def diff_constitutions(previous_path: Path, current_path: Path) -> Dict[str, Any]:
    previous = _load_yaml_dict(previous_path)
    current = _load_yaml_dict(current_path)

    prev_rules = previous.get("rules") if isinstance(previous.get("rules"), dict) else {}
    cur_rules = current.get("rules") if isinstance(current.get("rules"), dict) else {}

    added = sorted(set(cur_rules.keys()) - set(prev_rules.keys()))
    removed = sorted(set(prev_rules.keys()) - set(cur_rules.keys()))
    changed: List[str] = []
    for key in sorted(set(prev_rules.keys()) & set(cur_rules.keys())):
        if _canonical(prev_rules.get(key)) != _canonical(cur_rules.get(key)):
            changed.append(key)

    return {
        "previous_path": previous_path.as_posix(),
        "current_path": current_path.as_posix(),
        "version_before": str(previous.get("version") or ""),
        "version_after": str(current.get("version") or ""),
        "effective_from_before": str(previous.get("effective_from") or ""),
        "effective_from_after": str(current.get("effective_from") or ""),
        "added_rules": added,
        "removed_rules": removed,
        "changed_rules": changed,
        "breaking_change_hint": bool(removed or changed),
    }


def _load_yaml_dict(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"constitution file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise SystemExit(f"constitution file must contain a mapping: {path}")
    return raw


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _parse_iso(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _print_human(payload: Dict[str, Any]) -> None:
    validation = payload.get("validation", {}) if isinstance(payload, dict) else {}
    summary = validation.get("summary", {}) if isinstance(validation, dict) else {}
    print(f"ok={validation.get('ok')}")
    if isinstance(summary, dict):
        print(f"version={summary.get('constitution_version')}")
        print(f"fingerprint={summary.get('constitution_fingerprint')}")
        print(f"effective_from={summary.get('effective_from')}")
    errors = validation.get("errors", [])
    if isinstance(errors, list) and errors:
        print("errors:")
        for err in errors:
            print(f"- {err}")

    diff = payload.get("diff")
    if isinstance(diff, dict):
        print("diff:")
        print(f"- version: {diff.get('version_before')} -> {diff.get('version_after')}")
        print(f"- effective_from: {diff.get('effective_from_before')} -> {diff.get('effective_from_after')}")
        print(f"- added_rules: {diff.get('added_rules')}")
        print(f"- removed_rules: {diff.get('removed_rules')}")
        print(f"- changed_rules: {diff.get('changed_rules')}")


if __name__ == "__main__":
    raise SystemExit(main())
