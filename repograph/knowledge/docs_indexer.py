"""Docs indexer — walks markdown/rst files and emits DOC nodes with MENTIONED_IN_DOC edges."""

from __future__ import annotations

import os
import re
from pathlib import Path

from repograph.indexer.schema import (
    ADR_DECIDES, DOCUMENTED_BY, DOC_TITLE, DOC_TYPE, IN_FILE, MENTIONED_IN_DOC,
)

Triple = tuple[str, str, str]

_DOC_EXTENSIONS = {".md", ".rst", ".txt"}
_ADR_PATTERNS = (
    re.compile(r"^docs?/adr", re.I),
    re.compile(r"^adr[s/]", re.I),
    re.compile(r"/adr[-_]\d+", re.I),
    re.compile(r"architecture.decision", re.I),
)
_RUNBOOK_PATTERNS = (
    re.compile(r"runbook", re.I),
    re.compile(r"playbook", re.I),
    re.compile(r"ops/", re.I),
)
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}

# Match backtick code references like `module.Class.method`
_CODE_REF_PATTERN = re.compile(r"`([a-zA-Z_][\w.]*)`")
# Match module paths like repograph.graph.store
_DOTTED_REF_PATTERN = re.compile(r"\b([a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*){2,})\b")


def index_docs(repo_path: str) -> list[Triple]:
    """Walk all doc files in repo and return graph triples."""
    repo_root = Path(repo_path).expanduser().resolve()
    triples: list[Triple] = []
    for current_root, dirs, files in os.walk(repo_root, topdown=True):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for filename in files:
            file_path = Path(current_root) / filename
            if file_path.suffix.lower() not in _DOC_EXTENSIONS:
                continue
            try:
                rel = file_path.resolve().relative_to(repo_root).as_posix()
            except ValueError:
                continue
            triples.extend(_index_doc_file(file_path, rel))
    return triples


def _index_doc_file(file_path: Path, rel: str) -> list[Triple]:
    doc_node = f"doc:{rel}"
    doc_type = _classify_doc(rel)
    title = _extract_title(file_path)

    triples: list[Triple] = [
        (doc_node, IN_FILE, rel),
        (doc_node, DOC_TYPE, doc_type),
        (doc_node, DOC_TITLE, title or rel),
    ]

    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return triples

    refs = _extract_code_refs(text)
    for ref in refs:
        triples.append((ref, MENTIONED_IN_DOC, doc_node))
        triples.append((ref, DOCUMENTED_BY, doc_node))
        if doc_type == "adr":
            triples.append((doc_node, ADR_DECIDES, ref))

    return triples


def _classify_doc(rel: str) -> str:
    for pat in _ADR_PATTERNS:
        if pat.search(rel):
            return "adr"
    for pat in _RUNBOOK_PATTERNS:
        if pat.search(rel):
            return "runbook"
    return "doc"


def _extract_title(file_path: Path) -> str | None:
    try:
        for line in file_path.read_text(encoding="utf-8", errors="ignore").splitlines()[:10]:
            stripped = line.lstrip("#").strip()
            if stripped:
                return stripped[:120]
    except OSError:
        pass
    return None


def _extract_code_refs(text: str) -> list[str]:
    refs: set[str] = set()
    for m in _CODE_REF_PATTERN.finditer(text):
        candidate = m.group(1)
        if "." in candidate and len(candidate) > 3:
            refs.add(candidate)
    for m in _DOTTED_REF_PATTERN.finditer(text):
        refs.add(m.group(1))
    return list(refs)
