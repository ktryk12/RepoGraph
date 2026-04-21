"""Consumer adapters — thin formatting layer per consumer type."""

from __future__ import annotations

from .models import SharedRetrievalResponse


def format_for_consumer(response: SharedRetrievalResponse, consumer: str) -> dict:
    """Return a consumer-specific dict view of the response."""
    match consumer:
        case "claude_code":
            return _claude_code(response)
        case "codex":
            return _codex(response)
        case "babyai_agent":
            return _babyai(response)
        case "newmodel":
            return _newmodel(response)
        case _:
            return _generic(response)


# ---------------------------------------------------------------------------
# Per-consumer formatters
# ---------------------------------------------------------------------------

def _claude_code(r: SharedRetrievalResponse) -> dict:
    """Claude Code keeps a flat prompt, but must preserve the full envelope."""
    sections = [r.prompt_pack.preamble, r.prompt_pack.objective]
    for block in r.prompt_pack.context_blocks:
        sections.append(f"### {block.label}\n{block.content}")
    payload = r.model_dump()
    payload["prompt"] = "\n\n".join(section for section in sections if section)
    payload["prompt_pack"] = r.prompt_pack.model_dump()
    payload["working_set"] = dict(r.working_set)
    payload["verification_plan"] = r.verification_plan.model_dump()
    payload["retrieval_trace_id"] = r.retrieval_trace_id
    payload["cache"] = r.cache.model_dump()
    payload["token_estimate"] = r.prompt_pack.total_tokens
    return payload


def _codex(r: SharedRetrievalResponse) -> dict:
    """Codex/OpenAI style: messages list."""
    content_parts = [r.prompt_pack.objective]
    for block in r.prompt_pack.context_blocks:
        content_parts.append(block.content)
    return {
        "messages": [
            {"role": "system", "content": r.prompt_pack.preamble or "You are a coding assistant."},
            {"role": "user", "content": "\n\n".join(content_parts)},
        ],
        "task_id": r.task_id,
        "token_estimate": r.prompt_pack.total_tokens,
    }


def _babyai(r: SharedRetrievalResponse) -> dict:
    """babyAI agent: structured working_set + packed blocks separately."""
    return {
        "task_id": r.task_id,
        "task_family": r.task_family,
        "preamble": r.prompt_pack.preamble,
        "objective": r.prompt_pack.objective,
        "context_blocks": [b.model_dump() for b in r.prompt_pack.context_blocks],
        "working_set": r.working_set,
        "verification_plan": r.verification_plan.model_dump(),
        "token_estimate": r.prompt_pack.total_tokens,
    }


def _newmodel(r: SharedRetrievalResponse) -> dict:
    """NewModel: same as babyAI but with retrieval_trace_id for attribution."""
    base = _babyai(r)
    base["retrieval_trace_id"] = r.retrieval_trace_id
    base["cache"] = r.cache.model_dump()
    return base


def _generic(r: SharedRetrievalResponse) -> dict:
    return r.model_dump()
