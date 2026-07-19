"""Code span selector — filters expanded symbols to fit a token budget."""

from __future__ import annotations

from repograph.graph.factory import GraphStore
from repograph.indexer.schema import AT_LINE, IN_FILE, RISK_LEVEL, SIGNATURE, SHORT_SUMMARY
from repograph.token_budget import get_engine

_RISK_PRIORITY = {"high": 3, "medium": 2, "low": 1}


def select(
    symbols: list[str],
    query: str,
    task_family: str,
    store: GraphStore,
    token_budget: int = 4096,
    target_model: str | None = None,
) -> list[dict]:
    """Return an ordered list of symbol dicts that fit within token_budget."""
    scored = [_score(sym, query, task_family, store, target_model) for sym in symbols]
    scored.sort(key=lambda x: x["_score"], reverse=True)

    selected = []
    tokens_used = 0
    for item in scored:
        cost = item["_token_cost"]
        if tokens_used + cost > token_budget:
            continue
        tokens_used += cost
        item.pop("_score")
        item.pop("_token_cost")
        selected.append(item)

    return selected


def _score(
    sym: str,
    query: str,
    task_family: str,
    store: GraphStore,
    target_model: str | None = None,
) -> dict:
    in_file = store.first_outgoing(sym, IN_FILE)
    at_line = store.first_outgoing(sym, AT_LINE)
    signature = store.first_outgoing(sym, SIGNATURE)
    summary = store.first_outgoing(sym, SHORT_SUMMARY)
    risk = store.first_outgoing(sym, RISK_LEVEL) or "medium"

    # Relevance: query terms in symbol name
    query_terms = set(query.lower().split())
    sym_lower = sym.lower()
    term_hits = sum(1 for t in query_terms if t in sym_lower)

    risk_score = _RISK_PRIORITY.get(risk, 2)

    # Caller count as proxy for importance
    caller_count = min(len(store.callers_of(sym)), 10)

    score = term_hits * 10 + risk_score * 3 + caller_count

    token_cost = estimate_item_tokens(
        {
            "symbol": sym,
            "in_file": in_file,
            "at_line": at_line,
            "signature": signature,
            "summary": summary,
        },
        target_model,
    )

    return {
        "symbol": sym,
        "in_file": in_file,
        "at_line": at_line,
        "signature": signature,
        "summary": summary,
        "risk_level": risk,
        "callers": len(store.callers_of(sym)),
        "_score": score,
        "_token_cost": token_cost,
    }


def estimate_item_tokens(item: dict, target_model: str | None = None) -> int:
    engine = get_engine(target_model)
    fields = (
        item.get("symbol"),
        item.get("in_file"),
        item.get("at_line"),
        item.get("signature"),
        item.get("summary"),
    )
    return engine.count_text("\n".join(str(value) for value in fields if value))
