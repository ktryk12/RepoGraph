"""
skill_runtime/context/context_builder.py - Context packing for skill execution.

Canonical path:
  RepoGraph shared retrieval envelope

Degraded path:
  local symbol/artifact/policy context only
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

_log = logging.getLogger(__name__)
_ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", "artifacts"))


@dataclass
class ContextPack:
    skill_id: str
    user_prompt: str
    repo_symbols: List[str] = field(default_factory=list)
    recent_artifacts: List[Dict[str, Any]] = field(default_factory=list)
    memory_snippets: List[str] = field(default_factory=list)
    policy_refs: List[str] = field(default_factory=list)
    prompt: str = ""
    prompt_pack: Dict[str, Any] = field(default_factory=dict)
    working_set: Dict[str, Any] = field(default_factory=dict)
    verification_plan: Dict[str, Any] = field(default_factory=dict)
    retrieval_trace_id: str = ""
    cache: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "user_prompt": self.user_prompt,
            "repo_symbols": self.repo_symbols,
            "recent_artifacts": self.recent_artifacts,
            "memory_snippets": self.memory_snippets,
            "policy_refs": self.policy_refs,
            "prompt": self.prompt,
            "prompt_pack": self.prompt_pack,
            "working_set": self.working_set,
            "verification_plan": self.verification_plan,
            "retrieval_trace_id": self.retrieval_trace_id,
            "cache": self.cache,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ContextPack":
        return cls(
            skill_id=str(payload.get("skill_id", "")),
            user_prompt=str(payload.get("user_prompt", "")),
            repo_symbols=list(payload.get("repo_symbols", []) or []),
            recent_artifacts=list(payload.get("recent_artifacts", []) or []),
            memory_snippets=list(payload.get("memory_snippets", []) or []),
            policy_refs=list(payload.get("policy_refs", []) or []),
            prompt=str(payload.get("prompt", "") or ""),
            prompt_pack=dict(payload.get("prompt_pack") or {}),
            working_set=dict(payload.get("working_set") or {}),
            verification_plan=dict(payload.get("verification_plan") or {}),
            retrieval_trace_id=str(payload.get("retrieval_trace_id", "") or ""),
            cache=dict(payload.get("cache") or {}),
            extra=dict(payload.get("extra") or {}),
        )

    def token_estimate(self) -> int:
        raw = json.dumps(self.to_dict(), ensure_ascii=True)
        return len(raw) // 4

    @property
    def degraded(self) -> bool:
        return bool(self.extra.get("degraded"))


class ContextBuilder:
    def __init__(self, repograph_client=None) -> None:
        self._rg = repograph_client

    def build(
        self,
        skill_id: str,
        user_prompt: str,
        *,
        manifest: Any | None = None,
        parameters: Dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> ContextPack:
        pack = ContextPack(skill_id=skill_id, user_prompt=user_prompt)
        pack.recent_artifacts = self._recent_artifacts(skill_id)
        pack.policy_refs = self._relevant_policies(skill_id)

        if self._rg:
            try:
                envelope = self._rg.prepare_skill_context(
                    skill_id=skill_id,
                    user_prompt=user_prompt,
                    manifest=manifest,
                    parameters=parameters,
                    trace_id=trace_id,
                    include_debug=False,
                )
                pack.prompt = str(envelope.get("prompt", "") or "")
                pack.prompt_pack = dict(envelope.get("prompt_pack") or {})
                pack.working_set = dict(envelope.get("working_set") or {})
                pack.verification_plan = dict(envelope.get("verification_plan") or {})
                pack.retrieval_trace_id = str(envelope.get("retrieval_trace_id", "") or "")
                pack.cache = dict(envelope.get("cache") or {})
                pack.repo_symbols = _repo_symbols_from_working_set(pack.working_set)
                pack.extra.update(
                    {
                        "degraded": False,
                        "retrieval_mode": "repograph_shared_retrieval",
                        "repograph_consumer": getattr(self._rg, "_consumer", ""),
                        "prompt_assembly_owner": str(envelope.get("prompt_assembly_owner", "") or ""),
                        "payload_mode": str(envelope.get("payload_mode", "") or ""),
                        "source_mode": str(envelope.get("source_mode", "") or "shared_retrieval"),
                        "task_family": str(envelope.get("task_family", "") or ""),
                        "retry_pack_available": bool(envelope.get("retry_pack_available", False)),
                        "verification_plan_available": bool(envelope.get("verification_plan_available", False)),
                        "task_memory_refs": list(envelope.get("task_memory_refs", []) or []),
                        "structured_prompt_assembly": str(envelope.get("prompt_assembly_owner", "") or "") == "babyai",
                    }
                )
                return pack
            except Exception as exc:
                pack.extra.update(
                    {
                        "degraded": True,
                        "retrieval_mode": "degraded_local_context",
                        "degraded_reason": f"repograph_shared_retrieval_failed: {exc}",
                    }
                )
                _log.warning("context_builder_degraded_repograph error=%s", exc)

        self._populate_local_fallback(pack, user_prompt)
        return pack

    def _populate_local_fallback(self, pack: ContextPack, user_prompt: str) -> None:
        if self._rg:
            try:
                words = [word for word in user_prompt.split() if len(word) > 4][:3]
                for word in words:
                    symbols = self._rg.find_capability(word)
                    pack.repo_symbols.extend(symbols[:2])
                pack.repo_symbols = list(dict.fromkeys(pack.repo_symbols))[:10]
            except Exception as exc:
                _log.warning("context_builder_legacy_repograph_error error=%s", exc)

    def _recent_artifacts(self, skill_id: str) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        try:
            skill_dir = _ARTIFACT_DIR / "skills" / skill_id
            if skill_dir.exists():
                for entry in sorted(skill_dir.iterdir(), reverse=True)[:3]:
                    artifacts.append(
                        {
                            "path": str(entry),
                            "modified": entry.stat().st_mtime,
                            "size": entry.stat().st_size,
                        }
                    )
        except Exception:
            pass
        return artifacts

    def _relevant_policies(self, skill_id: str) -> List[str]:
        policy_dir = Path("policy/domain/skills")
        if not policy_dir.exists():
            return []
        return [str(path) for path in policy_dir.glob("*.yaml")][:5]


def _repo_symbols_from_working_set(working_set: Dict[str, Any]) -> List[str]:
    if not isinstance(working_set, dict):
        return []
    collected: list[str] = []
    for key in ("symbols", "top_symbols", "files", "items", "selected"):
        raw_items = working_set.get(key)
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if isinstance(item, dict):
                for field in ("symbol", "path", "fqn", "name", "file"):
                    value = str(item.get(field, "") or "").strip()
                    if value:
                        collected.append(value)
                        break
            else:
                value = str(item).strip()
                if value:
                    collected.append(value)
    return list(dict.fromkeys(collected))[:10]
