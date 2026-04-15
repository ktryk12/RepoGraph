"""RepoGraph MCP stdio server."""

from __future__ import annotations

from typing import Any
import os

from fastapi import HTTPException
from mcp.server.fastmcp import FastMCP

from repograph.api.routes import (
    IndexRequest,
    blast_radius as api_blast_radius,
    blast_radius_with_context as api_blast_radius_with_context,
    index_repo as api_index_repo,
    status as api_status,
    symbol_detail as api_symbol_detail,
    symbols as api_symbols,
    search_notes as api_search_notes,
    notes_for_symbol as api_notes_for_symbol,
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
