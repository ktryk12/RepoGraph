"""Tests for natural-language query handling in coarse retrieval."""
from __future__ import annotations

from repograph.retrieval.coarse_retriever import _query_terms, _search, coarse_retrieve


class FakeStore:
    """Substring-matching store like RepoGraph.search, over a fixed symbol set."""

    def __init__(self, symbols: list[str]):
        self.symbols = symbols

    def search(self, query: str, limit: int = 20) -> list[str]:
        lowered = query.lower()
        return [s for s in self.symbols if lowered in s.lower()][:limit]


SYMBOLS = [
    "indexer.parser.parse_file",
    "indexer.parser.ParseState.add_triple",
    "indexer.walker.walk",
    "api.routes.index_repo",
    "graph.store.RepoGraph.search",
]


def test_query_terms_drops_stopwords_and_prioritizes_identifiers():
    terms = _query_terms("explain how parse_file works in the indexer")
    assert terms[0] == "parse_file"
    assert "indexer" in terms
    assert "the" not in terms
    assert "how" not in terms
    assert "explain" not in terms


def test_search_whole_query_still_wins_when_it_matches():
    store = FakeStore(SYMBOLS)
    assert _search(store, "parse_file", 10) == ["indexer.parser.parse_file"]


def test_search_natural_language_falls_back_to_terms():
    store = FakeStore(SYMBOLS)
    result = _search(store, "explain parse_file in the indexer", 10)
    # parse_file + indexer both hit indexer.parser.parse_file → ranked first
    assert result[0] == "indexer.parser.parse_file"
    assert len(result) >= 2


def test_coarse_retrieve_symbol_lookup_uses_term_search():
    store = FakeStore(SYMBOLS)
    result = coarse_retrieve("how does the walker walk files", "symbol_lookup", store)
    assert "indexer.walker.walk" in result


def test_search_no_terms_returns_empty():
    store = FakeStore(SYMBOLS)
    assert _search(store, "how does it do that", 10) == []
