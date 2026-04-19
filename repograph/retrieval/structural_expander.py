"""Structural expander — enriches coarse candidates via graph edges."""

from __future__ import annotations

from repograph.graph.factory import GraphStore
from repograph.indexer.schema import BELONGS_TO_SERVICE, DEFINES, IMPORTS, INHERITS


def expand(
    symbols: list[str],
    task_family: str,
    store: GraphStore,
    max_symbols: int = 80,
) -> list[str]:
    """Expand a coarse symbol set using graph structure relevant to the task."""
    result = list(symbols)
    seen = set(symbols)

    def _add(candidates: list[str]) -> None:
        for c in candidates:
            if c not in seen and len(result) < max_symbols:
                seen.add(c)
                result.append(c)

    for sym in list(symbols):
        match task_family:
            case "call_chain_reasoning" | "bug_localization" | "blast_radius_analysis":
                _add(store.callers_of(sym))
                _add(store.callees_of(sym))

            case "targeted_refactor" | "symbol_lookup":
                _add(store.callers_of(sym))
                _add(store.outgoing(sym, DEFINES))
                parent = store.incoming(sym, DEFINES)
                _add(parent)

            case "targeted_test_generation" | "test_impact_lookup":
                _add(store.callees_of(sym))
                _add(store.outgoing(sym, DEFINES))

            case "file_to_symbol_map":
                _add(store.outgoing(sym, DEFINES))

            case "config_dependency_reasoning":
                svc = store.first_outgoing(sym, BELONGS_TO_SERVICE)
                if svc:
                    _add(store.incoming(svc, BELONGS_TO_SERVICE)[:10])

            case _:
                _add(store.callers_of(sym))

        # Always pull in co-service siblings for context (capped tightly)
        svc = store.first_outgoing(sym, BELONGS_TO_SERVICE)
        if svc:
            siblings = store.incoming(svc, BELONGS_TO_SERVICE)[:5]
            _add(siblings)

        if len(result) >= max_symbols:
            break

    return result
