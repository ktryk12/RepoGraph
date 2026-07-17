"""
skill_runtime/executor/output_parser.py — Struktureret output-extraction.

Forsøger at parse JSON-blokke, markdown-sektioner og findings.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParsedOutput:
    raw:            str
    structured:     Dict[str, Any]   = field(default_factory=dict)
    findings:       List[str]        = field(default_factory=list)
    artifacts:      List[str]        = field(default_factory=list)
    auto_fixes:     List[str]        = field(default_factory=list)
    status:         str              = "success"


def parse(raw: str) -> ParsedOutput:
    out = ParsedOutput(raw=raw)

    # JSON-blok
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        try:
            out.structured = json.loads(json_match.group(1))
        except Exception:
            pass

    # Findings: linjer der starter med - eller * eller nummererede
    for line in raw.splitlines():
        line = line.strip()
        if re.match(r"^[-*•]\s+\*{0,2}(FINDING|ISSUE|RISK|FEJL|ADVARSEL)", line, re.I):
            out.findings.append(line)
        if re.match(r"^\d+\.\s+(FINDING|ISSUE)", line, re.I):
            out.findings.append(line)

    # Artifact-refs: linjer med "artifact:", "fil:", "path:"
    for line in raw.splitlines():
        m = re.search(r"(artifact|fil|path|ref)[:\s]+([`'\"]?)([^\s`'\"]{5,})\2", line, re.I)
        if m:
            out.artifacts.append(m.group(3))

    # Auto-fixes: linjer med "FIX:", "RETTET:", "AUTO-FIX:"
    for line in raw.splitlines():
        if re.match(r"^(FIX|RETTET|AUTO.FIX)[:\s]", line, re.I):
            out.auto_fixes.append(line)

    if "[expert_client_error" in raw:
        out.status = "failure"

    return out
