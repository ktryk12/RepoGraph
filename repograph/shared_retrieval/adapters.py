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
    payload = r.model_dump(exclude_none=True)
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
    payload = {
        "messages": [
            {"role": "system", "content": r.prompt_pack.preamble or "You are a coding assistant."},
            {"role": "user", "content": "\n\n".join(content_parts)},
        ],
        "task_id": r.task_id,
        "token_estimate": r.prompt_pack.total_tokens,
    }
    return _attach_analysis_metadata(payload, r)


def _babyai(r: SharedRetrievalResponse) -> dict:
    """babyAI agent: structured working_set + packed blocks separately."""
    payload = {
        "task_id": r.task_id,
        "task_family": r.task_family,
        "consumer": "babyai_agent",
        "source_mode": r.source_mode,
        "payload_mode": "structured_retrieval_pack",
        "prompt_assembly_owner": "babyai",
        "preamble": r.prompt_pack.preamble,
        "objective": r.prompt_pack.objective,
        "context_blocks": [b.model_dump() for b in r.prompt_pack.context_blocks],
        "working_set": r.working_set,
        "verification_plan": r.verification_plan.model_dump(),
        "verification_plan_available": r.verification_plan_available,
        "retry_pack_available": r.retry_pack_available,
        "retrieval_trace_id": r.retrieval_trace_id,
        "cache": r.cache.model_dump(),
        "task_memory_refs": list(r.task_memory_refs),
        "token_estimate": r.prompt_pack.total_tokens,
    }
    return _attach_analysis_metadata(payload, r)


def _newmodel(r: SharedRetrievalResponse) -> dict:
    """NewModel: same as babyAI but with retrieval_trace_id for attribution."""
    base = _babyai(r)
    base["consumer"] = "newmodel"
    base["prompt_assembly_owner"] = "newmodel"
    return base


def _generic(r: SharedRetrievalResponse) -> dict:
    return r.model_dump(exclude_none=True)


def _attach_analysis_metadata(payload: dict, response: SharedRetrievalResponse) -> dict:
    if response.analysis_step_id:
        payload["analysis_step_id"] = response.analysis_step_id
    if response.analysis_step_kind:
        payload["analysis_step_kind"] = response.analysis_step_kind
    if response.analysis_plan is not None:
        payload["analysis_plan"] = response.analysis_plan.model_dump()
    return payload
