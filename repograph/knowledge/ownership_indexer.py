"""Ownership indexer — parses CODEOWNERS and emits OWNED_BY edges."""

from __future__ import annotations

import re
from pathlib import Path

from repograph.indexer.schema import OWNED_BY

Triple = tuple[str, str, str]

_CODEOWNERS_LOCATIONS = (
    "CODEOWNERS",
    ".github/CODEOWNERS",
    "docs/CODEOWNERS",
)


def index_ownership(repo_path: str) -> list[Triple]:
    """Parse CODEOWNERS and return OWNED_BY triples."""
    repo_root = Path(repo_path).expanduser().resolve()
    for location in _CODEOWNERS_LOCATIONS:
        codeowners = repo_root / location
        if codeowners.exists():
            return _parse_codeowners(codeowners, repo_root)
    return []


def _parse_codeowners(path: Path, repo_root: Path) -> list[Triple]:
    triples: list[Triple] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return triples

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        pattern, owners = parts[0], parts[1:]
        # Find matching files in repo
        matched = _glob_pattern(pattern, repo_root)
        for filepath in matched:
            for owner in owners:
                triples.append((filepath, OWNED_BY, owner))
    return triples


def _glob_pattern(pattern: str, repo_root: Path) -> list[str]:
    pattern = pattern.lstrip("/")
    try:
        matches = list(repo_root.glob(f"**/{pattern}") if "*" not in pattern else repo_root.glob(pattern))
        result = []
        for m in matches[:50]:  # cap to avoid explosion
            try:
                result.append(m.resolve().relative_to(repo_root).as_posix())
            except ValueError:
                pass
        return result
    except Exception:
        return [pattern]
