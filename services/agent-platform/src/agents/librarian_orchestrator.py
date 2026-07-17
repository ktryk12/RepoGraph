from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Dict, Iterable, List, Optional, Tuple
import json

from babyai_shared.knowledge.registry import KnowledgeRegistry, LibrarianSpec
from babyai_shared.storage.artifact_store import FileArtifactStore


@dataclass(frozen=True)
class LibrarianNamespace:
    namespace: str
    root_paths: List[str]


class LibrarianOrchestrator:
    """
    Auto-provision librarians per namespace based on repo snapshots.
    """

    def __init__(
        self,
        *,
        registry: Optional[KnowledgeRegistry] = None,
        registry_path: Optional[str] = None,
        artifact_store: Optional[FileArtifactStore] = None,
        artifact_root: Optional[str] = None,
    ) -> None:
        self.registry = registry or KnowledgeRegistry(path=registry_path or "knowledge/registry.sqlite")
        if artifact_store is not None:
            self.store = artifact_store
        else:
            self.store = FileArtifactStore(root=artifact_root or "artifacts")

    def ensure_librarians(
        self,
        truth_pack: Dict[str, Any],
        snapshot_ref: str,
    ) -> List[LibrarianSpec]:
        payload = self._load_snapshot_payload(snapshot_ref)
        snapshot_id = _snapshot_id(payload)
        namespaces = _discover_namespaces(payload.get("manifest", []))

        specs: List[LibrarianSpec] = []
        for ns in namespaces:
            spec = LibrarianSpec(
                namespace=ns.namespace,
                root_paths=ns.root_paths,
                snapshot_id=snapshot_id,
                snapshot_ref=snapshot_ref,
            )
            self.registry.upsert_librarian(spec)
            specs.append(spec)

        return specs

    def _load_snapshot_payload(self, snapshot_ref: str) -> Dict[str, Any]:
        raw = self.store.get(snapshot_ref)
        if raw is None:
            raise FileNotFoundError(f"Snapshot ref not found: {snapshot_ref}")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Snapshot payload must be a mapping")
        return payload


def _discover_namespaces(manifest: Iterable[Dict[str, Any]]) -> List[LibrarianNamespace]:
    """
    Map manifest paths to librarian namespaces.

    Heuristics:
      - services/<name>/... -> services/<name>
      - apps/<name>/...     -> apps/<name>
      - src/<name>/...      -> src/<name>
      - fallback "root" if non-matching paths exist or nothing matches
    """
    buckets: Dict[str, set[str]] = {}
    unmatched = False

    for entry in manifest:
        path = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(path, str):
            continue
        parts = path.split("/")
        if len(parts) < 2:
            unmatched = True
            continue
        prefix = parts[0]
        name = parts[1] if len(parts) >= 2 else None

        if prefix in {"services", "apps"} and name:
            ns = f"{prefix}/{name}"
            buckets.setdefault(ns, set()).add(f"{prefix}/{name}")
        elif prefix == "src" and name:
            ns = f"src/{name}"
            buckets.setdefault(ns, set()).add(f"src/{name}")
        else:
            unmatched = True

    if not buckets:
        buckets["root"] = {"."}
    elif unmatched:
        buckets.setdefault("root", set()).add(".")

    out = [
        LibrarianNamespace(namespace=ns, root_paths=sorted(paths))
        for ns, paths in sorted(buckets.items(), key=lambda x: x[0])
    ]
    return out


def _snapshot_id(payload: Dict[str, Any]) -> str:
    """
    Stable snapshot id derived from snapshot payload.
    """
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
