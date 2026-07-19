from __future__ import annotations

from unittest.mock import Mock

from repograph.shared_retrieval.gateway import _build_verification_plan, prepare_task_context
from repograph.token_budget import BudgetRequest, get_engine
from repograph.working_set.models import WorkingSetFile

from tests.fixtures.builders import make_working_set


def test_prepare_task_context_respects_target_context(
    sample_request,
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    sample_request.target_context = 6000
    sample_request.output_profile = "patch"
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=30, token_budget=sample_request.target_context),
    )

    response = prepare_task_context(sample_request, fake_store)

    assert response.prompt_pack.target_context == 6000
    assert response.prompt_pack.total_tokens <= 6000
    assert response.working_set["token_budget"] == 6000


def test_prepare_task_context_cache_hit_returns_cached_response_without_rebuild(
    sample_request,
    fake_store,
    fake_redis,
    monkeypatch,
) -> None:
    cached_response = prepare_task_context(sample_request, fake_store).model_dump()
    fake_redis["state"][next(iter(fake_redis["state"]))] = cached_response
    build_working_set = Mock(side_effect=AssertionError("build_working_set should not run on cache hit"))
    monkeypatch.setattr("repograph.shared_retrieval.gateway.build_working_set", build_working_set)

    response = prepare_task_context(sample_request, fake_store)

    assert response.cache.used is True
    assert response.cache.keys_hit
    build_working_set.assert_not_called()


def test_prepare_task_context_force_refresh_bypasses_cache(
    sample_request,
    fake_store,
    fake_redis,
    monkeypatch,
) -> None:
    cached_response = prepare_task_context(sample_request, fake_store).model_dump()
    fake_redis["state"][next(iter(fake_redis["state"]))] = cached_response
    sample_request.force_refresh = True
    build_calls = {"count": 0}

    def fake_build(**kwargs):
        build_calls["count"] += 1
        return make_working_set(symbol_count=12, token_budget=kwargs["token_budget"])

    monkeypatch.setattr("repograph.shared_retrieval.gateway.build_working_set", fake_build)

    response = prepare_task_context(sample_request, fake_store)

    assert build_calls["count"] == 1
    assert response.cache.used is False


def test_prepare_task_context_logs_trace_with_pre_and_post_compress_tokens(
    sample_request,
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=24, token_budget=sample_request.target_context),
    )

    prepare_task_context(sample_request, fake_store)

    assert len(fake_tracer) == 1
    entry = fake_tracer[0]
    assert entry["token_budget"] == sample_request.target_context
    assert entry["pre_compress_tokens"] >= entry["post_compress_tokens"]
    assert entry["consumer"] == sample_request.consumer


def test_prepare_task_context_uses_central_budget_and_logs_identity_dimensions(
    sample_request,
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    sample_request.target_model = "claude-sonnet"
    sample_request.session_id = "session-1"
    sample_request.adapter_version = "v2"
    sample_request.repo_revision = "abc123"
    sample_request.content_hash = "content456"
    sample_request.system_instructions = "Keep changes minimal"
    sample_request.required_tool_schemas = [{"name": "pytest"}]
    sample_request.reserved_output_tokens = 500
    sample_request.safety_margin_tokens = 100
    captured: dict = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return make_working_set(symbol_count=12, token_budget=kwargs["token_budget"])

    monkeypatch.setattr("repograph.shared_retrieval.gateway.build_working_set", fake_build)

    response = prepare_task_context(sample_request, fake_store)
    expected = get_engine(sample_request.target_model).calculate(
        BudgetRequest(
            total_context=sample_request.target_context,
            target_model=sample_request.target_model,
            system_instructions=sample_request.system_instructions,
            required_tool_schemas=sample_request.required_tool_schemas,
            reserved_output_tokens=500,
            safety_margin_tokens=100,
        )
    )

    assert captured["token_budget"] == expected.available_retrieval_tokens
    assert captured["target_model"] == "claude-sonnet"
    assert response.prompt_pack.target_context == expected.available_retrieval_tokens
    assert fake_tracer[0]["repo_revision"] == "abc123"
    assert fake_tracer[0]["content_hash"] == "content456"
    assert fake_tracer[0]["session_id"] == "session-1"
    assert fake_tracer[0]["adapter_version"] == "v2"
    assert fake_tracer[0]["tokenizer_profile"] == "anthropic"


def test_cache_hit_logs_reused_and_cache_saved_tokens(
    sample_request,
    fake_store,
    fake_redis,
    fake_tracer,
) -> None:
    first = prepare_task_context(sample_request, fake_store)
    second = prepare_task_context(sample_request, fake_store)

    assert second.cache.used is True
    assert len(fake_tracer) == 2
    cache_trace = fake_tracer[-1]
    assert cache_trace["cache_hit"] is True
    assert cache_trace["reused_tokens"] == first.prompt_pack.total_tokens
    assert cache_trace["cache_saved_tokens"] == first.prompt_pack.total_tokens


def test_verification_plan_identifies_tests_and_avoids_false_positives() -> None:
    ws = make_working_set(symbol_count=4)
    ws.files.extend(
        [
            WorkingSetFile(filepath="tests/test_budget.py", file_summary="real test"),
            WorkingSetFile(filepath="src/contest.py", file_summary="not a test"),
            WorkingSetFile(filepath="pkg/module_test.py", file_summary="real suffix test"),
        ]
    )

    plan = _build_verification_plan(ws)

    assert plan.lint is True
    assert "tests/test_budget.py" in plan.tests
    assert "pkg/module_test.py" in plan.tests
    assert "src/contest.py" not in plan.tests


def test_prepare_task_context_adds_analysis_plan_for_broad_analyze_queries(
    sample_request,
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    sample_request.query = "analyze the code and understand this repo"
    sample_request.output_profile = "review"
    sample_request.target_context = 8192
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **kwargs: make_working_set(
            symbol_count=18,
            token_budget=kwargs["token_budget"],
            task_family=kwargs.get("task_hint") or "targeted_refactor",
            query=kwargs["query"],
        ),
    )

    response = prepare_task_context(sample_request, fake_store)

    assert response.analysis_plan is not None
    assert len(response.analysis_plan.steps) >= 8
    assert response.analysis_step_id == response.analysis_plan.steps[0].step_id
    assert response.prompt_pack.total_tokens <= response.prompt_pack.target_context


def test_prepare_task_context_can_materialize_one_analysis_step_at_a_time(
    sample_request,
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    sample_request.query = "analyze the code and understand this repo"
    sample_request.output_profile = "review"
    sample_request.target_context = 8192
    sample_request.analysis_step_id = "step_entrypoints"
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **kwargs: make_working_set(
            symbol_count=14,
            token_budget=kwargs["token_budget"],
            task_family=kwargs.get("task_hint") or "targeted_refactor",
            query=kwargs["query"],
        ),
    )

    response = prepare_task_context(sample_request, fake_store)

    assert response.analysis_plan is not None
    assert response.analysis_step_id == "step_entrypoints"
    assert response.analysis_step_kind == "entrypoints_execution_flow"
    assert response.prompt_pack.total_tokens <= response.prompt_pack.target_context
    assert response.prompt_pack.target_context <= sample_request.target_context
