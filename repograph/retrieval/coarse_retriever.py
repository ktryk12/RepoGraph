"""Coarse retrieval — first-pass broad candidate set per task family."""

from __future__ import annotations

import re

from repograph.graph.factory import GraphStore
from repograph.indexer.schema import BELONGS_TO_SERVICE, IN_FILE, TESTS

# Natural-language filler that never contributes to symbol matching. Kept small
# and code-oriented on purpose — words like "file"/"parse" DO appear in symbols.
_STOPWORDS = {
    "the", "a", "an", "in", "on", "of", "for", "to", "and", "or", "is", "are",
    "was", "were", "be", "been", "it", "its", "at", "by", "from", "as", "that",
    "this", "these", "those", "with", "without", "into", "about", "over",
    "how", "does", "do", "did", "can", "could", "should", "would", "will",
    "what", "where", "when", "which", "why", "who", "whose",
    "explain", "show", "find", "describe", "tell", "give", "list", "please",
    "me", "my", "we", "our", "you", "your", "us", "i", "not", "no", "all",
}


def _query_terms(query: str) -> list[str]:
    """Extract identifier-like search terms from a natural-language query."""
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", query)
    terms: list[str] = []
    for token in tokens:
        lowered = token.lower()
        if lowered in _STOPWORDS or len(lowered) < 3:
            continue
        if lowered not in terms:
            terms.append(lowered)
    # Identifier-looking tokens (snake_case / dotted) are the strongest signals.
    terms.sort(key=lambda t: ("_" not in t and "." not in t))
    return terms


def _search(store: GraphStore, query: str, limit: int) -> list[str]:
    """Whole-query search first; fall back to per-term search ranked by hit count.

    ``store.search`` is a plain substring match, so a full natural-language
    sentence ("explain parse_file in the indexer") matches nothing. Splitting
    into terms lets each meaningful token vote on candidate symbols.
    """
    hits = store.search(query, limit=limit)
    if hits:
        return hits

    scores: dict[str, int] = {}
    first_seen: dict[str, int] = {}
    for term in _query_terms(query)[:8]:
        for position, symbol in enumerate(store.search(term, limit=limit)):
            scores[symbol] = scores.get(symbol, 0) + 1
            first_seen.setdefault(symbol, position)

    ranked = sorted(scores, key=lambda s: (-scores[s], first_seen[s], s))
    return ranked[:limit]


def coarse_retrieve(
    query: str,
    task_family: str,
    store: GraphStore,
    limit: int = 40,
) -> list[str]:
    """Return a broad candidate set of symbol IDs for the given task family."""
    match task_family:
        case "symbol_lookup":
            return _search(store, query, limit)

        case "file_to_symbol_map":
            # query is expected to be a file path
            symbols = store.file_symbols(query)
            if not symbols:
                symbols = _search(store, query, limit)
            return symbols[:limit]

        case "bug_localization":
            # Broad search + expand to callers
            seeds = _search(store, query, limit // 2)
            callers = []
            for s in seeds[:5]:
                callers.extend(store.callers_of(s)[:4])
            return _dedupe(seeds + callers, limit)

        case "call_chain_reasoning":
            seeds = _search(store, query, 10)
            result = list(seeds)
            for s in seeds[:5]:
                result.extend(store.callees_of(s)[:6])
                result.extend(store.callers_of(s)[:6])
            return _dedupe(result, limit)

        case "blast_radius_analysis":
            seeds = _search(store, query, 5)
            result = list(seeds)
            for s in seeds[:3]:
                affected = store.blast_radius(s, depth=2)
                for deps in affected.values():
                    result.extend(deps)
            return _dedupe(result, limit)

        case "targeted_refactor":
            seeds = _search(store, query, 10)
            result = list(seeds)
            for s in seeds[:5]:
                result.extend(store.callers_of(s)[:4])
                result.extend(store.outgoing(s, IN_FILE)[:2])
            return _dedupe(result, limit)

        case "test_impact_lookup":
            seeds = _search(store, query, 10)
            result = list(seeds)
            for s in seeds[:5]:
                result.extend(store.incoming(s, TESTS)[:8])
            return _dedupe(result, limit)

        case "targeted_test_generation":
            seeds = _search(store, query, 10)
            result = list(seeds)
            for s in seeds[:5]:
                result.extend(store.callees_of(s)[:6])
            return _dedupe(result, limit)

        case "config_dependency_reasoning":
            seeds = _search(store, query, 10)
            result = list(seeds)
            for s in seeds[:5]:
                result.extend(store.outgoing(s, BELONGS_TO_SERVICE)[:4])
            return _dedupe(result, limit)

        case _:
            return _search(store, query, limit)


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
