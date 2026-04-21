"""RepoGraph MCP stdio server."""

from __future__ import annotations

from typing import Any
import os

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

from repograph.api.routes import (
    ClassifyRequest,
    CoarseRequest,
    IndexRequest,
    RetrieveRequest,
    VerifyRequest,
    WorkingSetRequest,
    blast_radius as api_blast_radius,
    blast_radius_with_context as api_blast_radius_with_context,
    index_repo as api_index_repo,
    multi_stage_retrieve as api_retrieve,
    notes_for_symbol as api_notes_for_symbol,
    read_file_summary as api_file_summary,
    read_symbol_summary as api_symbol_summary,
    retrieve_coarse as api_coarse,
    search_notes as api_search_notes,
    status as api_status,
    symbol_detail as api_symbol_detail,
    symbol_enrichment as api_symbol_enrichment,
    symbols as api_symbols,
    task_classify as api_classify,
    verify_patch_plan as api_verify,
    build_working_set as api_working_set,
)

mcp = FastMCP(
    name="RepoGraph",
    instructions="Index repositories and query code graph symbols, callers, callees, and blast radius.",
)

TENANT_ID = os.getenv("REPOGRAPH_TENANT_ID")


def index_repo_impl(repo_path: str, force: bool = False) -> str:
    result = _handle_api_call(api_index_repo, IndexRequest(repo_path=repo_path, force=force), x_tenant_id=TENANT_ID)
    return (
        f"Indexed {result['files_indexed']} files and added {result['triples_added']} triples "
        f"in {result['duration_ms']} ms."
    )


def search_symbols_impl(query: str, limit: int = 20) -> list[str]:
    result = _handle_api_call(api_symbols, q=query, limit=limit, x_tenant_id=TENANT_ID)
    return result["symbols"]


def get_symbol_impl(symbol: str) -> dict[str, Any]:
    return _handle_api_call(api_symbol_detail, symbol_path=symbol, x_tenant_id=TENANT_ID)


def blast_radius_impl(symbol: str, depth: int = 3) -> dict[str, Any]:
    return _handle_api_call(api_blast_radius, symbol_path=symbol, depth=depth, x_tenant_id=TENANT_ID)


def repo_status_impl() -> dict[str, Any]:
    return _handle_api_call(api_status, x_tenant_id=TENANT_ID)


def search_notes_impl(query: str) -> dict[str, Any]:
    return _handle_api_call(api_search_notes, q=query)


def get_notes_for_symbol_impl(symbol: str) -> dict[str, Any]:
    return _handle_api_call(api_notes_for_symbol, symbol_path=symbol)


def get_symbol_context_impl(symbol: str) -> dict[str, Any]:
    return _handle_api_call(api_blast_radius_with_context, symbol_path=symbol, x_tenant_id=TENANT_ID)


@mcp.tool(name="index_repo")
def index_repo(repo_path: str, force: bool = False) -> str:
    """Index a repository and return a short status message."""
    return index_repo_impl(repo_path=repo_path, force=force)


@mcp.tool(name="search_symbols")
def search_symbols(query: str, limit: int = 20) -> list[str]:
    """Search indexed symbol IDs."""
    return search_symbols_impl(query=query, limit=limit)


@mcp.tool(name="get_symbol")
def get_symbol(symbol: str) -> dict[str, Any]:
    """Return file, line, callers, callees, and ownership details for one symbol."""
    return get_symbol_impl(symbol=symbol)


@mcp.tool(name="blast_radius")
def blast_radius(symbol: str, depth: int = 3) -> dict[str, Any]:
    """Return reverse CALLS impact for a symbol."""
    return blast_radius_impl(symbol=symbol, depth=depth)


@mcp.tool(name="repo_status")
def repo_status() -> dict[str, Any]:
    """Return graph statistics and indexing metadata."""
    return repo_status_impl()


@mcp.tool(name="search_notes")
def search_notes(query: str) -> dict[str, Any]:
    """Search for notes related to architecture, decisions, or text in Obsidian."""
    return search_notes_impl(query=query)


@mcp.tool(name="get_notes_for_symbol")
def get_notes_for_symbol(symbol: str) -> dict[str, Any]:
    """Search for notes specifically documenting a symbol from Obsidian."""
    return get_notes_for_symbol_impl(symbol=symbol)


@mcp.tool(name="get_symbol_context")
def get_symbol_context(symbol: str) -> dict[str, Any]:
    """Return both reverse CALLS impact for a symbol and related context from Obsidian."""
    return get_symbol_context_impl(symbol=symbol)


# --- Fase 9: 6 new MCP tools ---

@mcp.tool(name="classify_task")
def classify_task(query: str, hint: str | None = None) -> dict[str, Any]:
    """Classify a free-text query into one of 9 task families with retrieval defaults."""
    return _handle_api_call(api_classify, ClassifyRequest(query=query, hint=hint))


@mcp.tool(name="find_relevant_symbols")
def find_relevant_symbols(query: str, task_family: str | None = None, limit: int = 40) -> dict[str, Any]:
    """Coarse retrieval — return candidate symbols for a query."""
    return _handle_api_call(api_coarse, CoarseRequest(query=query, task_family=task_family, limit=limit), x_tenant_id=TENANT_ID)


@mcp.tool(name="build_working_set")
def build_working_set(query: str, task_hint: str | None = None, token_budget: int = 4096, format: str = "compact") -> dict[str, Any]:
    """Full multi-stage retrieval → token-budget-aware WorkingSet. Use format='prompt' to get LLM-ready context."""
    return _handle_api_call(
        api_working_set,
        WorkingSetRequest(query=query, task_hint=task_hint, token_budget=token_budget, format=format),
        x_tenant_id=TENANT_ID,
    )


@mcp.tool(name="get_symbol_summary")
def get_symbol_summary(symbol: str) -> dict[str, Any]:
    """Return stored summary + enrichment (signature, risk, service) for a symbol."""
    enrichment = _handle_api_call(api_symbol_enrichment, symbol_path=symbol, x_tenant_id=TENANT_ID)
    summary = _handle_api_call(api_symbol_summary, symbol_path=symbol, x_tenant_id=TENANT_ID)
    return {**enrichment, "summary": summary.get("summary")}


@mcp.tool(name="get_file_summary")
def get_file_summary(filepath: str) -> dict[str, Any]:
    """Return stored L2 file summary for a filepath."""
    return _handle_api_call(api_file_summary, filepath=filepath, x_tenant_id=TENANT_ID)


@mcp.tool(name="verify_task_context")
def verify_task_context(repo_path: str, files: list[str], task_id: str | None = None, steps: list[str] | None = None) -> dict[str, Any]:
    """Run verification toolchain (lint, type check, tests) on changed files. Pass task_id to update TaskMemory."""
    return _handle_api_call(
        api_verify,
        VerifyRequest(repo_path=repo_path, files=files, task_id=task_id, steps=steps),
        x_tenant_id=TENANT_ID,
    )


@mcp.tool(name="multi_stage_retrieve")
def multi_stage_retrieve(
    query: str,
    task_hint: str | None = None,
    token_budget: int = 4096,
    coarse_limit: int = 40,
    expand_limit: int = 80,
    persist_trace: bool = True,
) -> dict[str, Any]:
    """Full multi-stage retrieval pipeline: classify → coarse → structural expansion → fine selection.
    Returns a ranked working set of symbols within the token budget."""
    return _handle_api_call(
        api_retrieve,
        RetrieveRequest(
            query=query,
            task_hint=task_hint,
            token_budget=token_budget,
            coarse_limit=coarse_limit,
            expand_limit=expand_limit,
            persist_trace=persist_trace,
        ),
        x_tenant_id=TENANT_ID,
    )


@mcp.tool(name="prepare_task_context")
def prepare_task_context(
    repo_path: str,
    query: str,
    task_hint: str | None = None,
    output_profile: str = "small",
    target_context: int = 4096,
    consumer: str = "generic",
    include_debug: bool = False,
) -> dict[str, Any]:
    """Full shared retrieval: classify → retrieve → pack. Returns consumer-ready prompt + working_set."""
    from repograph.shared_retrieval import SharedRetrievalRequest, prepare_task_context as _prepare
    from repograph.shared_retrieval.adapters import format_for_consumer
    from repograph.graph import get_graph_store
    store = get_graph_store(backend=os.getenv("REPOGRAPH_DB_BACKEND", "cog"), db_path=os.getenv("REPOGRAPH_DB_PATH", ".repograph"))
    req = SharedRetrievalRequest(
        repo_path=repo_path, query=query, task_hint=task_hint,
        output_profile=output_profile, target_context=target_context,
        consumer=consumer, include_debug=include_debug,
        tenant_id=TENANT_ID or "default",
    )
    response = _prepare(req, store)
    return format_for_consumer(response, consumer)


