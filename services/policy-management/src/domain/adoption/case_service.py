from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping
import os

from babyai_shared.storage.safe_paths import safe_segment


@dataclass(frozen=True)
class CaseResolution:
    case_id: str
    source: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": str(self.case_id),
            "source": str(self.source),
        }


class CaseService:
    """
    Central case-scoping service used across Truth/Policy/RAG flows.

    Resolution precedence:
    1) explicit case_id
    2) context fields (case_id/context_id/task_id/run_id/job_id)
    3) env AESA_CASE_ID / CASE_ID
    4) default case id
    """

    _CONTEXT_KEYS = ("case_id", "context_id", "task_id", "run_id", "job_id")

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        default_case_id: str = "global",
    ) -> None:
        self._env_override = env
        self._default_case_id = safe_segment(str(default_case_id or "global"))

    @property
    def default_case_id(self) -> str:
        return str(self._default_case_id)

    def resolve(
        self,
        *,
        case_id: str | None = None,
        context: Mapping[str, Any] | None = None,
        default_case_id: str | None = None,
    ) -> CaseResolution:
        explicit = _optional_text(case_id)
        if explicit:
            return CaseResolution(case_id=safe_segment(explicit), source="explicit")

        if isinstance(context, Mapping):
            for key in self._CONTEXT_KEYS:
                candidate = _optional_text(context.get(key))
                if candidate:
                    return CaseResolution(case_id=safe_segment(candidate), source=f"context:{key}")

        env_case = _optional_text(self._env().get("AESA_CASE_ID") or self._env().get("CASE_ID"))
        if env_case:
            return CaseResolution(case_id=safe_segment(env_case), source="env")

        fallback = _optional_text(default_case_id)
        if fallback:
            return CaseResolution(case_id=safe_segment(fallback), source="fallback")

        return CaseResolution(case_id=self.default_case_id, source="default")

    def resolve_case_id(
        self,
        *,
        case_id: str | None = None,
        context: Mapping[str, Any] | None = None,
        default_case_id: str | None = None,
    ) -> str:
        return self.resolve(
            case_id=case_id,
            context=context,
            default_case_id=default_case_id,
        ).case_id

    def is_default_case(self, case_id: str | None) -> bool:
        token = safe_segment(_optional_text(case_id) or self.default_case_id)
        return token == self.default_case_id

    def scope_namespace(
        self,
        namespace: str | None,
        *,
        case_id: str | None = None,
        context: Mapping[str, Any] | None = None,
        default_namespace: str = "default",
        include_default_case: bool = False,
    ) -> str:
        base = _normalize_namespace(namespace, default_namespace=default_namespace)
        if base.startswith("case/"):
            return base
        resolved = self.resolve(case_id=case_id, context=context)
        if (not include_default_case) and self.is_default_case(resolved.case_id):
            return base
        return f"case/{resolved.case_id}/{base}"

    def rag_scope_id(
        self,
        *,
        scope_id: str | None = None,
        case_id: str | None = None,
        context: Mapping[str, Any] | None = None,
        include_default_case: bool = False,
    ) -> str:
        explicit_scope = _optional_text(scope_id)
        if explicit_scope:
            return explicit_scope
        resolved = self.resolve(case_id=case_id, context=context)
        if (not include_default_case) and self.is_default_case(resolved.case_id):
            return "default"
        return f"case:{resolved.case_id}"

    def with_case_metadata(
        self,
        metadata: Mapping[str, Any] | None,
        *,
        case_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        out = dict(metadata or {})
        resolved = self.resolve(case_id=case_id, context=context)
        out.setdefault("case_id", resolved.case_id)
        out.setdefault("case_source", resolved.source)
        return out

    def _env(self) -> Mapping[str, str]:
        if self._env_override is not None:
            return self._env_override
        return os.environ


_SERVICE: CaseService | None = None


def get_case_service(
    *,
    env: Mapping[str, str] | None = None,
    default_case_id: str | None = None,
    reload: bool = False,
) -> CaseService:
    global _SERVICE
    if _SERVICE is None or env is not None or default_case_id is not None:
        _SERVICE = CaseService(
            env=env,
            default_case_id=(default_case_id if isinstance(default_case_id, str) else "global"),
        )
        return _SERVICE
    if reload:
        _SERVICE = CaseService(default_case_id=_SERVICE.default_case_id)
    return _SERVICE


def _normalize_namespace(value: str | None, *, default_namespace: str) -> str:
    text = _optional_text(value) or _optional_text(default_namespace) or "default"
    token = str(text).replace("\\", "/").strip("/")
    return token or "default"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None

