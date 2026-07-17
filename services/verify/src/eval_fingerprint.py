from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from verify.artifacts.registry import write_artifact


EVAL_MANIFEST_SCHEMA_VERSION = 1


def _task_id(task: Dict[str, Any], fallback: str) -> str:
    if isinstance(task.get("task_id"), str):
        return str(task["task_id"])
    spec = task.get("spec")
    if isinstance(spec, dict) and isinstance(spec.get("id"), str):
        return str(spec["id"])
    return fallback


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _line_count(payload: bytes) -> int:
    if not payload:
        return 0
    return int(payload.count(b"\n") + (0 if payload.endswith(b"\n") else 1))


def _relative_posix(path: Path, root: Optional[Path]) -> str:
    if root is None:
        return path.as_posix()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def build_eval_manifest(
    tasks: Sequence[Tuple[str, Dict[str, Any]]],
    *,
    root: Optional[Path] = None,
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for task_path, task in sorted(tasks, key=lambda item: str(item[0])):
        path_obj = Path(task_path)
        payload = path_obj.read_bytes()
        row = {
            "task_id": _task_id(task, path_obj.stem),
            "path": _relative_posix(path_obj, root),
            "sha256": _sha256_bytes(payload),
            "size_bytes": int(len(payload)),
            "line_count": _line_count(payload),
        }
        rows.append(row)

    rows.sort(key=lambda row: (str(row["task_id"]), str(row["path"])))
    task_ids = sorted({str(row["task_id"]) for row in rows})
    return {
        "schema_version": int(EVAL_MANIFEST_SCHEMA_VERSION),
        "file_count": int(len(rows)),
        "task_count": int(len(task_ids)),
        "task_ids": task_ids,
        "files": rows,
    }


def build_eval_manifest_for_dir(
    tasks_dir: Path,
    *,
    split: Optional[set[str]] = None,
) -> Dict[str, Any]:
    if not tasks_dir.exists():
        raise FileNotFoundError(f"Tasks dir not found: {tasks_dir}")

    tasks: List[Tuple[str, Dict[str, Any]]] = []
    for path_obj in sorted(tasks_dir.glob("*.json")):
        parsed = json.loads(path_obj.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise TypeError(f"Task must be dict JSON: {path_obj}")
        task_id = _task_id(parsed, path_obj.stem)
        if split is not None and task_id not in split:
            continue
        tasks.append((str(path_obj), parsed))
    return build_eval_manifest(tasks, root=tasks_dir)


def compute_eval_set_fingerprint(manifest: Dict[str, Any]) -> str:
    canonical = json.dumps(manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_eval_manifest_lock(lock_path: Path, manifest: Dict[str, Any]) -> None:
    from policy.constitution_service import get_constitution_service
    constitution = get_constitution_service()
    payload = json.dumps(manifest, indent=2, ensure_ascii=True, sort_keys=True) + "\n"
    write_artifact(
        "eval_manifest_lock_json",
        payload,
        lock_path,
        metadata={
            "source_ref": "verify.eval_fingerprint",
            "constitution_version": constitution.state.version,
        },
    )


def load_eval_manifest_lock(lock_path: Path) -> Dict[str, Any]:
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Eval manifest lock must be dict JSON: {lock_path}")
    return payload


def diff_eval_manifests(expected: Dict[str, Any], actual: Dict[str, Any]) -> List[str]:
    expected_files = expected.get("files", [])
    actual_files = actual.get("files", [])
    if not isinstance(expected_files, list) or not isinstance(actual_files, list):
        return ["manifest_shape_mismatch"]

    expected_map: Dict[str, Dict[str, Any]] = {}
    actual_map: Dict[str, Dict[str, Any]] = {}

    for row in expected_files:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path", ""))
        task_id = str(row.get("task_id", ""))
        if path and task_id:
            expected_map[f"{task_id}::{path}"] = row
    for row in actual_files:
        if not isinstance(row, dict):
            continue
        path = str(row.get("path", ""))
        task_id = str(row.get("task_id", ""))
        if path and task_id:
            actual_map[f"{task_id}::{path}"] = row

    diffs: List[str] = []
    for key in sorted(set(expected_map) - set(actual_map)):
        diffs.append(f"missing:{key}")
    for key in sorted(set(actual_map) - set(expected_map)):
        diffs.append(f"extra:{key}")

    for key in sorted(set(expected_map) & set(actual_map)):
        exp_sha = str(expected_map[key].get("sha256", ""))
        act_sha = str(actual_map[key].get("sha256", ""))
        if exp_sha != act_sha:
            diffs.append(f"sha256_mismatch:{key}")

    return diffs
