"""Serialization formats for WorkingSet consumption."""

from __future__ import annotations

from repograph.task_families.registry import get as get_family

from .models import WorkingSet


def to_prompt_context(ws: WorkingSet) -> str:
    """Format WorkingSet as plain text ready to inject into an LLM prompt."""
    family = get_family(ws.task_family)
    preamble = family.prompt_preamble if family else ""

    lines: list[str] = []
    if preamble:
        lines += [preamble, ""]
    lines += [f"# Context: {ws.explanation}", ""]

    for f in ws.files:
        lines.append(f"## {f.filepath}")
        if f.file_summary:
            lines.append(f"_{f.file_summary}_")
        lines.append("")
        for sym in f.symbols:
            lines.append(f"### `{sym.symbol}`")
            if sym.at_line:
                lines.append(f"Line {sym.at_line} · risk: {sym.risk_level}")
            if sym.signature:
                lines.append(f"```\n{sym.signature}\n```")
            if sym.summary:
                lines.append(sym.summary)
            if sym.calls:
                lines.append(f"Calls: {', '.join(sym.calls[:5])}")
            lines.append("")

    # Symbols not attached to a file
    orphans = [s for s in ws.symbols if not s.in_file or not any(
        s.symbol in [fs.symbol for fs in f.symbols] for f in ws.files
    )]
    if orphans:
        lines.append("## Additional symbols")
        for sym in orphans:
            sig = f" — `{sym.signature}`" if sym.signature else ""
            lines.append(f"- `{sym.symbol}`{sig}")
        lines.append("")

    return "\n".join(lines)


def to_compact(ws: WorkingSet) -> dict:
    """Minimal serialisation — symbol names + files only."""
    return {
        "id": ws.id,
        "query": ws.query,
        "task_family": ws.task_family,
        "symbols": [s.symbol for s in ws.symbols],
        "files": [f.filepath for f in ws.files],
        "token_estimate": ws.token_estimate,
        "compression": ws.compression,
    }
