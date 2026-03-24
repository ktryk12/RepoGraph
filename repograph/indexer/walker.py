"""Repository file walker with gitignore support."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from .languages import language_for_path

SKIP_DIRS = {".git", "__pycache__", "node_modules"}


def walk(repo_path: str, respect_gitignore: bool = True) -> Iterator[tuple[Path, str]]:
    """Yield source files with their mapped language."""
    repo_root = Path(repo_path).expanduser().resolve()
    if not repo_root.is_dir():
        raise ValueError(f"Repository path does not exist or is not a directory: {repo_path}")

    ignore_spec = _build_ignore_spec(repo_root) if respect_gitignore else None

    for current_root, dirs, files in os.walk(repo_root, topdown=True):
        current_dir = Path(current_root)
        dirs[:] = [
            dirname
            for dirname in dirs
            if not _should_skip_dir(current_dir / dirname, repo_root, ignore_spec)
        ]

        for filename in files:
            file_path = current_dir / filename
            language = language_for_path(file_path)
            if language is None:
                continue
            if _should_skip_file(file_path, repo_root, ignore_spec):
                continue
            yield file_path, language


def _should_skip_dir(path: Path, repo_root: Path, ignore_spec: object | None) -> bool:
    if path.name in SKIP_DIRS:
        return True
    return _matches_ignore(path, repo_root, ignore_spec, is_dir=True)


def _should_skip_file(path: Path, repo_root: Path, ignore_spec: object | None) -> bool:
    if path.name.endswith(".min.js"):
        return True
    if _matches_ignore(path, repo_root, ignore_spec, is_dir=False):
        return True
    return _is_binary_file(path)


def _matches_ignore(path: Path, repo_root: Path, ignore_spec: object | None, is_dir: bool) -> bool:
    if ignore_spec is None:
        return False
    relative = path.resolve().relative_to(repo_root).as_posix()
    candidate = f"{relative}/" if is_dir else relative
    return bool(ignore_spec.match_file(candidate))


def _is_binary_file(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:8192]
    except OSError:
        return True

    if not chunk:
        return False
    if b"\x00" in chunk:
        return True

    text_bytes = bytes(range(32, 127)) + b"\b\f\n\r\t"
    non_text = sum(byte not in text_bytes for byte in chunk)
    return (non_text / len(chunk)) > 0.30


def _build_ignore_spec(repo_root: Path):
    try:
        from pathspec import PathSpec
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "pathspec is required for respect_gitignore=True. Install it with `pip install pathspec`."
        ) from exc

    patterns: list[str] = []
    for gitignore_path in _iter_gitignore_files(repo_root):
        patterns.extend(_load_prefixed_patterns(repo_root, gitignore_path))
    return PathSpec.from_lines("gitignore", patterns)


def _iter_gitignore_files(repo_root: Path) -> Iterator[Path]:
    for current_root, dirs, files in os.walk(repo_root, topdown=True):
        current_dir = Path(current_root)
        dirs[:] = [dirname for dirname in dirs if dirname not in SKIP_DIRS]
        if ".gitignore" in files:
            yield current_dir / ".gitignore"


def _load_prefixed_patterns(repo_root: Path, gitignore_path: Path) -> list[str]:
    base_dir = gitignore_path.parent.resolve().relative_to(repo_root).as_posix()
    patterns: list[str] = []

    for raw_line in gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        negate = line.startswith("!")
        if negate:
            line = line[1:]

        anchored = line.startswith("/")
        pattern = line.lstrip("/")

        if base_dir:
            pattern = f"{base_dir}/{pattern}" if anchored else f"{base_dir}/**/{pattern}"

        if negate:
            pattern = f"!{pattern}"

        patterns.append(pattern)

    return patterns
