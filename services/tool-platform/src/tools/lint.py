from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import ast
import json

from tools.base import ToolBudget, ToolResult, artifact_ref_for_bytes


class LintTool:
    name = "lint"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[1]

    def run(self, request: Dict[str, Any], *, budget: ToolBudget) -> ToolResult:
        path = request.get("path") or "."
        target = (self.root / Path(str(path))).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            return ToolResult(
                self.name,
                False,
                {"error": "path_outside_repo"},
                warnings=["path_outside_repo"],
            )

        max_files = int(request.get("max_files") or budget.max_results)
        files = sorted(p for p in target.rglob("*.py") if p.is_file())
        if max_files:
            files = files[:max_files]

        errors: List[Dict[str, Any]] = []
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                ast.parse(text, filename=str(path))
            except SyntaxError as exc:
                errors.append({
                    "path": str(path.relative_to(self.root)),
                    "line": exc.lineno,
                    "offset": exc.offset,
                    "message": exc.msg,
                })

        payload = {
            "files_checked": len(files),
            "error_count": len(errors),
            "errors": errors,
        }
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        result_ref = artifact_ref_for_bytes(raw)

        output = {
            "result_ref": result_ref,
            "files_checked": len(files),
            "error_count": len(errors),
        }

        return ToolResult(self.name, len(errors) == 0, output, artifact_ref=result_ref)
