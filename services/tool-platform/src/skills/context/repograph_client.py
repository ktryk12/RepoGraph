"""
skill_runtime/context/repograph_client.py - Canonical RepoGraph shared retrieval client.

Default path:
  skill-runtime -> RepoGraph /shared-retrieval/prepare

Legacy/degraded path:
  context-plane /search helpers used only when shared retrieval is unavailable.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional
from urllib.request import Request, urlopen

_log = logging.getLogger(__name__)

_REPOGRAPH_URL = os.getenv("REPOGRAPH_URL", "http://host.docker.internal:8001").rstrip("/")
_REPOGRAPH_REPO_PATH = os.getenv("REPOGRAPH_REPO_PATH", os.getenv("BABYAI_REPO_PATH", "/app"))
# babyAI should receive the structured retrieval pack and assemble the final prompt locally.
_REPOGRAPH_CONSUMER = os.getenv("REPOGRAPH_CONSUMER", "babyai_agent")
_REPOGRAPH_TIMEOUT = float(os.getenv("REPOGRAPH_TIMEOUT_SECONDS", "8"))

_CONTEXT_PLANE_URL = os.getenv("CONTEXT_PLANE_BASE_URL", "http://localhost:8092").rstrip("/")
_LEGACY_TIMEOUT = float(os.getenv("CONTEXT_PLANE_TIMEOUT_SECONDS", "8"))

RequestFn = Callable[[str, str, Dict[str, Any], float], Dict[str, Any]]


def _default_request(method: str, url: str, payload: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=True).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json"}, method=method.upper())
    with urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
        if not isinstance(data, dict):
            raise ValueError(f"expected JSON object from {url}")
        return data


def _render_prompt(prompt_pack: Dict[str, Any]) -> str:
    if not isinstance(prompt_pack, dict):
        return ""
    sections: list[str] = []
    preamble = str(prompt_pack.get("preamble", "") or "").strip()
    objective = str(prompt_pack.get("objective", "") or "").strip()
    if preamble:
        sections.append(preamble)
    if objective:
        sections.append(objective)
    for block in prompt_pack.get("context_blocks", []) or []:
        if not isinstance(block, dict):
            continue
        label = str(block.get("label", "") or "").strip()
        content = str(block.get("content", "") or "").strip()
        if not content:
            continue
        sections.append(f"### {label}\n{content}" if label else content)
    return "\n\n".join(section for section in sections if section)


class RepographClient:
    """Canonical RepoGraph client with degraded legacy helpers."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        repo_path: str | None = None,
        consumer: str | None = None,
        request_fn: RequestFn | None = None,
        context_plane_url: str | None = None,
    ) -> None:
        self._base_url = (base_url or _REPOGRAPH_URL).rstrip("/")
        self._repo_path = repo_path or _REPOGRAPH_REPO_PATH
        self._consumer = consumer or _REPOGRAPH_CONSUMER
        self._request_fn = request_fn or _default_request
        self._context_plane_url = (context_plane_url or _CONTEXT_PLANE_URL).rstrip("/")

    def prepare_skill_context(
        self,
        *,
        skill_id: str,
        user_prompt: str,
        manifest: Any | None = None,
        parameters: Dict[str, Any] | None = None,
        trace_id: str | None = None,
        include_debug: bool = False,
    ) -> Dict[str, Any]:
        output_profile = _infer_output_profile(skill_id, manifest)
        request_payload = {
            "repo_path": self._repo_path,
            "query": _query_text(user_prompt, parameters),
            "task_hint": _task_hint(skill_id, manifest),
            "consumer": self._consumer,
            "task_id": trace_id or f"skill-runtime:{skill_id}",
            "target_model": getattr(manifest, "model", None) or "general",
            "target_context": _target_context_for_profile(output_profile),
            "output_profile": output_profile,
            "include_debug": include_debug,
        }
        response = self._request_fn(
            "POST",
            f"{self._base_url}/shared-retrieval/prepare",
            request_payload,
            _REPOGRAPH_TIMEOUT,
        )
        normalized = _normalize_prepare_response(response, consumer=request_payload["consumer"])
        normalized.setdefault("task_id", request_payload["task_id"])
        normalized.setdefault("output_profile", output_profile)
        _log.info(
            "repograph_shared_retrieval_ok skill=%s consumer=%s trace=%s profile=%s",
            skill_id,
            request_payload["consumer"],
            normalized.get("retrieval_trace_id", ""),
            output_profile,
        )
        return normalized

    # ------------------------------------------------------------------
    # Legacy/degraded-only helpers
    # ------------------------------------------------------------------

    def callers_of(self, symbol: str) -> List[str]:
        r = self._legacy_query(f"Who calls {symbol}?")
        return [x.get("symbol", "") for x in r.get("results", [])]

    def callees_of(self, symbol: str) -> List[str]:
        r = self._legacy_query(f"What does {symbol} call?")
        return [x.get("symbol", "") for x in r.get("results", [])]

    def blast_radius(self, symbol: str) -> List[str]:
        r = self._legacy_query(f"Blast radius of {symbol}")
        return [x.get("symbol", "") for x in r.get("results", [])]

    def find_symbol(self, name: str) -> Optional[Dict[str, Any]]:
        r = self._legacy_query(f"Find symbol {name}")
        results = r.get("results", [])
        return results[0] if results else None

    def consumers_of_topic(self, topic: str) -> List[str]:
        r = self._legacy_query(f"Which agents consume Kafka topic '{topic}'?")
        return [x.get("module", "") for x in r.get("results", [])]

    def producers_of_topic(self, topic: str) -> List[str]:
        r = self._legacy_query(f"Which modules produce Kafka topic '{topic}'?")
        return [x.get("module", "") for x in r.get("results", [])]

    def imports_of(self, module: str) -> List[str]:
        r = self._legacy_query(f"What does {module} import?")
        return [x.get("module", "") for x in r.get("results", [])]

    def dependents_of(self, module: str) -> List[str]:
        r = self._legacy_query(f"What imports {module}?")
        return [x.get("module", "") for x in r.get("results", [])]

    def services_on_port(self, port: int) -> List[str]:
        r = self._legacy_query(f"Which service listens on port {port}?")
        return [x.get("service", "") for x in r.get("results", [])]

    def find_capability(self, capability: str) -> List[str]:
        r = self._legacy_query(f"Does a component with capability '{capability}' exist?")
        return [x.get("symbol", x.get("module", "")) for x in r.get("results", [])]

    def skill_mds(self) -> List[str]:
        r = self._legacy_query("List all SKILL.md files")
        return [x.get("path", "") for x in r.get("results", [])]

    def artifact_writers(self) -> List[str]:
        r = self._legacy_query("Which modules write artifacts via artifact-writer?")
        return [x.get("module", "") for x in r.get("results", [])]

    def policy_files(self) -> List[str]:
        r = self._legacy_query("List all policy YAML files")
        return [x.get("path", "") for x in r.get("results", [])]

    def agents_emitting(self, event: str) -> List[str]:
        r = self._legacy_query(f"Which agents emit event '{event}'?")
        return [x.get("agent", "") for x in r.get("results", [])]

    def reuse_report(self, capability: str) -> Dict[str, Any]:
        return self._legacy_query(f"Reuse report for capability: {capability}")

    def _legacy_query(self, query: str) -> Dict[str, Any]:
        try:
            _log.warning("repograph_legacy_context_plane_query query=%s", query[:80])
            return self._request_fn(
                "POST",
                f"{self._context_plane_url}/search",
                {"query": query},
                _LEGACY_TIMEOUT,
            )
        except Exception as exc:
            _log.warning("repograph_legacy_query_error query=%s error=%s", query[:60], exc)
            return {"results": [], "error": str(exc)}


