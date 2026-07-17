from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import asyncio
import json
import time
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class PermissionResult:
    approved: bool
    permission_level: str
    reason: str
    requires_human: bool
    request_id: str | None
    checked_at: str


class PermissionEngine:
    def __init__(
        self,
        project_id: str,
        council_ref: Any,
        human_approval_channel: str = "babyai:human_approval",
    ) -> None:
        self.project_id = str(project_id or "").strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")
        self.council_ref = council_ref
        self.human_approval_channel = str(human_approval_channel or "babyai:human_approval").strip() or "babyai:human_approval"

    def check(self, agent: Any, tool: Any, context: dict[str, Any] | None = None) -> PermissionResult:
        clean_context = dict(context or {})
        level = _resolve_permission_level(tool=tool, context=clean_context)
        if level == "low":
            result = PermissionResult(
                approved=True,
                permission_level="low",
                reason="auto_approved_low_risk",
                requires_human=False,
                request_id=None,
                checked_at=_utc_now_iso(),
            )
            self._log_permission_event(agent=agent, tool=tool, context=clean_context, result=result)
            return result

        if level == "medium":
            approved, reason = self._request_council_approval(agent=agent, tool=tool, context=clean_context)
            result = PermissionResult(
                approved=bool(approved),
                permission_level="medium",
                reason=reason,
                requires_human=False,
                request_id=None,
                checked_at=_utc_now_iso(),
            )
            self._log_permission_event(agent=agent, tool=tool, context=clean_context, result=result)
            return result

        request_id, approved, reason = self._request_human_approval(agent=agent, tool=tool, context=clean_context)
        result = PermissionResult(
            approved=bool(approved),
            permission_level="high",
            reason=reason,
            requires_human=True,
            request_id=request_id,
            checked_at=_utc_now_iso(),
        )
        self._log_permission_event(agent=agent, tool=tool, context=clean_context, result=result)
        return result

    def _request_council_approval(self, *, agent: Any, tool: Any, context: dict[str, Any]) -> tuple[bool, str]:
        candidates = []
        for owner in (self.council_ref, getattr(self.council_ref, "consensus_engine", None)):
            if owner is None:
                continue
            for method_name in ("approve_tool_call", "request_tool_approval", "approve"):
                method = getattr(owner, method_name, None)
                if callable(method):
                    candidates.append(method)

        for method in candidates:
            try:
                out = method(agent=agent, tool=tool, context=context)
            except TypeError:
                out = method(agent, tool, context)
            resolved = _resolve_awaitable(out)
            approved, reason = _parse_approval_response(resolved)
            return approved, reason
        return False, "council_approval_unavailable"

    def _request_human_approval(self, *, agent: Any, tool: Any, context: dict[str, Any]) -> tuple[str | None, bool, str]:
        redis_client = _resolve_redis(context=context, council_ref=self.council_ref)
        if redis_client is None:
            return None, False, "human_approval_channel_unavailable"

        request_id = str(uuid4())
        timeout = max(0.01, float(context.get("approval_timeout", 10.0)))
        poll_interval = max(0.01, float(context.get("poll_interval", 0.1)))
        request_payload = {
            "request_id": request_id,
            "project_id": self.project_id,
            "agent_id": str(getattr(agent, "agent_id", "") or ""),
            "agent_role": str(getattr(agent, "role", "") or ""),
            "tool_name": str(getattr(tool, "__class__", type(tool)).__name__),
            "permission_level": "high",
            "created_at": _utc_now_iso(),
            "context": {
                "domain": str(context.get("domain") or getattr(self.council_ref, "domain", "general")),
                "reason": str(context.get("reason") or ""),
            },
        }
        _publish(redis_client=redis_client, channel=self.human_approval_channel, payload=request_payload)

        started = time.monotonic()
        while (time.monotonic() - started) <= timeout:
            messages = _consume_channel(redis_client=redis_client, channel=self.human_approval_channel)
            for raw in messages:
                row = _decode_message(raw)
                if str(row.get("request_id") or "") != request_id:
                    continue
                if bool(row.get("approved", False)):
                    return request_id, True, "human_approved"
                decision = str(row.get("decision") or "").strip().lower()
                if decision in {"approve", "approved", "allow"}:
                    return request_id, True, "human_approved"
                if decision in {"deny", "denied", "reject"}:
                    return request_id, False, "human_denied"
            time.sleep(poll_interval)
        return request_id, False, "human_approval_timeout"

    def _log_permission_event(self, *, agent: Any, tool: Any, context: dict[str, Any], result: PermissionResult) -> None:
        memory_ref = _resolve_memory_ref(context=context, council_ref=self.council_ref)
        save = getattr(memory_ref, "save", None)
        if not callable(save):
            return
        domain = str(context.get("domain") or getattr(self.council_ref, "domain", "general") or "general")
        payload = {
            "subtype": "permission_check",
            "project_id": self.project_id,
            "domain": domain,
            "agent_role": str(getattr(agent, "role", "") or ""),
            "tool_name": str(getattr(tool, "__class__", type(tool)).__name__),
            "result": {
                "approved": bool(result.approved),
                "permission_level": result.permission_level,
                "reason": result.reason,
                "requires_human": bool(result.requires_human),
                "request_id": result.request_id,
            },
            "created_at": _utc_now_iso(),
        }
        save(self.project_id, domain, "event", payload)


