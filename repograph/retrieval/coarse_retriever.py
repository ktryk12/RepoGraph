"""Coarse retrieval — first-pass broad candidate set per task family."""

from __future__ import annotations

from repograph.graph.factory import GraphStore
from repograph.indexer.schema import BELONGS_TO_SERVICE, IN_FILE, TESTS


def coarse_retrieve(
    query: str,
    task_family: str,
    store: GraphStore,
    limit: int = 40,
) -> list[str]:
    """Return a broad candidate set of symbol IDs for the given task family."""
    match task_family:
        case "symbol_lookup":
            return store.search(query, limit=limit)

        case "file_to_symbol_map":
            # query is expected to be a file path
            symbols = store.file_symbols(query)
            if not symbols:
                symbols = store.search(query, limit=limit)
            return symbols[:limit]

        case "bug_localization":
            # Broad search + expand to callers
            seeds = store.search(query, limit=limit // 2)
            callers = []
            for s in seeds[:5]:
                callers.extend(store.callers_of(s)[:4])
            return _dedupe(seeds + callers, limit)

        case "call_chain_reasoning":
            seeds = store.search(query, limit=10)
            result = list(seeds)
            for s in seeds[:5]:
                result.extend(store.callees_of(s)[:6])
                result.extend(store.callers_of(s)[:6])
            return _dedupe(result, limit)

        case "blast_radius_analysis":
            seeds = store.search(query, limit=5)
            result = list(seeds)
            for s in seeds[:3]:
                affected = store.blast_radius(s, depth=2)
                for deps in affected.values():
                    result.extend(deps)
            return _dedupe(result, limit)

        case "targeted_refactor":
            seeds = store.search(query, limit=10)
            result = list(seeds)
            for s in seeds[:5]:
                result.extend(store.callers_of(s)[:4])
                result.extend(store.outgoing(s, IN_FILE)[:2])
            return _dedupe(result, limit)

        case "test_impact_lookup":
            seeds = store.search(query, limit=10)
            result = list(seeds)
            for s in seeds[:5]:
                result.extend(store.incoming(s, TESTS)[:8])
            return _dedupe(result, limit)

        case "targeted_test_generation":
            seeds = store.search(query, limit=10)
            result = list(seeds)
            for s in seeds[:5]:
                result.extend(store.callees_of(s)[:6])
            return _dedupe(result, limit)

        case "config_dependency_reasoning":
            seeds = store.search(query, limit=10)
            result = list(seeds)
            for s in seeds[:5]:
                result.extend(store.outgoing(s, BELONGS_TO_SERVICE)[:4])
            return _dedupe(result, limit)

        case _:
            return store.search(query, limit=limit)


def _dedupe(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
        if len(result) >= limit:
            break
    return result
