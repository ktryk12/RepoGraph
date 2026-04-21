from __future__ import annotations

from repograph.shared_retrieval.compressor import compress

from tests.fixtures.builders import make_working_set


def test_compress_reduces_tokens_when_budget_is_exceeded() -> None:
    ws = make_working_set(symbol_count=24, token_budget=4096)

    compressed = compress(ws, budget=220)

    assert compressed.pre_compress_tokens > 220
    assert compressed.post_compress_tokens <= 220
    assert compressed.post_compress_tokens < compressed.pre_compress_tokens
    assert compressed.strategy_applied != "none"


def test_compress_leaves_output_unchanged_when_under_budget() -> None:
    ws = make_working_set(symbol_count=4, token_budget=4096)

    compressed = compress(ws, budget=4096)

    assert compressed.strategy_applied == "none"
    assert compressed.pre_compress_tokens == compressed.post_compress_tokens
    assert [symbol.symbol for symbol in compressed.symbols] == [symbol.symbol for symbol in ws.symbols]
