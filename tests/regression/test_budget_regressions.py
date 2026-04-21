from __future__ import annotations

import pytest

from repograph.shared_retrieval import SharedRetrievalRequest, prepare_task_context

from tests.fixtures.builders import make_working_set


@pytest.mark.parametrize("target_context", [4096, 6000, 16384, 32768])
def test_prompt_pack_stays_under_requested_budget(
    target_context: int,
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    req = SharedRetrievalRequest(
        repo_path="/repo",
        query="Large coding task with many relevant files",
        task_hint="targeted_refactor",
        output_profile="medium",
        target_context=target_context,
        include_debug=True,
    )
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=80, token_budget=target_context),
    )

    response = prepare_task_context(req, fake_store)

    assert response.prompt_pack.total_tokens <= target_context
    assert response.debug["post_compress_tokens"] <= target_context


@pytest.mark.parametrize("symbol_count", [8, 16, 32, 64, 96])
def test_larger_inputs_do_not_produce_prompt_pack_over_budget(
    symbol_count: int,
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    target_context = 4096
    req = SharedRetrievalRequest(
        repo_path="/repo",
        query="Property style budget test",
        output_profile="patch",
        target_context=target_context,
    )
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=symbol_count, token_budget=target_context),
    )

    response = prepare_task_context(req, fake_store)

    assert response.prompt_pack.total_tokens <= target_context


def test_observability_reports_cache_and_compression_details(
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    req = SharedRetrievalRequest(
        repo_path="/repo",
        query="Observability regression",
        consumer="codex",
        target_context=4096,
        include_debug=True,
    )
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=24, token_budget=req.target_context),
    )

    first = prepare_task_context(req, fake_store)
    second = prepare_task_context(req, fake_store)

    assert first.cache.used is False
    assert second.cache.used is True
    assert second.cache.keys_hit
    assert first.debug["pre_compress_tokens"] >= first.debug["post_compress_tokens"]
    assert fake_tracer[0]["consumer"] == "codex"
    assert fake_tracer[0]["duration_ms"] >= 0
