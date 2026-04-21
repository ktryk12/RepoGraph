from __future__ import annotations

from repograph.memory.models import PrecisionSignals
from repograph.memory import store as memory_store
from repograph.shared_retrieval import SharedRetrievalRequest, prepare_task_context
from repograph.shared_retrieval.adapters import format_for_consumer

from tests.fixtures.builders import make_working_set


def test_shared_retrieval_pipeline_runs_working_set_compressor_and_prompt_pack(
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    req = SharedRetrievalRequest(
        repo_path="/repo",
        query="Refactor oversized prompt budget handling",
        task_hint="targeted_refactor",
        consumer="generic",
        output_profile="patch",
        target_context=6000,
        include_debug=True,
    )
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=28, token_budget=req.target_context),
    )

    response = prepare_task_context(req, fake_store)

    assert response.prompt_pack.total_tokens <= req.target_context
    assert response.working_set["compression"] in {"none", "drop_calls", "drop_low_summaries", "drop_low_risk"}
    assert response.debug["pre_compress_tokens"] >= response.debug["post_compress_tokens"]
    assert fake_tracer


def test_cache_invalidation_endpoint_deletes_cached_entries(
    api_client,
    fake_redis,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=10),
    )
    body = {"repo_path": "/repo", "query": "Cache me", "target_context": 4096}
    api_client.post("/shared-retrieval/prepare", json=body)

    response = api_client.post("/cache/invalidate", json={"repo_path": "/repo", "tenant_id": "default"})

    assert response.status_code == 200
    assert response.json()["deleted_keys"] >= 1


def test_task_memory_can_be_updated_and_loaded_back(fake_store) -> None:
    record = memory_store.create(
        fake_store,
        query="Fix retry loop",
        task_family="targeted_refactor",
        working_set_id="ws:1",
        retrieval_id="retrieval:1",
    )

    memory_store.update_signals(
        fake_store,
        record.task_id,
        PrecisionSignals(consumer_accepted=True, verification_passed=True),
    )
    loaded = memory_store.get(fake_store, record.task_id)

    assert loaded is not None
    assert loaded.task_id == record.task_id
    assert loaded.signals.consumer_accepted is True
    assert loaded.signals.verification_passed is True


def test_degraded_mode_when_redis_is_unavailable(fake_store, fake_tracer, monkeypatch) -> None:
    req = SharedRetrievalRequest(repo_path="/repo", query="No redis", target_context=4096, include_debug=True)
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=18, token_budget=req.target_context),
    )
    monkeypatch.setattr("repograph.cache.redis_layer.get", lambda key: None)
    monkeypatch.setattr("repograph.cache.redis_layer.set", lambda key, value, ttl: False)

    response = prepare_task_context(req, fake_store)

    assert response.cache.used is False
    assert response.prompt_pack.total_tokens <= req.target_context


def test_degraded_mode_when_router_hint_is_unavailable_still_returns_context(
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    req = SharedRetrievalRequest(
        repo_path="/repo",
        query="Target unavailable router model",
        target_model="missing-router-model",
        target_context=4096,
    )
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=16, token_budget=req.target_context),
    )

    response = prepare_task_context(req, fake_store)

    assert response.prompt_pack.total_tokens <= req.target_context
    assert response.task_family


def test_multiple_consumers_get_expected_shapes(
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    req = SharedRetrievalRequest(repo_path="/repo", query="Consumer routing", target_context=4096)
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=10, token_budget=req.target_context),
    )
    response = prepare_task_context(req, fake_store)

    claude = format_for_consumer(response, "claude_code")
    codex = format_for_consumer(response, "codex")
    babyai = format_for_consumer(response, "babyai_agent")
    generic = format_for_consumer(response, "generic")

    assert "prompt" in claude
    assert "prompt_pack" in claude
    assert "working_set" in claude
    assert "verification_plan" in claude
    assert claude["retrieval_trace_id"] == response.retrieval_trace_id
    assert claude["cache"] == response.cache.model_dump()
    assert "messages" in codex
    assert "working_set" in babyai
    assert "prompt_pack" in generic
