from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    tool_type: str
    permission_level: str
    ok: bool
    output: dict[str, Any]
    error: str | None = None
    started_at: str = field(default_factory=lambda: _utc_now_iso())
    finished_at: str = field(default_factory=lambda: _utc_now_iso())
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_type": self.tool_type,
            "permission_level": self.permission_level,
            "ok": bool(self.ok),
            "output": dict(self.output),
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": float(self.duration_ms),
            "metadata": dict(self.metadata),
        }


def ensure_audit_sink(memory_ref: Any, *, project_id: str, domain: str) -> Callable[[dict[str, Any]], None]:
    clean_project_id = str(project_id or "").strip()
    clean_domain = str(domain or "").strip()
    if not clean_project_id:
        raise ValueError("project_id must be non-empty for audited tool execution")
    if not clean_domain:
        raise ValueError("domain must be non-empty for audited tool execution")
    save = getattr(memory_ref, "save", None)
    if not callable(save):
        raise ValueError("memory_ref must implement save(project_id, domain, entry_type, content)")

    def _sink(content: dict[str, Any]) -> None:
        save(clean_project_id, clean_domain, "event", dict(content))

    return _sink


def log_tool_call(
    *,
    sink: Callable[[dict[str, Any]], None],
    project_id: str,
    domain: str,
    tool_name: str,
    tool_type: str,
    permission_level: str,
    request: dict[str, Any],
    result: ToolResult,
    agent_id: str | None = None,
) -> None:
    payload = {
        "subtype": "tool_call",
        "project_id": str(project_id),
        "domain": str(domain),
        "tool_name": str(tool_name),
        "tool_type": str(tool_type),
        "permission_level": str(permission_level),
        "agent_id": str(agent_id or "").strip() or None,
        "request": dict(request),
        "result": result.to_dict(),
        "created_at": _utc_now_iso(),
    }
    sink(payload)


def duration_ms(*, started_at: datetime, finished_at: datetime) -> float:
    return max(0.0, (finished_at - started_at).total_seconds() * 1000.0)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
