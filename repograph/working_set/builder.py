"""WorkingSet builder — assembles a WorkingSet from a retrieval result."""

from __future__ import annotations

import time
import uuid

from repograph.graph.factory import GraphStore
from repograph.indexer.schema import FILE_SUMMARY
from repograph.retrieval.pipeline import RetrievalResult, retrieve

from .budget import enforce_budget, token_cost
from .explainer import explain
from .models import WorkingSet, WorkingSetFile, WorkingSetSymbol


def build(
    query: str,
    store: GraphStore,
    task_hint: str | None = None,
    token_budget: int = 4096,
    coarse_limit: int = 40,
    expand_limit: int = 80,
) -> WorkingSet:
    t0 = time.perf_counter()

    result: RetrievalResult = retrieve(
        query=query,
        store=store,
        task_hint=task_hint,
        token_budget=token_budget,
        coarse_limit=coarse_limit,
        expand_limit=expand_limit,
        persist_trace=True,
    )

    raw_symbols = [
        WorkingSetSymbol(
            symbol=item["symbol"],
            in_file=item.get("in_file"),
            at_line=item.get("at_line"),
            signature=item.get("signature"),
            summary=item.get("summary"),
            risk_level=item.get("risk_level", "medium"),
            callers=item.get("callers", 0),
            calls=store.callees_of(item["symbol"])[:8],
        )
        for item in result.working_set
    ]

    compressed, compression = enforce_budget(raw_symbols, token_budget)
    token_estimate = sum(token_cost(s, compression) for s in compressed)

    # Group symbols by file
    file_map: dict[str, list[WorkingSetSymbol]] = {}
    for sym in compressed:
        key = sym.in_file or "__unknown__"
        file_map.setdefault(key, []).append(sym)

    files = [
        WorkingSetFile(
            filepath=fp,
            file_summary=store.first_outgoing(f"file:{fp}", FILE_SUMMARY),
            symbols=syms,
        )
        for fp, syms in file_map.items()
        if fp != "__unknown__"
    ]

    duration_ms = int((time.perf_counter() - t0) * 1000)

    ws = WorkingSet(
        id=f"ws:{uuid.uuid4()}",
        query=query,
        task_family=result.task_family,
        retrieval_id=result.retrieval_id,
        files=files,
        symbols=compressed,
        token_estimate=token_estimate,
        token_budget=token_budget,
        compression=compression,
        duration_ms=duration_ms,
    )
    ws.explanation = explain(ws)
    return ws
