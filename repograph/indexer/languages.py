"""Language helpers for repository indexing."""

from __future__ import annotations

from pathlib import Path

LANGUAGE_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cpp": "cpp",
    ".c": "c",
    ".cs": "c_sharp",
    ".rb": "ruby",
}


def language_for_path(path: str | Path) -> str | None:
    """Return the configured tree-sitter language for a file path."""
    suffix = Path(path).suffix.lower()
    return LANGUAGE_MAP.get(suffix)
