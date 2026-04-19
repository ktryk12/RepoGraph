"""CI indexer — parses .github/workflows to emit CI_COVERS edges and job nodes."""

from __future__ import annotations

import os
import re
from pathlib import Path

from repograph.indexer.schema import CI_COVERS, CI_JOB_NAME, DOC_TYPE, IN_FILE

Triple = tuple[str, str, str]

_WORKFLOW_DIRS = (".github/workflows", ".gitlab-ci.yml", ".circleci")
_PATH_PATTERN = re.compile(r"""['"]([a-zA-Z0-9_/.*-]+\.py)['"']""")
_PATHS_PATTERN = re.compile(r"paths?:\s*\n((?:\s+-\s+.+\n?)+)", re.M)
_PATH_ITEM = re.compile(r"-\s+(.+)")


def index_ci(repo_path: str) -> list[Triple]:
    """Walk CI workflow files and return CI_COVERS triples."""
    repo_root = Path(repo_path).expanduser().resolve()
    triples: list[Triple] = []

    workflows_dir = repo_root / ".github" / "workflows"
    if workflows_dir.is_dir():
        for wf_file in workflows_dir.glob("*.yml"):
            triples.extend(_index_workflow(wf_file, repo_root))
        for wf_file in workflows_dir.glob("*.yaml"):
            triples.extend(_index_workflow(wf_file, repo_root))

    gitlab = repo_root / ".gitlab-ci.yml"
    if gitlab.exists():
        triples.extend(_index_workflow(gitlab, repo_root))

    return triples


def _index_workflow(wf_path: Path, repo_root: Path) -> list[Triple]:
    try:
        rel = wf_path.resolve().relative_to(repo_root).as_posix()
        text = wf_path.read_text(encoding="utf-8", errors="ignore")
    except (OSError, ValueError):
        return []

    job_node = f"ci:{rel}"
    job_name = wf_path.stem
    triples: list[Triple] = [
        (job_node, IN_FILE, rel),
        (job_node, DOC_TYPE, "ci_workflow"),
        (job_node, CI_JOB_NAME, job_name),
    ]

    # Extract file paths referenced in workflow
    covered_files: set[str] = set()

    # paths: blocks in workflow triggers
    for block_match in _PATHS_PATTERN.finditer(text):
        for item in _PATH_ITEM.finditer(block_match.group(1)):
            path = item.group(1).strip().strip("'\"")
            covered_files.add(path)

    # Explicit .py file references
    for m in _PATH_PATTERN.finditer(text):
        covered_files.add(m.group(1))

    # If no explicit paths, link to all Python source files (coarse)
    if not covered_files:
        covered_files = _find_source_files(repo_root)

    for filepath in covered_files:
        triples.append((job_node, CI_COVERS, filepath))

    return triples


def _find_source_files(repo_root: Path) -> set[str]:
    result: set[str] = set()
    skip = {".git", "__pycache__", "node_modules", ".venv"}
    for root, dirs, files in os.walk(repo_root, topdown=True):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if f.endswith(".py"):
                try:
                    rel = (Path(root) / f).resolve().relative_to(repo_root).as_posix()
                    result.add(rel)
                    if len(result) >= 200:
                        return result
                except ValueError:
                    pass
    return result
