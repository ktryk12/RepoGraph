"""
skill_runtime/executor/expert_client.py - Canonical llm-server completion client.

Default path:
  skill-runtime -> llm-server (/llm/completion or /models/llm/completion)

Legacy/degraded path:
  direct llama.cpp /completion against model containers
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, List
from urllib.request import Request, urlopen

_log = logging.getLogger(__name__)

_TIMEOUT = float(os.getenv("SKILL_EXPERT_TIMEOUT", "120"))
_LLM_GATEWAY_URL = os.getenv("LLM_GATEWAY_URL", "http://host.docker.internal:8112").rstrip("/")
_LLM_COMPLETION_PATH = os.getenv("LLM_COMPLETION_PATH", "/models/llm/completion")

_LEGACY_ENDPOINTS = {
    "code-codestral": os.getenv("LLM_CODE_URL", "http://code-qwen-code-qwen-1:8000").rstrip("/"),
    "danish": os.getenv("LLM_DANISH_URL", "http://llm-server-glm-5.1-1:8000").rstrip("/"),
    "general": os.getenv("LLM_GENERAL_URL", "http://llm-server-glm-5.1-1:8000").rstrip("/"),
    "vision": os.getenv("LLM_VISION_URL", "http://llm-server-glm-5.1-1:8000").rstrip("/"),
}
_LEGACY_FALLBACK = os.getenv("LLM_FALLBACK_URL", "http://mixtral-mixtral-1:8000").rstrip("/")

RequestFn = Callable[[str, str, Dict[str, Any], float], Dict[str, Any]]


@dataclass
class CompletionResult:
    text: str
    mode: str
    endpoint: str


def _default_request(method: str, url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=True).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method.upper())
    with urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
        if not isinstance(data, dict):
            raise ValueError(f"expected JSON object from {url}")
        return data


def _messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    parts = []
    for message in messages:
        role = message.get("role", "user").upper()
        parts.append(f"### {role}:\n{message.get('content', '')}")
    parts.append("### ASSISTANT:\n")
    return "\n\n".join(parts)


def complete(
    *,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int = 512,
    temperature: float = 0.3,
    prompt: str = "",
    prompt_pack: Dict[str, Any] | None = None,
    working_set: Dict[str, Any] | None = None,
    verification_plan: Dict[str, Any] | None = None,
    retrieval_trace_id: str = "",
    cache: Dict[str, Any] | None = None,
    system_prompt: str = "",
    history: List[Dict[str, Any]] | None = None,
    tool_outputs: Any = None,
    request_fn: RequestFn | None = None,
) -> CompletionResult:
    request = request_fn or _default_request
    canonical_url = f"{_LLM_GATEWAY_URL}{_LLM_COMPLETION_PATH}"
    canonical_payload = _canonical_payload(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
        prompt=prompt,
        prompt_pack=prompt_pack or {},
        working_set=working_set or {},
        verification_plan=verification_plan or {},
        retrieval_trace_id=retrieval_trace_id,
        cache=cache or {},
        system_prompt=system_prompt,
        history=history or [],
        tool_outputs=tool_outputs,
    )

    try:
        response = request("POST", canonical_url, canonical_payload, _TIMEOUT)
        text = _extract_text(response)
        if text:
            _log.info("expert_client_canonical_ok model=%s endpoint=%s chars=%d", model, canonical_url, len(text))
            return CompletionResult(text=text.strip(), mode="canonical_llm_server", endpoint=canonical_url)
        raise ValueError("canonical llm-server returned empty completion")
    except Exception as exc:
        _log.warning("expert_client_canonical_degraded model=%s endpoint=%s error=%s", model, canonical_url, exc)

    legacy_payload = {
        "prompt": prompt or _messages_to_prompt(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stop": ["### USER:", "### SYSTEM:"],
    }
    for url_base in _legacy_targets(model):
        legacy_url = f"{url_base}/completion"
        try:
            response = request("POST", legacy_url, legacy_payload, _TIMEOUT)
            text = _extract_text(response)
            if text:
                _log.warning("expert_client_legacy_ok model=%s endpoint=%s chars=%d", model, legacy_url, len(text))
                return CompletionResult(
                    text=text.strip(),
                    mode="degraded_legacy_completion",
                    endpoint=legacy_url,
                )
        except Exception as exc:
            _log.warning("expert_client_legacy_attempt model=%s endpoint=%s error=%s", model, legacy_url, exc)

    _log.error("expert_client_error model=%s all_attempts_failed", model)
    return CompletionResult(
        text="[expert_client_error: canonical and legacy completion paths failed]",
        mode="error",
        endpoint=canonical_url,
    )


def _canonical_payload(
    *,
    model: str,
    messages: List[Dict[str, str]],
    max_tokens: int,
    temperature: float,
    prompt: str,
    prompt_pack: Dict[str, Any],
    working_set: Dict[str, Any],
    verification_plan: Dict[str, Any],
    retrieval_trace_id: str,
    cache: Dict[str, Any],
    system_prompt: str,
    history: List[Dict[str, Any]],
    tool_outputs: Any,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "prompt": prompt or _messages_to_prompt(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "_capability_hint": _capability_hint(model),
    }
    if prompt_pack:
        payload["prompt_pack"] = prompt_pack
    if working_set:
        payload["working_set"] = working_set
    if verification_plan:
        payload["verification_plan"] = verification_plan
    if retrieval_trace_id:
        payload["retrieval_trace_id"] = retrieval_trace_id
    if cache:
        payload["cache"] = cache
    if system_prompt:
        payload["system_prompt"] = system_prompt
    if history:
        payload["history"] = history
    if tool_outputs is not None:
        payload["tool_outputs"] = tool_outputs
    return payload


def _extract_text(response: Dict[str, Any]) -> str:
    for key in ("content", "text", "response"):
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value

    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, dict):
            text = choice.get("text")
            if isinstance(text, str) and text.strip():
                return text
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content

    return ""


def _capability_hint(model: str) -> str:
    normalized = str(model or "general").strip().lower()
    if normalized == "code-codestral":
        return "code"
    if normalized in {"danish", "general"}:
        return normalized
    if normalized == "vision":
        return "general"
    return "general"


def _legacy_targets(model: str) -> List[str]:
    base = _LEGACY_ENDPOINTS.get(model, _LEGACY_ENDPOINTS["general"])
    return [base, _LEGACY_FALLBACK]
