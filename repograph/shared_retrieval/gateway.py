"""SharedRetrievalGateway — prepare_task_context() orchestrator."""

from __future__ import annotations

import time
import uuid
from pathlib import PurePosixPath

from repograph.cache import keys as cache_keys
from repograph.cache import redis as redis_layer
from repograph.graph.factory import GraphStore
from repograph.postgres import tracer as pg_tracer
from repograph.token_budget import BudgetRequest, get_engine
from repograph.working_set.builder import build as build_working_set
from repograph.retrieval.task_planner import classify

from .analysis import build_analysis_plan, request_for_analysis_step, select_analysis_step, should_break_down_for_analysis
from .compressor import compress
from .models import (
    CacheInfo,
    SharedRetrievalRequest,
    SharedRetrievalResponse,
    VerificationPlan,
)
from .profiles import resolve_profile
from .prompt_packer import pack


def prepare_task_context(
    req: SharedRetrievalRequest,
    store: GraphStore,
) -> SharedRetrievalResponse:
    task_family = classify(req.query, req.task_hint)
    analysis_plan = None
    analysis_step = None
    effective_req = req
    if req.include_analysis_plan and should_break_down_for_analysis(req, task_family):
        analysis_plan = build_analysis_plan(req)
        analysis_step = select_analysis_step(analysis_plan, req.analysis_step_id)
        effective_req = request_for_analysis_step(req, analysis_step)

    response = _prepare_task_context_base(effective_req, store)
    if analysis_plan is None or analysis_step is None:
        return response
    return response.model_copy(
        update={
            "analysis_plan": analysis_plan,
            "analysis_step_id": analysis_step.step_id,
            "analysis_step_kind": analysis_step.step_kind,
        }
    )