def _query_text(user_prompt: str, parameters: Dict[str, Any] | None) -> str:
    prompt = str(user_prompt or "").strip()
    if not parameters:
        return prompt
    rendered = "\n".join(f"- {key}: {value}" for key, value in sorted(parameters.items()))
    return f"{prompt}\n\nParameters:\n{rendered}"


def _task_hint(skill_id: str, manifest: Any | None) -> str:
    description = str(getattr(manifest, "description", "") or "").strip()
    name = str(getattr(manifest, "name", skill_id) or skill_id).strip()
    return f"{name}: {description}" if description else name


def _infer_output_profile(skill_id: str, manifest: Any | None) -> str:
    parts = [
        skill_id,
        str(getattr(manifest, "name", "") or ""),
        str(getattr(manifest, "description", "") or ""),
        " ".join(getattr(manifest, "domains", []) or []),
        " ".join(getattr(manifest, "dimensions", []) or []),
    ]
    haystack = " ".join(part.lower() for part in parts if part)
    if any(token in haystack for token in ("review", "finding", "audit", "analyse", "analysis")):
        return "review"
    if any(token in haystack for token in ("patch", "repair", "fix", "refactor", "edit", "rewrite")):
        return "patch"
    return "small"


def _target_context_for_profile(profile: str) -> int:
    if profile == "review":
        return 16384
    if profile == "patch":
        return 6000
    return 8192


def _normalize_prepare_response(response: Dict[str, Any], *, consumer: str) -> Dict[str, Any]:
    prompt_pack = dict(response.get("prompt_pack") or {})
    payload_mode = str(response.get("payload_mode", "") or "").strip()
    prompt_assembly_owner = str(response.get("prompt_assembly_owner", "") or "").strip()
    structured_first = payload_mode == "structured_retrieval_pack" or prompt_assembly_owner == "babyai"
    normalized = {
        "prompt": "" if structured_first else str(response.get("prompt", "") or _render_prompt(prompt_pack)),
        "prompt_pack": prompt_pack,
        "working_set": dict(response.get("working_set") or {}),
        "verification_plan": dict(response.get("verification_plan") or {}),
        "retrieval_trace_id": str(response.get("retrieval_trace_id", "") or ""),
        "cache": dict(response.get("cache") or {}),
        "consumer": str(response.get("consumer", "") or consumer),
        "source_mode": str(response.get("source_mode", "") or "shared_retrieval"),
        "payload_mode": payload_mode or "retrieval_envelope",
        "prompt_assembly_owner": prompt_assembly_owner or ("babyai" if consumer == "babyai_agent" else "consumer"),
        "retry_pack_available": bool(response.get("retry_pack_available", False)),
        "verification_plan_available": bool(response.get("verification_plan_available", bool(response.get("verification_plan")))),
        "task_family": str(response.get("task_family", "") or ""),
        "task_memory_refs": list(response.get("task_memory_refs", []) or []),
        "debug": dict(response.get("debug") or {}),
    }
    missing = [
        field
        for field in ("prompt_pack", "working_set", "verification_plan", "retrieval_trace_id", "cache")
        if not normalized[field]
    ]
    if missing:
        raise ValueError(f"RepoGraph response missing canonical envelope fields: {', '.join(missing)}")
    return normalized
