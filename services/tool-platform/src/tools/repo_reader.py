from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from tools.base import ToolBudget, ToolResult, artifact_ref_for_bytes, clamp_bytes


class RepoReaderTool:
    name = "repo_reader"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[1]

    def run(self, request: Dict[str, Any], *, budget: ToolBudget) -> ToolResult:
        path = request.get("path")
        if not isinstance(path, str) or not path.strip():
            return ToolResult(self.name, False, {"error": "missing path"}, warnings=["invalid_path"])

        rel = Path(path)
        target = (self.root / rel).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            return ToolResult(self.name, False, {"error": "path outside repo"}, warnings=["path_outside_repo"])

        if not target.exists() or not target.is_file():
            return ToolResult(self.name, False, {"error": "file not found"}, warnings=["file_missing"])

        text = target.read_text(encoding="utf-8", errors="replace")
        start_line = request.get("start_line")
        end_line = request.get("end_line")
        if isinstance(start_line, int) or isinstance(end_line, int):
            lines = text.splitlines()
            start = max(1, int(start_line) if isinstance(start_line, int) else 1)
            end = int(end_line) if isinstance(end_line, int) else len(lines)
            end = max(start, end)
            text = "\n".join(lines[start - 1 : end])

        from babyai_shared.privacy.redaction import redact_text

        truncated_text, truncated = clamp_bytes(text, budget.max_bytes)
        truncated_text = redact_text(truncated_text)
        warnings = ["truncated"] if truncated else []

        raw = truncated_text.encode("utf-8")
        artifact_ref = artifact_ref_for_bytes(raw)

        output = {
            "path": str(rel),
            "content_ref": artifact_ref,
            "start_line": start_line,
            "end_line": end_line,
            "truncated": truncated,
        }
        cost = {"bytes": len(raw)}
        return ToolResult(self.name, True, output, artifact_ref=artifact_ref, warnings=warnings, cost=cost)
