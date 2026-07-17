from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple
import json

from tools.base import ToolBudget, ToolResult, artifact_ref_for_bytes


class SecurityScanTool:
    name = "security_scan"

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
        max_findings = int(request.get("max_findings") or budget.max_results)

        patterns: List[Tuple[str, str]] = [
            ("eval(", "eval_usage"),
            ("exec(", "exec_usage"),
            ("pickle.loads", "pickle_loads"),
            ("subprocess.Popen", "subprocess_popen"),
            ("shell=True", "subprocess_shell"),
        ]

        findings: List[Dict[str, Any]] = []
        files = sorted(p for p in target.rglob("*.py") if p.is_file())
        if max_files:
            files = files[:max_files]

        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            rel = str(path.relative_to(self.root))
            for idx, line in enumerate(text.splitlines(), start=1):
                for needle, rule in patterns:
                    if needle in line:
                        findings.append({
                            "path": rel,
                            "line": idx,
                            "rule": rule,
                        })
                        if len(findings) >= max_findings:
                            break
                if len(findings) >= max_findings:
                    break
            if len(findings) >= max_findings:
                break

        payload = {
            "files_scanned": len(files),
            "finding_count": len(findings),
            "findings": findings,
        }
        raw = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        result_ref = artifact_ref_for_bytes(raw)

        output = {
            "result_ref": result_ref,
            "files_scanned": len(files),
            "finding_count": len(findings),
        }
        return ToolResult(self.name, len(findings) == 0, output, artifact_ref=result_ref)
