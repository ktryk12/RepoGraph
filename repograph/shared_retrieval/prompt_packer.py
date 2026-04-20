"""Prompt/context packing engine — 5 strategies for different task types."""

from __future__ import annotations

from repograph.task_families.registry import get as get_family
from repograph.working_set.models import WorkingSet

from .models import PromptBlock, PromptPack
from .profiles import OutputProfile

_WORDS_PER_TOKEN = 0.75
_CHARS_PER_TOKEN = 4


def _tok(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def pack(
    ws: WorkingSet,
    profile: OutputProfile,
    failure_reason: str | None = None,
    previous_diff: str | None = None,
) -> PromptPack:
    strategy = profile.packing_strategy
    if failure_reason:
        strategy = "retry"

    match strategy:
        case "summary_first":
            blocks = _summary_first(ws, profile)
        case "symbol_first":
            blocks = _symbol_first(ws, profile)
        case "patch_first":
            blocks = _patch_first(ws, profile)
        case "test_first":
            blocks = _test_first(ws, profile)
        case "retry":
            blocks = _retry_pack(ws, profile, failure_reason or "", previous_diff)
        case _:
            blocks = _summary_first(ws, profile)

    preamble = _get_preamble(ws.task_family)
    objective = _format_objective(ws.query, ws.task_family)

    total = _tok(preamble) + _tok(objective) + sum(b.token_estimate for b in blocks)

    return PromptPack(
        preamble=preamble,
        objective=objective,
        context_blocks=blocks,
        total_tokens=total,
        strategy=strategy,
        target_context=profile.target_context,
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

def _summary_first(ws: WorkingSet, profile: OutputProfile) -> list[PromptBlock]:
    blocks: list[PromptBlock] = []

    # L2 file summaries first
    for f in ws.files[:profile.max_files]:
        if f.file_summary:
            content = f"File: {f.filepath}\n{f.file_summary}"
            blocks.append(PromptBlock(
                role="context", label=f.filepath,
                content=content, token_estimate=_tok(content),
                why_included="file summary — L2 context",
            ))

    # Then signatures (no full bodies)
    if profile.include_signatures:
        for sym in ws.symbols[:profile.max_symbols]:
            if sym.signature:
                content = f"`{sym.symbol}` (line {sym.at_line}, risk={sym.risk_level})\n{sym.signature}"
                if sym.summary:
                    content += f"\n{sym.summary}"
                blocks.append(PromptBlock(
                    role="context", label=sym.symbol,
                    content=content, token_estimate=_tok(content),
                    why_included="symbol signature — L3 context",
                ))

    return _trim_to_budget(blocks, profile.target_context)


def _symbol_first(ws: WorkingSet, profile: OutputProfile) -> list[PromptBlock]:
    blocks: list[PromptBlock] = []

    # Symbols sorted by risk + caller count
    sorted_syms = sorted(
        ws.symbols[:profile.max_symbols],
        key=lambda s: ({"high": 3, "medium": 2, "low": 1}.get(s.risk_level, 2), s.callers),
        reverse=True,
    )

    for sym in sorted_syms:
        lines = [f"`{sym.symbol}`"]
        if sym.at_line:
            lines.append(f"  file: {sym.in_file}  line: {sym.at_line}  risk: {sym.risk_level}")
        if sym.signature and profile.include_signatures:
            lines.append(f"  sig: {sym.signature}")
        if sym.summary:
            lines.append(f"  summary: {sym.summary}")
        if sym.calls and profile.include_calls:
            lines.append(f"  calls: {', '.join(sym.calls[:4])}")
        content = "\n".join(lines)
        blocks.append(PromptBlock(
            role="context", label=sym.symbol,
            content=content, token_estimate=_tok(content),
            why_included=f"risk={sym.risk_level} callers={sym.callers}",
        ))

    return _trim_to_budget(blocks, profile.target_context)


def _patch_first(ws: WorkingSet, profile: OutputProfile) -> list[PromptBlock]:
    """Pack focused on the specific symbols to change + their callers."""
    blocks: list[PromptBlock] = []

    # High-risk symbols first
    high = [s for s in ws.symbols if s.risk_level == "high"]
    rest = [s for s in ws.symbols if s.risk_level != "high"]

    for sym in (high + rest)[:profile.max_symbols]:
        lines = [f"`{sym.symbol}` — PATCH TARGET"]
        if sym.signature and profile.include_signatures:
            lines.append(f"```\n{sym.signature}\n```")
        if sym.summary:
            lines.append(sym.summary)
        if sym.calls and profile.include_calls:
            lines.append(f"Callers ({sym.callers}): {', '.join(sym.calls[:3])}")
        content = "\n".join(lines)
        blocks.append(PromptBlock(
            role="context", label=sym.symbol,
            content=content, token_estimate=_tok(content),
            why_included="patch target",
        ))

    return _trim_to_budget(blocks, profile.target_context)


def _test_first(ws: WorkingSet, profile: OutputProfile) -> list[PromptBlock]:
    """Pack focused on test patterns and the symbol under test."""
    blocks: list[PromptBlock] = []

    test_syms = [s for s in ws.symbols if s.in_file and "test" in (s.in_file or "").lower()]
    src_syms = [s for s in ws.symbols if s not in test_syms]

    for sym in src_syms[:5]:
        content = f"Symbol under test: `{sym.symbol}`"
        if sym.signature:
            content += f"\n```\n{sym.signature}\n```"
        blocks.append(PromptBlock(role="context", label=sym.symbol,
                                  content=content, token_estimate=_tok(content),
                                  why_included="symbol under test"))

    for sym in test_syms[:profile.max_symbols]:
        content = f"Existing test: `{sym.symbol}`"
        if sym.signature:
            content += f"\n```\n{sym.signature}\n```"
        blocks.append(PromptBlock(role="context", label=sym.symbol,
                                  content=content, token_estimate=_tok(content),
                                  why_included="existing test pattern"))

    return _trim_to_budget(blocks, profile.target_context)


def _retry_pack(
    ws: WorkingSet,
    profile: OutputProfile,
    failure_reason: str,
    previous_diff: str | None,
) -> list[PromptBlock]:
    """Pack for retry after verification failure."""
    blocks: list[PromptBlock] = []

    fail_content = f"PREVIOUS ATTEMPT FAILED:\n{failure_reason}"
    blocks.append(PromptBlock(role="retry", label="failure",
                              content=fail_content, token_estimate=_tok(fail_content),
                              why_included="failure context for retry"))

    if previous_diff:
        diff_content = f"Previous patch (failed):\n```diff\n{previous_diff[:1000]}\n```"
        blocks.append(PromptBlock(role="retry", label="previous_diff",
                                  content=diff_content, token_estimate=_tok(diff_content),
                                  why_included="previous failed diff"))

    blocks.extend(_patch_first(ws, profile))
    return _trim_to_budget(blocks, profile.target_context)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _trim_to_budget(blocks: list[PromptBlock], budget: int) -> list[PromptBlock]:
    result, used = [], 0
    for b in blocks:
        if used + b.token_estimate > budget:
            break
        result.append(b)
        used += b.token_estimate
    return result


def _get_preamble(task_family: str) -> str:
    family = get_family(task_family)
    return family.prompt_preamble if family else ""


def _format_objective(query: str, task_family: str) -> str:
    return f"Task ({task_family}): {query}"
