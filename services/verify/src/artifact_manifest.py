from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from babyai_shared.fingerprint import sha256_file
from verify.artifacts.registry import write_artifact


MANIFEST_SCHEMA_VERSION = 1


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    from policy.constitution_service import get_constitution_service
    constitution = get_constitution_service()
    write_artifact(
        "artifact_manifest_json",
        payload,
        path,
        metadata={
            "source_ref": "verify.artifact_manifest",
            "constitution_version": constitution.state.version,
        },
    )


def _normalize_entries(artifact_paths: Dict[str, str]) -> List[Tuple[str, Path]]:
    entries: List[Tuple[str, Path]] = []
    seen_paths: set[str] = set()
    for name in sorted(artifact_paths.keys()):
        if name in {"manifest_json", "run_manifest_json"}:
            continue
        raw_path = artifact_paths.get(name)
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        normalized = Path(raw_path).as_posix()
        if normalized in seen_paths:
            continue
        seen_paths.add(normalized)
        entries.append((name, Path(normalized)))
    return entries


def build_artifact_manifest(
    *,
    run_id: str,
    artifact_paths: Dict[str, str],
    created_at_utc: str | None = None,
    schema_version: int = MANIFEST_SCHEMA_VERSION,
) -> Dict[str, Any]:
    created_at = created_at_utc or _now_utc_iso()
    from policy.constitution_service import get_constitution_service
    constitution = get_constitution_service()
    rows: List[Dict[str, Any]] = []
    for name, path in _normalize_entries(artifact_paths):
        if not path.exists():
            raise FileNotFoundError(f"Artifact missing before manifest write: {path.as_posix()}")
        rows.append(
            {
                "name": str(name),
                "path": path.as_posix(),
                "sha256": sha256_file(path),
                "bytes": int(path.stat().st_size),
                "created_at_utc": created_at,
            }
        )
    rows.sort(key=lambda row: (str(row.get("name", "")), str(row.get("path", ""))))
    return {
        "schema_version": int(schema_version),
        "run_id": str(run_id),
        "created_at_utc": created_at,
        "constitution": constitution.metadata(),
        "artifacts": rows,
    }


def write_artifact_manifest(
    *,
    output_path: Path,
    run_id: str,
    artifact_paths: Dict[str, str],
    created_at_utc: str | None = None,
    schema_version: int = MANIFEST_SCHEMA_VERSION,
) -> Dict[str, Any]:
    payload = build_artifact_manifest(
        run_id=run_id,
        artifact_paths=artifact_paths,
        created_at_utc=created_at_utc,
        schema_version=schema_version,
    )
    _atomic_write_json(output_path, payload)
    return payload


def validate_artifact_manifest(manifest_path: Path) -> List[str]:
    errors: List[str] = []
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"manifest_unreadable:{manifest_path.as_posix()}:{exc}"]

    if not isinstance(payload, dict):
        return [f"manifest_not_dict:{manifest_path.as_posix()}"]
    rows = payload.get("artifacts")
    if not isinstance(rows, list):
        return [f"manifest_artifacts_not_list:{manifest_path.as_posix()}"]

    for row in rows:
        if not isinstance(row, dict):
            errors.append("manifest_row_not_dict")
            continue
        path_raw = row.get("path")
        expected_sha = row.get("sha256")
        expected_bytes = row.get("bytes")
        created_at = row.get("created_at_utc")

        if not isinstance(path_raw, str) or not path_raw.strip():
            errors.append("manifest_row_missing_path")
            continue
        if not isinstance(created_at, str) or not created_at.strip():
            errors.append(f"manifest_row_missing_created_at:{path_raw}")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            errors.append(f"manifest_row_invalid_sha256:{path_raw}")
        if not isinstance(expected_bytes, int) or expected_bytes < 0:
            errors.append(f"manifest_row_invalid_bytes:{path_raw}")

        path = Path(path_raw)
        if not path.exists():
            errors.append(f"missing:{path.as_posix()}")
            continue

        actual_bytes = int(path.stat().st_size)
        if isinstance(expected_bytes, int) and actual_bytes != expected_bytes:
            errors.append(f"bytes_mismatch:{path.as_posix()}")

        actual_sha = sha256_file(path)
        if isinstance(expected_sha, str) and actual_sha != expected_sha:
            errors.append(f"sha256_mismatch:{path.as_posix()}")

    return errors


def assert_artifact_manifest_integrity(manifest_path: Path) -> None:
    errors = validate_artifact_manifest(manifest_path)
    if errors:
        raise ValueError(f"Artifact manifest integrity failed for {manifest_path.as_posix()}: {errors}")
