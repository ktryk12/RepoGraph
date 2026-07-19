"""summary_first must not produce an empty pack on a fresh graph (no summaries)."""
from __future__ import annotations

from repograph.shared_retrieval.profiles import resolve_profile
from repograph.shared_retrieval.prompt_packer import pack

from tests.fixtures.builders import make_working_set


def _strip_summaries(ws):
    """Simulate a freshly indexed repo: no consumer-written summaries yet."""
    symbols = [s.model_copy(update={"summary": None}) for s in ws.symbols]
    files = [f.model_copy(update={"file_summary": None}) for f in ws.files]
    return ws.model_copy(update={"symbols": symbols, "files": files})


def test_tiny_profile_falls_back_to_symbol_map_without_summaries():
    ws = _strip_summaries(make_working_set(symbol_count=10))
    prompt_pack = pack(ws, resolve_profile("tiny"))

    assert prompt_pack.context_blocks, "pack must not be empty when working set has symbols"
    assert prompt_pack.total_tokens <= prompt_pack.target_context


def test_small_profile_still_packs_signatures_without_summaries():
    ws = _strip_summaries(make_working_set(symbol_count=10))
    prompt_pack = pack(ws, resolve_profile("small"))

    assert prompt_pack.context_blocks
