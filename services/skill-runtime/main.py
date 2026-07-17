"""
Compatibility shim for the historical skill-runtime module path.

The active implementation lives under services/tool-platform/src/skills/, but
existing tests and older integration points still load this module directly.
This shim keeps the old path working while letting babyAI own final prompt
assembly from the structured RepoGraph payload.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict

from skill_runtime.context.context_builder import ContextBuilder
from skill_runtime.context.repograph_client import RepographClient
from skill_runtime.executor.expert_client import complete
from skill_runtime.executor.output_parser import parse
from skill_runtime.executor.prompt_assembler import assemble


class _NullCache:
    def get(self, skill_id: str, prompt: str) -> dict[str, Any] | None:
        return None

    def set(self, skill_id: str, prompt: str, pack: dict[str, Any]) -> None:
        return None


class _NullTelemetry:
    def build_event(self, **kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    def emit(self, event: dict[str, Any]) -> None:
        return None


_builder = ContextBuilder(repograph_client=RepographClient())
_cache = _NullCache()
_telemetry = _NullTelemetry()


def _render_messages_prompt(messages: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = str(message.get("role", "user") or "user").upper()
        content = str(message.get("content", "") or "").strip()
        parts.append(f"### {role}:\n{content}")
    parts.append("### ASSISTANT:\n")
    return "\n\n".join(parts)


def _max_tokens_for_manifest(manifest: Any) -> int:
    expert_routing = getattr(manifest, "expert_routing", {}) or {}
    requested = int(expert_routing.get("max_tokens", 512) or 512)
    return min(requested, 1024)


def _temperature_for_manifest(manifest: Any) -> float:
    expert_routing = getattr(manifest, "expert_routing", {}) or {}
    return float(expert_routing.get("temperature", 0.3) or 0.3)


def _model_for_manifest(manifest: Any) -> str:
    expert_routing = getattr(manifest, "expert_routing", {}) or {}
    return str(expert_routing.get("model", "general") or "general")


def _parsed_to_dict(parsed: Any) -> dict[str, Any]:
    if is_dataclass(parsed):
        return asdict(parsed)
    if hasattr(parsed, "__dict__"):
        return dict(parsed.__dict__)
    return {"raw": str(parsed)}


def _execute_skill(
    *,
    manifest: Any,
    user_input: str,
    parameters: Dict[str, Any] | None = None,
    trace_id: str | None = None,
) -> dict[str, Any]:
    context = _builder.build(
        str(getattr(manifest, "skill_id", "") or getattr(manifest, "name", "skill")),
        user_input,
        manifest=manifest,
        parameters=parameters,
        trace_id=trace_id,
    )
    messages = assemble(manifest=manifest, context=context, user_input=user_input, parameters=parameters)
    prompt = _render_messages_prompt(messages)

    cached = _cache.get(str(getattr(manifest, "skill_id", "") or getattr(manifest, "name", "skill")), prompt)
    if cached is not None:
        return dict(cached)

    completion = complete(
        model=_model_for_manifest(manifest),
        messages=messages,
        max_tokens=_max_tokens_for_manifest(manifest),
        temperature=_temperature_for_manifest(manifest),
        prompt=prompt,
        prompt_pack=context.prompt_pack,
        working_set=context.working_set,
        verification_plan=context.verification_plan,
        retrieval_trace_id=context.retrieval_trace_id,
        cache=context.cache,
        system_prompt=messages[0]["content"] if messages else "",
    )
    parsed = parse(completion.text)
    result = {
        "status": getattr(parsed, "status", "success"),
        "structured": getattr(parsed, "structured", {}),
        "parsed_output": _parsed_to_dict(parsed),
        "raw_output": completion.text,
        "completion_mode": completion.mode,
        "completion_endpoint": completion.endpoint,
        "retrieval_mode": str(context.extra.get("retrieval_mode", "") or ""),
        "retrieval_trace_id": context.retrieval_trace_id,
        "prompt_assembly_owner": str(context.extra.get("prompt_assembly_owner", "") or "babyai"),
    }
    _cache.set(str(getattr(manifest, "skill_id", "") or getattr(manifest, "name", "skill")), prompt, result)
    _telemetry.emit(
        _telemetry.build_event(
            trace_id=trace_id,
            skill_id=str(getattr(manifest, "skill_id", "") or getattr(manifest, "name", "skill")),
            retrieval_mode=result["retrieval_mode"],
            completion_mode=result["completion_mode"],
        )
    )
    return result