def _prepare_task_context_base(
    req: SharedRetrievalRequest,
    store: GraphStore,
) -> SharedRetrievalResponse:
    t0 = time.perf_counter()

    profile = resolve_profile(req.output_profile, req.target_context)
    engine = get_engine(req.target_model)
    budget = engine.calculate(
        BudgetRequest(
            total_context=profile.target_context,
            target_model=req.target_model,
            system_instructions=req.system_instructions,
            required_tool_schemas=req.required_tool_schemas,
            active_task_memory=req.active_task_memory,
            tool_results=req.tool_results,
            reserved_output_tokens=req.reserved_output_tokens,
            safety_margin_tokens=req.safety_margin_tokens,
            safety_margin_ratio=req.safety_margin_ratio,
        )
    )
    retrieval_budget = budget.available_retrieval_tokens
    profile = resolve_profile(req.output_profile, retrieval_budget)
    repo_revision, content_hash = _repository_identity(req, store)

    # Cache lookup
    q_hash = cache_keys.query_hash(
        req.query,
        req.output_profile,
        retrieval_budget,
        repo_revision=repo_revision,
        content_hash=content_hash,
        session_id=req.session_id,
        task_hint=req.task_hint,
        target_model=req.target_model,
        consumer=req.consumer,
        adapter_version=req.adapter_version,
        analysis_step_id=req.analysis_step_id,
    )
    ws_key = cache_keys.working_set(req.tenant_id, req.repo_path, q_hash)

    cache_info = CacheInfo()

    if not req.force_refresh:
        cached = redis_layer.get(ws_key)
        if cached is not None:
            cached["cache"] = CacheInfo(used=True, keys_hit=[ws_key]).model_dump()
            response = SharedRetrievalResponse(**cached)
            cached_tokens = response.prompt_pack.total_tokens
            baseline_tokens = max(req.baseline_tokens or 0, cached_tokens)
            pg_tracer.log_retrieval_trace(
                retrieval_id=f"retrieval:{uuid.uuid4()}",
                tenant_id=req.tenant_id,
                query=req.query,
                task_family=response.task_family,
                token_budget=retrieval_budget,
                token_estimate=cached_tokens,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                consumer=req.consumer,
                compressor_strategy=response.working_set.get("compression", "none"),
                pre_compress_tokens=baseline_tokens,
                post_compress_tokens=cached_tokens,
                baseline_tokens=baseline_tokens,
                saved_tokens_vs_baseline=max(0, baseline_tokens - cached_tokens),
                cache_hit=True,
                cache_saved_tokens=cached_tokens,
                reused_tokens=cached_tokens,
                repo_revision=repo_revision,
                content_hash=content_hash,
                session_id=req.session_id,
                task_hint=req.task_hint,
                target_model=req.target_model,
                adapter_version=req.adapter_version,
                analysis_step_id=req.analysis_step_id,
                tokenizer_profile=engine.profile.name,
            )
            return response

    # Build WorkingSet
    ws = build_working_set(
        query=req.query,
        store=store,
        task_hint=req.task_hint,
        token_budget=retrieval_budget,
        target_model=req.target_model,
    )

    # Compress to budget (structural, no LLM)
    compressed = compress(ws, profile.target_context, req.target_model)
    ws = ws.model_copy(update={
        "symbols": compressed.symbols,
        "token_estimate": compressed.post_compress_tokens,
        "compression": compressed.strategy_applied,
    })

    # Pack context blocks
    prompt_pack = pack(ws, profile, target_model=req.target_model)

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
        token_budget=retrieval_budget,
        token_estimate=prompt_pack.total_tokens,
        duration_ms=duration_ms,
        consumer=req.consumer,
        compressor_strategy=compressed.strategy_applied,
        pre_compress_tokens=compressed.pre_compress_tokens,
        post_compress_tokens=compressed.post_compress_tokens,
        baseline_tokens=max(req.baseline_tokens or 0, compressed.pre_compress_tokens, prompt_pack.total_tokens),
        saved_tokens_vs_baseline=max(
            0,
            max(req.baseline_tokens or 0, compressed.pre_compress_tokens, prompt_pack.total_tokens)
            - prompt_pack.total_tokens,
        ),
        cache_hit=False,
        reused_tokens=0,
        repo_revision=repo_revision,
        content_hash=content_hash,
        session_id=req.session_id,
        task_hint=req.task_hint,
        target_model=req.target_model,
        adapter_version=req.adapter_version,
        analysis_step_id=req.analysis_step_id,
        tokenizer_profile=engine.profile.name,
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
            "tokenizer_profile": engine.profile.name,
            "tokenizer_exact": engine.exact,
            "budget": {
                "total_context": budget.total_context,
                "components": budget.component_tokens,
                "reserved_output_tokens": budget.reserved_output_tokens,
                "safety_margin_tokens": budget.safety_margin_tokens,
                "available_retrieval_tokens": budget.available_retrieval_tokens,
            },
            "repo_revision": repo_revision,
            "content_hash": content_hash,
        }

    response = SharedRetrievalResponse(
        task_family=ws.task_family,
        task_id=task_id,
        working_set_id=ws_id,
        retrieval_trace_id=ws.retrieval_id,
        consumer=req.consumer,
        source_mode="shared_retrieval",
        payload_mode="retrieval_envelope",
        prompt_assembly_owner="consumer",
        prompt_pack=prompt_pack,
        working_set=ws.model_dump(),
        verification_plan=verification_plan,
        verification_plan_available=bool(
            verification_plan.tests
            or verification_plan.lint
            or verification_plan.typecheck
            or verification_plan.static_analysis
        ),
        retry_pack_available=False,
        task_memory_refs=[],
        cache=cache_info,
        duration_ms=duration_ms,
        debug=debug,
    )

    # Write to cache
    redis_layer.set(ws_key, response.model_dump(exclude_none=True), ttl=redis_layer.TTL_WORKING_SET)

    return response


def _repository_identity(req: SharedRetrievalRequest, store: GraphStore) -> tuple[str, str]:
    metadata = store.load_metadata()
    revision = req.repo_revision or metadata.get("git_revision") or metadata.get("last_indexed") or "unversioned"
    content_hash = req.content_hash or metadata.get("content_hash") or revision
    return str(revision), str(content_hash)


def _build_verification_plan(ws) -> VerificationPlan:
    test_files = [
        f.filepath for f in ws.files
        if _is_test_file(f.filepath or "")
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


def _is_test_file(filepath: str) -> bool:
    normalized = filepath.replace("\\", "/").strip("/")
    if not normalized:
        return False
    path = PurePosixPath(normalized)
    filename = path.name.lower()
    if filename.startswith("test_") or filename.endswith("_test.py"):
        return True
    return any(part.lower() == "tests" for part in path.parts)
