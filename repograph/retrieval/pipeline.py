"""Multi-stage retrieval pipeline: classify → coarse → structural → fine."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from repograph.graph.factory import GraphStore
from repograph.task_families.registry import defaults_for

from .code_span_selector import select
from .coarse_retriever import coarse_retrieve
from .structural_expander import expand
from .task_planner import classify
from .trace import record


@dataclass
class RetrievalResult:
    retrieval_id: str
    task_family: str
    stages: dict[str, int]           # stage_name → symbol count
    working_set: list[dict]
    token_estimate: int
    files: list[str]
    duration_ms: int


def retrieve(
    query: str,
    store: GraphStore,
    task_hint: str | None = None,
    token_budget: int = 4096,
    coarse_limit: int = 40,
    expand_limit: int = 80,
    persist_trace: bool = True,
) -> RetrievalResult:
    t0 = time.perf_counter()

    # Stage 1: classify
    task_family = classify(query, hint=task_hint)

    # Apply per-family defaults unless caller provided explicit values
    _defaults = defaults_for(task_family)
    if coarse_limit == 40:
        coarse_limit = _defaults["coarse_limit"]
    if expand_limit == 80:
        expand_limit = _defaults["expand_limit"]
    if token_budget == 4096:
        token_budget = _defaults["token_budget"]

    # Stage 2: coarse retrieval
    coarse = coarse_retrieve(query, task_family, store, limit=coarse_limit)

    # Stage 3: structural expansion
    expanded = expand(coarse, task_family, store, max_symbols=expand_limit)

    # Stage 4: token-budget selection
    selected = select(expanded, query, task_family, store, token_budget=token_budget)

    token_estimate = sum(
        80 if s.get("summary") else 40 if s.get("signature") else 15
        for s in selected
    )
    files = sorted({s["in_file"] for s in selected if s.get("in_file")})
    duration_ms = int((time.perf_counter() - t0) * 1000)

    # Stage 5: persist trace
    trace_id = ""
    if persist_trace:
        trace_id = record(
            store, query, task_family,
            coarse, expanded, selected,
            token_estimate, duration_ms,
        )

    return RetrievalResult(
        retrieval_id=trace_id,
        task_family=task_family,
        stages={"coarse": len(coarse), "structural": len(expanded), "fine": len(selected)},
        working_set=selected,
        token_estimate=token_estimate,
        files=files,
        duration_ms=duration_ms,
    )
