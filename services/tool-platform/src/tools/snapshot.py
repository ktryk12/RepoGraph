from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import ast
import json
import subprocess


@dataclass(frozen=True)
class RepoSnapshot:
    snapshot_id: str
    artifact_ref: str
    commit: str | None
    manifest: List[Dict[str, Any]]
    symbols: List[Dict[str, Any]]


def create_snapshot(
    *,
    commit: str | None = "HEAD",
    root: Path | None = None,
    include_symbols: bool = True,
) -> RepoSnapshot:
    root = root or Path(__file__).resolve().parents[1]

    if commit:
        entries = _git_list_tree(commit, root)
        paths = [e["path"] for e in entries]
        manifest = entries
        symbols = _extract_symbols_for_commit(commit, paths, root) if include_symbols else []
    else:
        paths = _git_list_worktree(root)
        manifest = [_manifest_entry_from_worktree(root / p, root) for p in paths]
        symbols = _extract_symbols_for_worktree(paths, root) if include_symbols else []

    manifest = sorted(manifest, key=lambda x: x["path"])
    symbols = sorted(symbols, key=lambda x: (x["path"], x["kind"], x["name"], x.get("line", 0)))

    payload = {
        "commit": commit,
        "manifest": manifest,
        "symbols": symbols,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    snapshot_id = sha256(raw).hexdigest()
    artifact_ref = f"artifact:sha256:{snapshot_id}"

    return RepoSnapshot(
        snapshot_id=snapshot_id,
        artifact_ref=artifact_ref,
        commit=commit,
        manifest=manifest,
        symbols=symbols,
    )


def _git_list_tree(commit: str, root: Path) -> List[Dict[str, Any]]:
    """
    Return manifest entries using git ls-tree (fast, no file reads).
    """
    out = _run_git(["ls-tree", "-r", "-l", commit], root)
    entries: List[Dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # format: <mode> <type> <sha> <size>\t<path>
        try:
            meta, path = line.split("\t", 1)
        except ValueError:
            continue
        parts = meta.split()
        if len(parts) < 4:
            continue
        sha = parts[2]
        size_str = parts[3]
        try:
            size = int(size_str)
        except Exception:
            size = 0
        entries.append({
            "path": path,
            "size": size,
            "sha256": sha,
        })
    return entries


def _git_list_worktree(root: Path) -> List[str]:
    out = _run_git(["ls-files"], root)
    return [line.strip() for line in out.splitlines() if line.strip()]


def _manifest_entry_from_worktree(path: Path, root: Path) -> Dict[str, Any]:
    rel = path.relative_to(root).as_posix()
    data = path.read_bytes()
    return {
        "path": rel,
        "size": len(data),
        "sha256": sha256(data).hexdigest(),
    }


def _extract_symbols_for_commit(commit: str, paths: Iterable[str], root: Path) -> List[Dict[str, Any]]:
    symbols: List[Dict[str, Any]] = []
    for path in paths:
        if not path.endswith(".py"):
            continue
        data = _git_show_bytes(commit, path, root)
        symbols.extend(_extract_symbols(path, data))
    return symbols


def _extract_symbols_for_worktree(paths: Iterable[str], root: Path) -> List[Dict[str, Any]]:
    symbols: List[Dict[str, Any]] = []
    for path in paths:
        if not path.endswith(".py"):
            continue
        data = (root / path).read_bytes()
        symbols.extend(_extract_symbols(path, data))
    return symbols


def _extract_symbols(path: str, data: bytes) -> List[Dict[str, Any]]:
    text = data.decode("utf-8", errors="replace")
    try:
        tree = ast.parse(text)
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out.append(_symbol_entry(path, "function", node.name, node.lineno))
        elif isinstance(node, ast.AsyncFunctionDef):
            out.append(_symbol_entry(path, "async_function", node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            out.append(_symbol_entry(path, "class", node.name, node.lineno))
    return out


def _symbol_entry(path: str, kind: str, name: str, line: int) -> Dict[str, Any]:
    return {"path": path, "kind": kind, "name": name, "line": int(line)}


def _git_show_bytes(commit: str, path: str, root: Path) -> bytes:
    spec = f"{commit}:{Path(path).as_posix()}"
    return _run_git_bytes(["show", spec], root)


def _run_git(args: List[str], root: Path) -> str:
    return _run_git_bytes(args, root).decode("utf-8", errors="replace")


def _run_git_bytes(args: List[str], root: Path) -> bytes:
    cmd = ["git"] + args
    proc = subprocess.run(
        cmd,
        cwd=str(root),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout
