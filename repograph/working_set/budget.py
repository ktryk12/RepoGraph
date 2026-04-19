"""Token budget enforcer and compression strategies for WorkingSet."""

from __future__ import annotations

from .models import WorkingSetSymbol

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

# Token costs per compression level
_COST_FULL = 80      # signature + summary + calls
_COST_SIG = 40       # signature only
_COST_BARE = 15      # name + file + line only


def token_cost(sym: WorkingSetSymbol, compression: str) -> int:
    match compression:
        case "symbols_only":
            return _COST_BARE
        case "signatures_only":
            return _COST_SIG
        case _:
            return _COST_FULL if sym.summary else _COST_SIG if sym.signature else _COST_BARE


def enforce_budget(
    symbols: list[WorkingSetSymbol],
    token_budget: int,
) -> tuple[list[WorkingSetSymbol], str]:
    """
    Apply the lightest compression that fits within token_budget.
    Returns (compressed_symbols, compression_strategy_name).
    """
    for strategy in ("none", "drop_low_risk", "signatures_only", "symbols_only"):
        candidates = _apply(symbols, strategy)
        total = sum(token_cost(s, strategy) for s in candidates)
        if total <= token_budget:
            return candidates, strategy

    # Hard cap: return as many symbols_only entries as fit
    result = []
    used = 0
    for sym in sorted(symbols, key=lambda s: _RISK_ORDER.get(s.risk_level, 1), reverse=True):
        if used + _COST_BARE > token_budget:
            break
        result.append(_strip(sym))
        used += _COST_BARE
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
