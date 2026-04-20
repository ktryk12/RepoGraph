"""SharedRetrievalGateway — prepare_task_context() orchestrator."""

from __future__ import annotations

import time
import uuid

from repograph.cache import keys as cache_keys
from repograph.cache import redis as redis_layer
from repograph.graph.factory import GraphStore
from repograph.postgres import tracer as pg_tracer
from repograph.working_set.builder import build as build_working_set

from .compressor import compress
from .models import (
    CacheInfo,
    SharedRetrievalRequest,
    SharedRetrievalResponse,
    VerificationPlan,
)
from .profiles import get_profile
from .prompt_packer import pack


def prepare_task_context(
    req: SharedRetrievalRequest,
    store: GraphStore,
) -> SharedRetrievalResponse:
    t0 = time.perf_counter()

    profile = get_profile(req.output_profile)

    # Cache lookup
    q_hash = cache_keys.query_hash(req.query, req.output_profile, req.target_context)
    ws_key = cache_keys.working_set(req.tenant_id, req.repo_path, q_hash)

    cache_info = CacheInfo()

    if not req.force_refresh:
        cached = redis_layer.get(ws_key)
        if cached is not None:
            cache_info = CacheInfo(used=True, keys_hit=[ws_key])
            return SharedRetrievalResponse(**cached)

    # Build WorkingSet
    ws = build_working_set(
        query=req.query,
        store=store,
        task_hint=req.task_hint,
        token_budget=req.target_context,
    )

    # Compress to budget (structural, no LLM)
    compressed = compress(ws, profile.target_context)
    ws = ws.model_copy(update={
        "symbols": compressed.symbols,
        "token_estimate": compressed.post_compress_tokens,
        "compression": compressed.strategy_applied,
    })

    # Pack context blocks
    prompt_pack = pack(ws, profile)

    # Build verification plan
    verification_plan = _build_verification_plan(ws)

    task_id = req.task_id or f"task:{uuid.uuid4()}"
    ws_id = ws.id

    duration_ms = int((time.perf_counter() - t0) * 1000)

    pg_tracer.log_retrieval_trace(
        retrieval_id=ws.retrieval_id,
        tenant_id=req.tenant_id,
        query=req.query,
        task_family=ws.task_family,
        token_budget=req.target_context,
        token_estimate=compressed.post_compress_tokens,
        duration_ms=duration_ms,
        consumer=req.consumer,
        compressor_strategy=compressed.strategy_applied,
        pre_compress_tokens=compressed.pre_compress_tokens,
        post_compress_tokens=compressed.post_compress_tokens,
    )

    debug = {}
    if req.include_debug:
        debug = {
            "profile": profile.name,
            "strategy": prompt_pack.strategy,
            "ws_symbols": len(ws.symbols),
            "ws_files": len(ws.files),
            "compression": ws.compression,
            "pre_compress_tokens": compressed.pre_compress_tokens,
            "post_compress_tokens": compressed.post_compress_tokens,
            "compressor_strategy": compressed.strategy_applied,
        }

    response = SharedRetrievalResponse(
        task_family=ws.task_family,
        task_id=task_id,
        working_set_id=ws_id,
        retrieval_trace_id=ws.retrieval_id,
        prompt_pack=prompt_pack,
        working_set=ws.model_dump(),
        verification_plan=verification_plan,
        cache=cache_info,
        duration_ms=duration_ms,
        debug=debug,
    )

    # Write to cache
    redis_layer.set(ws_key, response.model_dump(), ttl=redis_layer.TTL_WORKING_SET)

    return response


def _build_verification_plan(ws) -> VerificationPlan:
    test_files = [
        f.filepath for f in ws.files
        if "test" in (f.filepath or "").lower()
    ]
    has_py = any(
        (f.filepath or "").endswith(".py") for f in ws.files
    )
    return VerificationPlan(
        tests=test_files[:5],
        lint=has_py,
        typecheck=False,
        static_analysis=False,
    )
