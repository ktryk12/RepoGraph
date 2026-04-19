"""Knowledge graph relationship enricher — runs all knowledge indexers and writes to graph."""

from __future__ import annotations

import time
from dataclasses import dataclass

from repograph.graph.factory import GraphStore
from repograph.indexer.config_indexer import index_config_file, walk_config_files

from .ci_indexer import index_ci
from .docs_indexer import index_docs
from .ownership_indexer import index_ownership


@dataclass
class KnowledgeIndexResult:
    docs_triples: int = 0
    ownership_triples: int = 0
    config_triples: int = 0
    ci_triples: int = 0
    duration_ms: int = 0

    @property
    def total(self) -> int:
        return self.docs_triples + self.ownership_triples + self.config_triples + self.ci_triples


def index_knowledge(
    repo_path: str,
    store: GraphStore,
    include: set[str] | None = None,
) -> KnowledgeIndexResult:
    """
    Run all knowledge indexers and write triples to graph store.

    Args:
        include: subset of {"docs", "ownership", "config", "ci"}; runs all if None.
    """
    from pathlib import Path
    repo_root = Path(repo_path).expanduser().resolve()
    enabled = include or {"docs", "ownership", "config", "ci"}
    t0 = time.perf_counter()
    result = KnowledgeIndexResult()

    if "docs" in enabled:
        triples = index_docs(str(repo_root))
        if triples:
            store.put_triples_batch(triples)
        result.docs_triples = len(triples)

    if "ownership" in enabled:
        triples = index_ownership(str(repo_root))
        if triples:
            store.put_triples_batch(triples)
        result.ownership_triples = len(triples)

    if "config" in enabled:
        all_triples = []
        for config_path, _ in walk_config_files(str(repo_root)):
            all_triples.extend(index_config_file(config_path, repo_root))
        if all_triples:
            store.put_triples_batch(all_triples)
        result.config_triples = len(all_triples)

    if "ci" in enabled:
        triples = index_ci(str(repo_root))
        if triples:
            store.put_triples_batch(triples)
        result.ci_triples = len(triples)

    result.duration_ms = int((time.perf_counter() - t0) * 1000)
    return result
