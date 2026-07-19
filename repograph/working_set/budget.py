"""Token budget enforcer and compression strategies for WorkingSet."""

from __future__ import annotations

from repograph.token_budget import get_engine

from .models import WorkingSetSymbol

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

def token_cost(
    sym: WorkingSetSymbol,
    compression: str,
    target_model: str | None = None,
) -> int:
    """Count the representation actually retained at a compression level."""
    engine = get_engine(target_model)
    parts = [sym.symbol]
    if sym.in_file:
        parts.append(sym.in_file)
    if sym.at_line:
        parts.append(str(sym.at_line))
    match compression:
        case "symbols_only":
            pass
        case "signatures_only":
            if sym.signature:
                parts.append(sym.signature)
        case _:
            if sym.signature:
                parts.append(sym.signature)
            if sym.summary:
                parts.append(sym.summary)
            if sym.calls:
                parts.extend(sym.calls[:8])
    return engine.count_text("\n".join(parts))


def enforce_budget(
    symbols: list[WorkingSetSymbol],
    token_budget: int,
    target_model: str | None = None,
) -> tuple[list[WorkingSetSymbol], str]:
    """
    Apply the lightest compression that fits within token_budget.
    Returns (compressed_symbols, compression_strategy_name).
    """
    for strategy in ("none", "drop_low_risk", "signatures_only", "symbols_only"):
        candidates = _apply(symbols, strategy)
        total = sum(token_cost(s, strategy, target_model) for s in candidates)
        if total <= token_budget:
            return candidates, strategy

    # Hard cap: return as many symbols_only entries as fit
    result = []
    used = 0
    for sym in sorted(symbols, key=lambda s: _RISK_ORDER.get(s.risk_level, 1), reverse=True):
        stripped = _strip(sym)
        cost = token_cost(stripped, "symbols_only", target_model)
        if used + cost > token_budget:
            continue
        result.append(stripped)
        used += cost
    return result, "symbols_only"


def _apply(symbols: list[WorkingSetSymbol], strategy: str) -> list[WorkingSetSymbol]:
    match strategy:
        case "none":
            return symbols

        case "drop_low_risk":
            # Drop low-risk symbols that have no callers first
            filtered = [s for s in symbols if s.risk_level != "low" or s.callers > 0]
            return filtered if filtered else symbols

        case "signatures_only":
            return [s.model_copy(update={"summary": None, "calls": []}) for s in symbols]

        case "symbols_only":
            return [_strip(s) for s in symbols]

        case _:
            return symbols


def _strip(sym: WorkingSetSymbol) -> WorkingSetSymbol:
    return sym.model_copy(update={"signature": None, "summary": None, "calls": []})
