from __future__ import annotations

from repograph.shared_retrieval.adapters import format_for_consumer
from repograph.shared_retrieval.models import (
    CacheInfo,
    PromptBlock,
    PromptPack,
    SharedRetrievalResponse,
    VerificationPlan,
)
from tests.fixtures.builders import make_working_set


def _response() -> SharedRetrievalResponse:
    working_set = make_working_set(symbol_count=6, token_budget=6000).model_dump()
    working_set["tokens_used"] = 4120
    return SharedRetrievalResponse(
        task_family="targeted_refactor",
        task_id="task:claude",
        working_set_id="ws:test",
        retrieval_trace_id="trace:test",
        prompt_pack=PromptPack(
            preamble="You are a precise coding assistant.",
            objective="Fix the RepoGraph envelope handoff.",
            context_blocks=[
                PromptBlock(
                    role="context",
                    label="repograph/shared_retrieval/adapters.py",
                    content="Claude Code should keep prompt_pack and working_set intact.",
                    token_estimate=64,
                    why_included="adapter handoff",
                )
            ],
            total_tokens=4280,
            strategy="patch_first",
            target_context=6000,
        ),
        working_set=working_set,
        verification_plan=VerificationPlan(
            tests=["tests/unit/test_consumer_adapters.py"],
            lint=True,
            typecheck=False,
            static_analysis=False,
        ),
        cache=CacheInfo(used=True, keys_hit=["repo:default:/repo:workingset:abc123"]),
        duration_ms=17,
    )


def test_claude_code_format_keeps_prompt_and_structured_envelope() -> None:
    response = _response()

    payload = format_for_consumer(response, "claude_code")

    assert "prompt" in payload
    assert "Fix the RepoGraph envelope handoff." in payload["prompt"]
    assert "### repograph/shared_retrieval/adapters.py" in payload["prompt"]
    assert payload["prompt_pack"]["total_tokens"] == response.prompt_pack.total_tokens
    assert payload["working_set"]["token_budget"] == response.working_set["token_budget"]
    assert payload["verification_plan"] == response.verification_plan.model_dump()
    assert payload["retrieval_trace_id"] == response.retrieval_trace_id
    assert payload["cache"] == response.cache.model_dump()
    assert payload["token_estimate"] == response.prompt_pack.total_tokens


def test_generic_consumer_still_returns_full_model_dump() -> None:
    response = _response()

    payload = format_for_consumer(response, "generic")

    assert payload == response.model_dump()
