from __future__ import annotations

from repograph.token_budget import BudgetRequest, TokenBudgetEngine, get_engine


def test_resolves_tokenizer_profiles_by_model_family() -> None:
    assert get_engine("gpt-5-codex").profile.name == "openai"
    assert get_engine("claude-sonnet").profile.name == "anthropic"
    assert get_engine("gemini-2.5-pro").profile.name == "gemini"
    assert get_engine("qwen3-coder").profile.name == "local"
    assert get_engine("unknown-model").profile.name == "generic"


def test_registered_exact_counter_replaces_fallback() -> None:
    TokenBudgetEngine.register_counter("anthropic", lambda text: len(text.split()))
    try:
        engine = get_engine("claude-test")
        assert engine.exact is True
        assert engine.count_text("one two three") == 3
    finally:
        TokenBudgetEngine.unregister_counter("anthropic")


def test_budget_subtracts_every_non_retrieval_component() -> None:
    engine = get_engine("generic")
    request = BudgetRequest(
        total_context=1000,
        system_instructions="System rules",
        required_tool_schemas=[{"name": "search", "parameters": {"type": "object"}}],
        active_task_memory={"last_failure": "pytest"},
        code_and_documentation="already supplied code",
        tool_results={"status": "pass"},
        reserved_output_tokens=200,
        safety_margin_tokens=50,
    )

    budget = engine.calculate(request)

    assert set(budget.component_tokens) == {
        "system_instructions",
        "required_tool_schemas",
        "active_task_memory",
        "code_and_documentation",
        "tool_results",
    }
    assert all(tokens > 0 for tokens in budget.component_tokens.values())
    assert budget.available_retrieval_tokens == 1000 - budget.used_non_retrieval_tokens


def test_empty_structured_components_do_not_consume_budget() -> None:
    budget = get_engine().calculate(
        BudgetRequest(total_context=4096, required_tool_schemas=[], active_task_memory={})
    )
    assert budget.available_retrieval_tokens == 4096


def test_truncate_text_obeys_model_profile_budget() -> None:
    engine = get_engine("qwen3-coder")
    text = "def calculate_token_budget(value: str) -> int:\n" * 40

    truncated = engine.truncate_text(text, 30)

    assert truncated
    assert len(truncated) < len(text)
    assert engine.count_text(truncated) <= 30
