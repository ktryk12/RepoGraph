from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Dict, List, Protocol


@dataclass(frozen=True)
class ToolBudget:
    max_bytes: int = 50_000
    max_results: int = 200
    max_chunks: int = 200


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    ok: bool
    output: Dict[str, Any]
    artifact_ref: str | None = None
    warnings: List[str] = field(default_factory=list)
    cost: Dict[str, Any] = field(default_factory=dict)


class Tool(Protocol):
    name: str

    def run(self, request: Dict[str, Any], *, budget: ToolBudget) -> ToolResult:
        ...


def artifact_ref_for_bytes(data: bytes) -> str:
    return f"artifact:sha256:{sha256(data).hexdigest()}"


def clamp_bytes(text: str, max_bytes: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    truncated = raw[:max_bytes].decode("utf-8", errors="replace")
    return truncated, True
