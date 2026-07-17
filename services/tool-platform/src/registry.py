from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


_PERMISSION_ORDER = {"low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    name: str
    type: str
    capability: str
    risk_rating: str
    required_permissions: list[str] = field(default_factory=list)
    cost_model: dict[str, Any] = field(default_factory=dict)
    audit_hooks: list[str] = field(default_factory=list)
    version: int = 1
    created_at: str = field(default_factory=lambda: _utc_now_iso())


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, list[ToolDefinition]] = {}

    def register(self, tool_def: ToolDefinition) -> str:
        if not isinstance(tool_def, ToolDefinition):
            raise TypeError("tool_def must be ToolDefinition")
        clean_id = str(tool_def.id or "").strip()
        if not clean_id:
            raise ValueError("tool_def.id must be non-empty")

        history = self._definitions.setdefault(clean_id, [])
        version = len(history) + 1
        normalized = ToolDefinition(
            id=clean_id,
            name=str(tool_def.name or "").strip(),
            type=str(tool_def.type or "").strip(),
            capability=str(tool_def.capability or "").strip(),
            risk_rating=_normalize_risk(tool_def.risk_rating),
            required_permissions=[str(item).strip() for item in list(tool_def.required_permissions or []) if str(item).strip()],
            cost_model=dict(tool_def.cost_model or {}),
            audit_hooks=[str(item).strip() for item in list(tool_def.audit_hooks or []) if str(item).strip()],
            version=version,
            created_at=_utc_now_iso(),
        )
        history.append(normalized)
        return clean_id

    def get(self, tool_id: str, version: int | str = "latest") -> ToolDefinition:
        clean_id = str(tool_id or "").strip()
        if not clean_id:
            raise ValueError("tool_id must be non-empty")
        history = self._definitions.get(clean_id, [])
        if not history:
            raise KeyError(f"tool not found: {clean_id}")

        if version in {"latest", None}:
            return history[-1]
        clean_version = int(version)
        if clean_version < 1 or clean_version > len(history):
            raise KeyError(f"version not found: {clean_id}@{clean_version}")
        return history[clean_version - 1]

    def list(self, permission_level: str | None = None) -> list[ToolDefinition]:
        latest = [rows[-1] for rows in self._definitions.values() if rows]
        if permission_level is None:
            return sorted(latest, key=lambda item: item.id)

        rank = _permission_rank(permission_level)
        filtered = [tool for tool in latest if _permission_rank(tool.risk_rating) <= rank]
        return sorted(filtered, key=lambda item: item.id)


def _normalize_risk(value: Any) -> str:
    clean = str(value or "medium").strip().lower()
    if clean not in _PERMISSION_ORDER:
        return "medium"
    return clean


def _permission_rank(value: Any) -> int:
    return _PERMISSION_ORDER.get(str(value or "").strip().lower(), _PERMISSION_ORDER["high"])


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