def _resolve_permission_level(*, tool: Any, context: dict[str, Any]) -> str:
    override = str(context.get("permission_level") or "").strip().lower()
    if override in {"low", "medium", "high"}:
        return override
    method = getattr(tool, "permission_level", None)
    if callable(method):
        value = str(method()).strip().lower()
        if value in {"low", "medium", "high"}:
            return value
    risk_rating = str(getattr(tool, "risk_rating", "")).strip().lower()
    if risk_rating in {"low", "medium", "high"}:
        return risk_rating
    return "low"


def _resolve_awaitable(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return asyncio.run(value)
    return value


def _parse_approval_response(value: Any) -> tuple[bool, str]:
    if isinstance(value, bool):
        return bool(value), "council_approved" if value else "council_denied"
    if isinstance(value, dict):
        approved = bool(value.get("approved", False))
        reason = str(value.get("reason") or ("council_approved" if approved else "council_denied"))
        return approved, reason
    return bool(value), "council_approved" if bool(value) else "council_denied"


def _resolve_redis(*, context: dict[str, Any], council_ref: Any) -> Any | None:
    client = context.get("redis_client")
    if client is not None:
        return client
    memory_ref = _resolve_memory_ref(context=context, council_ref=council_ref)
    return getattr(memory_ref, "redis", None) if memory_ref is not None else None


def _resolve_memory_ref(*, context: dict[str, Any], council_ref: Any) -> Any | None:
    memory_ref = context.get("memory_ref")
    if memory_ref is not None:
        return memory_ref
    for attr in ("memory_ref", "memory"):
        candidate = getattr(council_ref, attr, None)
        if candidate is not None:
            return candidate
    return None


def _publish(*, redis_client: Any, channel: str, payload: dict[str, Any]) -> None:
    message = json.dumps(payload, ensure_ascii=True, sort_keys=True)
    method = getattr(redis_client, "publish", None)
    if callable(method):
        method(str(channel), message)
        return
    method = getattr(redis_client, "rpush", None)
    if callable(method):
        method(str(channel), message)


def _consume_channel(*, redis_client: Any, channel: str) -> list[Any]:
    consume = getattr(redis_client, "consume_channel", None)
    if callable(consume):
        rows = consume(str(channel))
        return list(rows) if isinstance(rows, list) else []
    getter = getattr(redis_client, "lrange", None)
    deleter = getattr(redis_client, "delete", None)
    if callable(getter):
        rows = getter(str(channel), 0, -1)
        if callable(deleter):
            deleter(str(channel))
        return list(rows) if isinstance(rows, list) else []
    channels = getattr(redis_client, "channels", None)
    if isinstance(channels, dict):
        rows = list(channels.get(str(channel), []))
        channels[str(channel)] = []
        return rows
    return []


def _decode_message(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw or "")
    if not text.strip():
        return {}
    try:
        value = json.loads(text)
    except Exception:
        return {"raw": text}
    return dict(value) if isinstance(value, dict) else {"raw": text}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
