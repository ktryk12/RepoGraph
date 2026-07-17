from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import json
import subprocess

from babyai_shared.storage.artifact_store import FileArtifactStore
from tools.base import ToolBudget, ToolResult, artifact_ref_for_bytes, clamp_bytes


class GitApplyTool:
    name = "git_apply"
    DEFAULT_MAX_FILES_CHANGED = 25
    DEFAULT_MAX_LINES_CHANGED = 1200
    HARD_MAX_FILES_CHANGED = 25
    HARD_MAX_LINES_CHANGED = 1200
    PROTECTED_PATH_PREFIXES = ("policy/", "verify/", "schemas/")

    def __init__(
        self,
        root: Path | None = None,
        *,
        artifact_store: Optional[FileArtifactStore] = None,
    ) -> None:
        self.root = root or Path(__file__).resolve().parents[1]
        self.store = artifact_store or FileArtifactStore()

    def run(self, request: Dict[str, Any], *, budget: ToolBudget) -> ToolResult:
        patch_text = request.get("patch_text")
        patch_ref = request.get("patch_ref")
        dry_run = bool(request.get("dry_run"))
        allowed_paths = request.get("allowed_paths")
        allow_protected_paths = request.get("allow_protected_paths")
        max_files_changed = request.get("max_files_changed")
        max_lines_changed = request.get("max_lines_changed")

        if patch_text is None and isinstance(patch_ref, str):
            raw = self.store.get(patch_ref)
            if raw is None:
                return ToolResult(
                    self.name,
                    False,
                    {"error": "patch_ref_not_found"},
                    warnings=["missing_patch_ref"],
                )
            patch_text = raw.decode("utf-8", errors="replace")

        if not isinstance(patch_text, str) or not patch_text.strip():
            return ToolResult(
                self.name,
                False,
                {"error": "missing_patch_text"},
                warnings=["invalid_patch_text"],
            )

        raw_patch = patch_text.encode("utf-8")
        if len(raw_patch) > budget.max_bytes:
            return ToolResult(
                self.name,
                False,
                {"error": "patch_too_large", "max_bytes": budget.max_bytes},
                warnings=["patch_too_large"],
            )

        patch_ref = patch_ref or artifact_ref_for_bytes(raw_patch)

        files_changed, diffstat = _analyze_patch(patch_text)
        diffstat_ref = self._store_diffstat(diffstat, patch_ref)

        allowed = _normalize_allowed_paths(allowed_paths)
        if not allowed:
            return ToolResult(
                self.name,
                False,
                {
                    "error": "missing_scope_allowlist",
                    "patch_ref": patch_ref,
                    "changed_files": files_changed,
                    "diffstat_ref": diffstat_ref,
                },
                warnings=["missing_scope_allowlist"],
            )

        if "." in allowed:
            return ToolResult(
                self.name,
                False,
                {
                    "error": "invalid_scope_allowlist",
                    "patch_ref": patch_ref,
                    "changed_files": files_changed,
                    "diffstat_ref": diffstat_ref,
                    "details": "Repo-root scope ('.') is not allowed",
                },
                warnings=["invalid_scope_allowlist"],
            )

        violations = [f for f in files_changed if not _path_allowed(f, allowed)]
        if violations:
            return ToolResult(
                self.name,
                False,
                {
                    "error": "scope_violation",
                    "patch_ref": patch_ref,
                    "changed_files": files_changed,
                    "diffstat_ref": diffstat_ref,
                    "disallowed_files": violations,
                },
                warnings=["scope_violation"],
            )

        allowed_protected = _normalize_allowed_paths(allow_protected_paths) or []
        protected_violations = [f for f in files_changed if _is_protected_path(f) and not _path_allowed(f, allowed_protected)]
        if protected_violations:
            return ToolResult(
                self.name,
                False,
                {
                    "error": "protected_path_violation",
                    "patch_ref": patch_ref,
                    "changed_files": files_changed,
                    "diffstat_ref": diffstat_ref,
                    "protected_files": protected_violations,
                },
                warnings=["protected_path_violation"],
            )

        requested_max_files = _as_int(max_files_changed)
        max_files = self.DEFAULT_MAX_FILES_CHANGED if requested_max_files is None else max(1, requested_max_files)
        max_files = min(max_files, self.HARD_MAX_FILES_CHANGED)
        if len(files_changed) > max_files:
            return ToolResult(
                self.name,
                False,
                {
                    "error": "too_many_files_changed",
                    "patch_ref": patch_ref,
                    "changed_files": files_changed,
                    "diffstat_ref": diffstat_ref,
                    "files_changed": len(files_changed),
                    "max_files_changed": max_files,
                },
                warnings=["too_many_files_changed"],
            )

        requested_max_lines = _as_int(max_lines_changed)
        max_lines = self.DEFAULT_MAX_LINES_CHANGED if requested_max_lines is None else max(1, requested_max_lines)
        max_lines = min(max_lines, self.HARD_MAX_LINES_CHANGED)
        total_lines = int(diffstat.get("lines_added", 0)) + int(diffstat.get("lines_removed", 0))
        if total_lines > max_lines:
            return ToolResult(
                self.name,
                False,
                {
                    "error": "too_many_lines_changed",
                    "patch_ref": patch_ref,
                    "changed_files": files_changed,
                    "diffstat_ref": diffstat_ref,
                    "lines_changed": total_lines,
                    "max_lines_changed": max_lines,
                },
                warnings=["too_many_lines_changed"],
            )

        cmd = ["git", "apply"]
        if dry_run:
            cmd.append("--check")

        proc = subprocess.run(
            cmd,
            input=raw_patch,
            cwd=self.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        combined = (proc.stdout or b"") + (proc.stderr or b"")
        text = combined.decode("utf-8", errors="replace")
        from babyai_shared.privacy.redaction import redact_text

        text = redact_text(text)
        clipped, truncated = clamp_bytes(text, budget.max_bytes)
        result_ref = artifact_ref_for_bytes(clipped.encode("utf-8"))

        warnings = []
        if truncated:
            warnings.append("truncated_output")
        if proc.returncode != 0:
            warnings.append("git_apply_failed")

        output = {
            "patch_ref": patch_ref,
            "result_ref": result_ref,
            "diffstat_ref": diffstat_ref,
            "changed_files": files_changed,
            "files_changed": len(files_changed),
            "lines_added": diffstat.get("lines_added", 0),
            "lines_removed": diffstat.get("lines_removed", 0),
            "dry_run": dry_run,
            "applied": proc.returncode == 0 and not dry_run,
            "exit_code": proc.returncode,
            "truncated": truncated,
        }
        return ToolResult(self.name, proc.returncode == 0, output, artifact_ref=result_ref, warnings=warnings)

    def _store_diffstat(self, diffstat: Dict[str, Any], patch_ref: str) -> str:
        payload = {
            "patch_ref": patch_ref,
            "diffstat": diffstat,
        }
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        return self.store.put(raw, name=f"diffstat:{patch_ref}").ref


def _normalize_allowed_paths(value: Any) -> List[str] | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return [value.strip().rstrip("/")]
    if isinstance(value, list):
        cleaned = [str(v).strip().rstrip("/") for v in value if str(v).strip()]
        return cleaned if cleaned else None
    return None


def _path_allowed(path: str, allowed: List[str]) -> bool:
    if "." in allowed:
        return True
    for root in allowed:
        if path == root or path.startswith(f"{root}/"):
            return True
    return False


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _is_protected_path(path: str) -> bool:
    normalized = _normalize_path(path)
    for prefix in GitApplyTool.PROTECTED_PATH_PREFIXES:
        if normalized == prefix.rstrip("/") or normalized.startswith(prefix):
            return True
    return False


def _analyze_patch(patch_text: str) -> tuple[List[str], Dict[str, Any]]:
    files: Dict[str, Dict[str, int]] = {}
    current_file: Optional[str] = None

    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                current_file = _normalize_path(parts[3])
                files.setdefault(current_file, {"added": 0, "removed": 0})
            continue
        if line.startswith("+++ "):
            path = _normalize_path(line[4:].strip())
            if path:
                current_file = path
                files.setdefault(current_file, {"added": 0, "removed": 0})
            continue
        if line.startswith("--- "):
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if current_file:
                files[current_file]["added"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            if current_file:
                files[current_file]["removed"] += 1

    file_list = sorted(files.keys())
    total_added = sum(v["added"] for v in files.values())
    total_removed = sum(v["removed"] for v in files.values())
    return file_list, {
        "files": {k: {"added": v["added"], "removed": v["removed"]} for k, v in files.items()},
        "lines_added": total_added,
        "lines_removed": total_removed,
    }


def _normalize_path(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path.strip().lstrip("./")