@mcp.tool(name="build_prompt_pack")
def build_prompt_pack(
    repo_path: str,
    query: str,
    output_profile: str = "small",
    target_context: int = 4096,
) -> dict[str, Any]:
    """Build a PromptPack from a query. Returns preamble, objective, context_blocks, and token estimate."""
    from repograph.shared_retrieval import SharedRetrievalRequest, prepare_task_context as _prepare
    from repograph.graph import get_graph_store
    store = get_graph_store(backend=os.getenv("REPOGRAPH_DB_BACKEND", "cog"), db_path=os.getenv("REPOGRAPH_DB_PATH", ".repograph"))
    req = SharedRetrievalRequest(
        repo_path=repo_path, query=query,
        output_profile=output_profile, target_context=target_context,
        tenant_id=TENANT_ID or "default",
    )
    response = _prepare(req, store)
    return response.prompt_pack.model_dump()


@mcp.tool(name="build_retry_pack")
def build_retry_pack(
    repo_path: str,
    query: str,
    failure_reason: str,
    previous_diff: str | None = None,
    target_context: int = 4096,
) -> dict[str, Any]:
    """Pack context for a retry after verification failure. Prepends failure reason + diff to patch_first context."""
    from repograph.shared_retrieval import SharedRetrievalRequest, prepare_task_context as _prepare
    from repograph.shared_retrieval.profiles import resolve_profile
    from repograph.shared_retrieval.prompt_packer import pack
    from repograph.working_set.builder import build as build_ws
    from repograph.graph import get_graph_store
    store = get_graph_store(backend=os.getenv("REPOGRAPH_DB_BACKEND", "cog"), db_path=os.getenv("REPOGRAPH_DB_PATH", ".repograph"))
    req = SharedRetrievalRequest(
        repo_path=repo_path, query=query, target_context=target_context,
        output_profile="patch", tenant_id=TENANT_ID or "default",
    )
    ws = build_ws(query=query, store=store, token_budget=target_context)
    profile = resolve_profile("patch", target_context)
    retry_pack = pack(ws, profile, failure_reason=failure_reason, previous_diff=previous_diff)
    return retry_pack.model_dump()


@mcp.tool(name="get_repo_summary")
def get_repo_summary() -> dict[str, Any]:
    """Return the stored repo-level summary (L0)."""
    from repograph.api.routes import _get_store
    store = _get_store(TENANT_ID)
    from repograph.indexer.schema import REPO_SUMMARY
    summary = store.first_outgoing("__repo__", REPO_SUMMARY)
    return {"summary": summary}


@mcp.tool(name="get_service_summary")
def get_service_summary(service: str) -> dict[str, Any]:
    """Return the stored service-level summary."""
    from repograph.api.routes import _get_store
    store = _get_store(TENANT_ID)
    from repograph.indexer.schema import SERVICE_SUMMARY
    summary = store.first_outgoing(f"service:{service}", SERVICE_SUMMARY)
    return {"service": service, "summary": summary}


@mcp.tool(name="get_task_memory")
def get_task_memory(task_id: str) -> dict[str, Any]:
    """Retrieve TaskMemory record for a task_id."""
    from repograph.api.routes import _get_store
    from repograph.memory.store import load as memory_load
    store = _get_store(TENANT_ID)
    record = memory_load(store, task_id)
    return record.model_dump() if record else {"task_id": task_id, "found": False}


@mcp.tool(name="update_task_memory")
def update_task_memory(task_id: str, patch: dict) -> dict[str, Any]:
    """Update TaskMemory with new fields (e.g. after a patch attempt)."""
    from repograph.api.routes import _get_store
    from repograph.memory.store import load as memory_load, save as memory_save
    store = _get_store(TENANT_ID)
    record = memory_load(store, task_id)
    if record is None:
        return {"error": f"task {task_id} not found"}
    updated = record.model_copy(update=patch)
    memory_save(store, updated)
    return updated.model_dump()


@mcp.tool(name="invalidate_context_cache")
def invalidate_context_cache(repo_path: str) -> dict[str, Any]:
    """Invalidate all Redis cached entries for a repo (force fresh retrieval on next call)."""
    from repograph.cache import redis as redis_layer
    from repograph.cache import keys as cache_keys
    tenant = TENANT_ID or "default"
    prefix = cache_keys._repo_prefix(tenant, repo_path)
    deleted = redis_layer.delete_pattern(f"{prefix}:*")
    return {"deleted_keys": deleted, "prefix": prefix}


def main() -> None:
    mcp.run(transport="stdio")


def _handle_api_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        raise RuntimeError(detail) from exc


if __name__ == "__main__":
    main()
