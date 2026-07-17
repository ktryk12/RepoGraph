from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from verify.artifacts.registry import write_artifact


def _run_git(args: list[str], *, repo_root: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = str(proc.stdout or "").strip()
    return out or None


def _dirty_flag(*, repo_root: Path) -> bool | None:
    out = _run_git(["status", "--porcelain"], repo_root=repo_root)
    if out is None:
        return None
    return bool(out.strip())


def collect_build_info(
    *,
    repo_root: Path,
    env: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    source_env = env if env is not None else os.environ
    from policy.constitution_service import get_constitution_service
    constitution = get_constitution_service()

    env_sha = source_env.get("GITHUB_SHA") or source_env.get("CI_COMMIT_SHA")
    env_branch = source_env.get("GITHUB_REF_NAME") or source_env.get("GITHUB_REF") or source_env.get("CI_COMMIT_REF_NAME")

    git_sha = env_sha or _run_git(["rev-parse", "HEAD"], repo_root=repo_root) or "unknown"
    git_branch = env_branch or _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root=repo_root) or "unknown"

    return {
        "git_sha": str(git_sha),
        "git_branch": str(git_branch),
        "dirty": _dirty_flag(repo_root=repo_root),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "python_executable": str(sys.executable),
        "constitution": constitution.metadata(),
    }


def write_build_info(path: Path, payload: Dict[str, Any]) -> None:
    from policy.constitution_service import get_constitution_service
    constitution = get_constitution_service()
    write_artifact(
        "build_info_json",
        payload,
        path,
        metadata={
            "source_ref": "verify.build_info",
            "constitution_version": constitution.state.version,
        },
    )
