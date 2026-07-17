from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from tools.base import ToolBudget, ToolResult, artifact_ref_for_bytes


class SearchLocalIndexTool:
    name = "search_local_index"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[1]

    def run(self, request: Dict[str, Any], *, budget: ToolBudget) -> ToolResult:
        query = request.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(self.name, False, {"error": "missing query"}, warnings=["invalid_query"])

        extensions = request.get("extensions")
        ext_set = None
        if isinstance(extensions, list):
            ext_set = {str(x).lower() for x in extensions if str(x).strip()}

        max_results = int(request.get("max_results") or budget.max_results)

        matches: List[Dict[str, Any]] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.root)
            if ext_set and path.suffix.lower() not in ext_set:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            from babyai_shared.privacy.redaction import redact_text
            text = redact_text(text)
            for idx, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    matches.append({
                        "path": str(rel),
                        "line": idx,
                        "text": line.strip(),
                    })
                    if len(matches) >= max_results:
                        break
            if len(matches) >= max_results:
                break

        payload = {
            "query": query,
            "results": matches,
            "truncated": len(matches) >= max_results,
        }
        raw = str(payload).encode("utf-8")
        artifact_ref = artifact_ref_for_bytes(raw)
        output = {
            "query": query,
            "result_ref": artifact_ref,
            "result_count": len(matches),
            "truncated": len(matches) >= max_results,
        }
        return ToolResult(self.name, True, output, artifact_ref=artifact_ref, cost={"results": len(matches)})
