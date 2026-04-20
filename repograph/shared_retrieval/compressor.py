"""LongCodeZip-inspireret strukturel kontekstkomprimering.

Strategi (ingen LLM):
  1. score hvert symbol: risk_level × caller_weight
  2. iterér over komprimeringstrin til token_budget er opfyldt
  3. returnér CompressedContext med pre/post token-estimater
"""
from __future__ import annotations

from dataclasses import dataclass

from repograph.working_set.models import WorkingSet, WorkingSetSymbol

_CHARS_PER_TOKEN = 4


def _tok(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _sym_tokens(sym: WorkingSetSymbol, *, include_calls: bool = True) -> int:
    parts = [sym.symbol]
    if sym.signature:
        parts.append(sym.signature)
    if sym.summary:
        parts.append(sym.summary)
    if include_calls and sym.calls:
        parts.append(", ".join(sym.calls[:4]))
    return sum(_tok(p) for p in parts)


_RISK_SCORE = {"high": 3, "medium": 2, "low": 1}


def _score(sym: WorkingSetSymbol) -> float:
    return _RISK_SCORE.get(sym.risk_level, 2) * (1 + min(sym.callers, 20) * 0.1)


@dataclass
class CompressedContext:
    symbols: list[WorkingSetSymbol]
    pre_compress_tokens: int
    post_compress_tokens: int
    strategy_applied: str   # none | drop_calls | drop_low_summaries | drop_low_risk
    budget: int


def compress(ws: WorkingSet, budget: int) -> CompressedContext:
    """Trim WorkingSet symbols to fit within budget using 3-pass structural compression."""
    symbols = list(ws.symbols)
    pre = sum(_sym_tokens(s) for s in symbols)

    # Already fits — return as-is
    if pre <= budget:
        return CompressedContext(
            symbols=symbols,
            pre_compress_tokens=pre,
            post_compress_tokens=pre,
            strategy_applied="none",
            budget=budget,
        )

    # Pass 1 — drop call lists from low-risk symbols
    pass1 = [
        s.model_copy(update={"calls": []}) if s.risk_level == "low" else s
        for s in symbols
    ]
    current = sum(_sym_tokens(s) for s in pass1)
    if current <= budget:
        return CompressedContext(
            symbols=pass1, pre_compress_tokens=pre,
            post_compress_tokens=current, strategy_applied="drop_calls", budget=budget,
        )

    # Pass 2 — drop summaries from low-risk symbols
    pass2 = [
        s.model_copy(update={"summary": None, "calls": []}) if s.risk_level == "low" else s
        for s in pass1
    ]
    current = sum(_sym_tokens(s) for s in pass2)
    if current <= budget:
        return CompressedContext(
            symbols=pass2, pre_compress_tokens=pre,
            post_compress_tokens=current, strategy_applied="drop_low_summaries", budget=budget,
        )

    # Pass 3 — drop lowest-scored symbols entirely until budget fits
    ranked = sorted(pass2, key=_score, reverse=True)
    kept: list[WorkingSetSymbol] = []
    used = 0
    for sym in ranked:
        cost = _sym_tokens(sym)
        if used + cost > budget:
            continue
        kept.append(sym)
        used += cost

    return CompressedContext(
        symbols=kept, pre_compress_tokens=pre,
        post_compress_tokens=used, strategy_applied="drop_low_risk", budget=budget,
    )
