from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json
import subprocess

from agents.base import Agent
from babyai_shared.bus.protocol import Message, MessageType, Context
from babyai_shared.repobrain.index_bm25 import BM25Index, build_index_for_snapshot
from babyai_shared.storage.artifact_store import FileArtifactStore


@dataclass(frozen=True)
class ContextPack:
    namespace: str
    question: str
    snapshot_ref: str
    snapshot_id: str
    facts: List[Dict[str, Any]]
    evidence: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "namespace": self.namespace,
            "question": self.question,
            "snapshot_ref": self.snapshot_ref,
            "snapshot_id": self.snapshot_id,
            "facts": list(self.facts),
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class SnapshotView:
    snapshot_id: str
    commit: Optional[str]
    manifest: List[Dict[str, Any]]
    symbols: List[Dict[str, Any]]


class LibrarianAgent(Agent):
    """
    Namespace-scoped librarian with ingest + retrieval helpers.
    """

    def __init__(
        self,
        *,
        namespace: str,
        root_paths: Iterable[str],
        snapshot_ref: Optional[str] = None,
        repo_root: Optional[Path] = None,
        artifact_root: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id or _default_agent_id(namespace),
            role="librarian",
            accepts={MessageType.CLARIFICATION_REQUEST},
        )
        self.namespace = namespace
        self.root_paths = sorted({p.strip().rstrip("/") for p in root_paths if str(p).strip()}) or ["."]
        self.snapshot_ref = snapshot_ref
        self.repo_root = repo_root or Path(__file__).resolve().parents[1]
        self.store = FileArtifactStore(root=artifact_root or "artifacts")
        self._index: Optional[BM25Index] = None
        self._snapshot: Optional[SnapshotView] = None

    def process(self, message: Message, context: Context) -> List[Message]:
        # Placeholder: no message-based flow yet.
        return []

    def ingest(
        self,
        *,
        snapshot_ref: Optional[str] = None,
        diff: Optional[Dict[str, Any]] = None,
        namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._ensure_namespace(namespace)
        ref = snapshot_ref or self.snapshot_ref
        if not ref:
            raise ValueError("snapshot_ref is required for ingest")

        snapshot = self._load_snapshot(ref)
        filtered = _filter_manifest(snapshot.manifest, self.root_paths)
        snapshot_view = SnapshotView(
            snapshot_id=snapshot.snapshot_id,
            commit=snapshot.commit,
            manifest=filtered,
            symbols=snapshot.symbols,
        )
        self._index = build_index_for_snapshot(
            snapshot_view,
            root=self.repo_root,
            scope_id=self.namespace,
        )
        self._snapshot = snapshot_view
        self.snapshot_ref = ref

        return {
            "namespace": self.namespace,
            "snapshot_ref": ref,
            "snapshot_id": snapshot.snapshot_id,
            "proposals": [],
            "notes": [],
            "index_updates": {
                "indexed_files": len(filtered),
                "diff_applied": bool(diff),
            },
        }

    def retrieve(
        self,
        question: str,
        *,
        top_k: int = 5,
        namespace: Optional[str] = None,
    ) -> ContextPack:
        self._ensure_namespace(namespace)
        if not isinstance(question, str) or not question.strip():
            raise ValueError("question must be a non-empty string")
        if self._index is None or self._snapshot is None:
            self.ingest()

        assert self._index is not None
        assert self._snapshot is not None
        hits = self._index.search(question, scope_id=self.namespace, top_k=top_k)

        facts: List[Dict[str, Any]] = []
        evidence: List[Dict[str, Any]] = []
        for hit in hits:
            if not _path_allowed(hit.path, self.root_paths):
                continue
            excerpt = _extract_snippet(
                hit.path,
                question,
                commit=self._snapshot.commit,
                root=self.repo_root,
            )
            facts.append({
                "path": hit.path,
                "score": hit.score,
                "summary": f"Relevant file: {hit.path}",
            })
            evidence.append({
                "path": hit.path,
                "score": hit.score,
                "excerpt": excerpt,
                "ref": f"{self.snapshot_ref}#{hit.path}",
            })

        return ContextPack(
            namespace=self.namespace,
            question=question,
            snapshot_ref=self.snapshot_ref or "",
            snapshot_id=self._snapshot.snapshot_id,
            facts=facts,
            evidence=evidence,
        )

    def _load_snapshot(self, snapshot_ref: str) -> SnapshotView:
        raw = self.store.get(snapshot_ref)
        if raw is None:
            raise FileNotFoundError(f"Snapshot ref not found: {snapshot_ref}")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Snapshot payload must be a mapping")
        snapshot_id = _snapshot_id(payload)
        commit = payload.get("commit") if isinstance(payload.get("commit"), str) else None
        manifest = payload.get("manifest") if isinstance(payload.get("manifest"), list) else []
        symbols = payload.get("symbols") if isinstance(payload.get("symbols"), list) else []
        return SnapshotView(
            snapshot_id=snapshot_id,
            commit=commit,
            manifest=manifest,
            symbols=symbols,
        )

    def _ensure_namespace(self, namespace: Optional[str]) -> None:
        if namespace and namespace != self.namespace:
            raise ValueError(f"Namespace mismatch: {namespace} != {self.namespace}")

    # ------------------------------------------------------------------
    # SkillProvider interface (Sprint A6-Adoption)
    # ------------------------------------------------------------------

    def provide(self, context: Dict[str, Any]) -> Dict[str, Any]:
        query = str(context.get("query") or "").strip()
        retrieved_text = ""
        if query:
            try:
                pack = self.retrieve(query)
                parts = [f["content"] for f in pack.facts if isinstance(f, dict) and f.get("content")]
                retrieved_text = "\n\n".join(parts)
            except Exception:
                pass
        return {
            "skill_context": retrieved_text,
            "skill_ids": ["librarian-bm25"],
            "token_count": len(retrieved_text) // 4,
        }


def _snapshot_id(payload: Dict[str, Any]) -> str:
    manifest = payload.get("manifest", [])
    symbols = payload.get("symbols", [])
    commit = payload.get("commit")
    normalized = {
        "commit": commit,
        "manifest": sorted(manifest, key=lambda x: x.get("path", "")) if isinstance(manifest, list) else [],
        "symbols": sorted(
            symbols,
            key=lambda x: (
                x.get("path", ""),
                x.get("kind", ""),
                x.get("name", ""),
                x.get("line", 0),
            ),
        )
        if isinstance(symbols, list)
        else [],
    }
    raw = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return sha256(raw).hexdigest()


def _filter_manifest(manifest: Iterable[Dict[str, Any]], root_paths: List[str]) -> List[Dict[str, Any]]:
    if "." in root_paths:
        return list(manifest)
    filtered: List[Dict[str, Any]] = []
    for entry in manifest:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str):
            continue
        if _path_allowed(path, root_paths):
            filtered.append(entry)
    return filtered


def _path_allowed(path: str, root_paths: List[str]) -> bool:
    if "." in root_paths:
        return True
    for root in root_paths:
        if path == root or path.startswith(f"{root}/"):
            return True
    return False


def _extract_snippet(path: str, question: str, *, commit: Optional[str], root: Path) -> str:
    text = _load_text(commit, path, root)
    if not text:
        return ""
    tokens = question.lower().split()
    for tok in tokens:
        idx = text.lower().find(tok)
        if idx >= 0:
            start = max(0, idx - 60)
            end = min(len(text), idx + 160)
            return text[start:end].strip()
    return text[:200].strip()


def _load_text(commit: Optional[str], path: str, root: Path) -> str:
    if commit:
        return _git_show(commit, path, root)
    file_path = root / path
    if not file_path.exists() or not file_path.is_file():
        return ""
    return file_path.read_text(encoding="utf-8", errors="replace")


def _git_show(commit: str, path: str, root: Path) -> str:
    spec = f"{commit}:{Path(path).as_posix()}"
    proc = subprocess.run(
        ["git", "show", spec],
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode("utf-8", errors="replace")


def _default_agent_id(namespace: str) -> str:
    safe = namespace.replace("/", "-").replace("\\", "-")
    return f"librarian-{safe}"
