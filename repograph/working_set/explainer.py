"""Explanation layer — generates a structural description of a WorkingSet."""

from __future__ import annotations

from .models import WorkingSet

_FAMILY_DESCRIPTIONS = {
    "symbol_lookup": "symbol definition lookup",
    "file_to_symbol_map": "file content overview",
    "bug_localization": "bug localization",
    "call_chain_reasoning": "call chain tracing",
    "blast_radius_analysis": "blast radius / change impact analysis",
    "targeted_refactor": "targeted refactor",
    "test_impact_lookup": "test impact lookup",
    "targeted_test_generation": "test generation",
    "config_dependency_reasoning": "config dependency reasoning",
}


def explain(ws: WorkingSet) -> str:
    task_desc = _FAMILY_DESCRIPTIONS.get(ws.task_family, ws.task_family)
    n_sym = len(ws.symbols)
    n_files = len(ws.files)
    risk_counts = _count_risk(ws.symbols)

    parts = [
        f"WorkingSet for '{ws.query}' ({task_desc}).",
        f"{n_sym} symbols across {n_files} file{'s' if n_files != 1 else ''}.",
    ]

    if any(risk_counts.values()):
        risk_str = ", ".join(
            f"{v} {k}" for k, v in risk_counts.items() if v
        )
        parts.append(f"Risk profile: {risk_str}.")

    if ws.compression != "none":
        parts.append(f"Compression applied: {ws.compression} (budget: {ws.token_budget} tokens).")

    token_pct = int(ws.token_estimate / ws.token_budget * 100) if ws.token_budget else 0
    parts.append(f"Token estimate: ~{ws.token_estimate} ({token_pct}% of budget).")

    return " ".join(parts)


def _count_risk(symbols) -> dict[str, int]:
    counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for s in symbols:
        if s.risk_level in counts:
            counts[s.risk_level] += 1
    return counts
