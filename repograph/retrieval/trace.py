"""Retrieval trace persistence — writes retrieval events to the graph store."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from repograph.graph.factory import GraphStore

_PRED_QUERY = "retrieval_query"
_PRED_TASK = "retrieval_task_family"
_PRED_SYMBOL = "retrieval_symbol"
_PRED_STAGE = "retrieval_stage"
_PRED_TOKENS = "retrieval_token_estimate"
_PRED_FILES = "retrieval_files_count"
_PRED_AT = "retrieval_at"
_PRED_DURATION = "retrieval_duration_ms"


def record(
    store: GraphStore,
    query: str,
    task_family: str,
    coarse_symbols: list[str],
    expanded_symbols: list[str],
    selected: list[dict],
    token_estimate: int,
    duration_ms: int,
) -> str:
    """Write a retrieval trace to the graph. Returns the trace_id."""
    trace_id = f"retrieval:{uuid.uuid4()}"
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    triples = [
        (trace_id, _PRED_QUERY, query),
        (trace_id, _PRED_TASK, task_family),
        (trace_id, _PRED_AT, now),
        (trace_id, _PRED_TOKENS, str(token_estimate)),
        (trace_id, _PRED_DURATION, str(duration_ms)),
        (trace_id, _PRED_STAGE, f"coarse:{len(coarse_symbols)}"),
        (trace_id, _PRED_STAGE, f"structural:{len(expanded_symbols)}"),
        (trace_id, _PRED_STAGE, f"fine:{len(selected)}"),
    ]

    files = {s["in_file"] for s in selected if s.get("in_file")}
    triples.append((trace_id, _PRED_FILES, str(len(files))))

    for item in selected:
        triples.append((trace_id, _PRED_SYMBOL, item["symbol"]))

    store.put_triples_batch(triples)
    return trace_id
