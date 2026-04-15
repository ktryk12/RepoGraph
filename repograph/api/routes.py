"""FastAPI routes for indexing and querying RepoGraph."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Header
from pydantic import BaseModel

from repograph import __version__
from repograph.graph import get_graph_store
from repograph.graph.factory import GraphStore
from repograph.indexer import parse_file, walk
from repograph.indexer.schema import AT_LINE, DEFINES, IN_FILE
from repograph.connectors.obsidian.service import ObsidianService

DEFAULT_DB_BACKEND = os.getenv("REPOGRAPH_DB_BACKEND", "cog")
DEFAULT_DB_PATH = os.getenv("REPOGRAPH_DB_PATH", ".repograph")

router = APIRouter()
obsidian_service = ObsidianService()


class IndexRequest(BaseModel):
    repo_path: str
    force: bool = False


def create_app() -> FastAPI:
    app = FastAPI(title="RepoGraph", version=__version__)
    app.include_router(router)
    return app


@router.post("/index")
def index_repo(request: IndexRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    start = time.perf_counter()
    repo_path = Path(request.repo_path).expanduser().resolve()
    store = _get_store(x_tenant_id)
    metadata = _load_metadata(store)

    if metadata["repo_path"] and metadata["repo_path"] != str(repo_path) and not request.force:
        raise HTTPException(
            status_code=409,
            detail="Graph already contains a different repo. Re-run with force=true to replace it.",
        )

    try:
        if request.force:
            store.clear()

        files_indexed = 0
        triples_added = 0
        for filepath, language in walk(str(repo_path)):
            triples = parse_file(filepath, language, repo_path=repo_path)
            if triples:
                store.put_triples_batch(triples)
                triples_added += len(triples)
            files_indexed += 1
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {exc}") from exc

    last_indexed = _utc_now_iso()
    _save_metadata(
        store,
        {
            "repo_path": str(repo_path),
            "last_indexed": last_indexed,
        },
    )

    duration_ms = int((time.perf_counter() - start) * 1000)
    return {
        "status": "ok",
        "files_indexed": files_indexed,
        "triples_added": triples_added,
        "duration_ms": duration_ms,
    }


@router.get("/status")
def status(x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    store = _get_store(x_tenant_id)
    metadata = _load_metadata(store)
    stats = store.stats()
    return {
        "indexed": metadata["last_indexed"] is not None,
        "repo_path": metadata["repo_path"],
        "node_count": stats["node_count"],
        "last_indexed": metadata["last_indexed"],
    }


@router.get("/symbols")
def symbols(q: str, limit: int = 20, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, list[str]]:
    store = _get_store(x_tenant_id)
    return {"symbols": store.search(q, limit=limit)}


@router.get("/symbol/{symbol_path}")
def symbol_detail(symbol_path: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    store = _get_store(x_tenant_id)
    symbol_exists = store.has_symbol(symbol_path)
    in_file = store.first_outgoing(symbol_path, IN_FILE)
    at_line = store.first_outgoing(symbol_path, AT_LINE)
    calls = store.callees_of(symbol_path)
    called_by = store.callers_of(symbol_path)
    defines = store.outgoing(symbol_path, DEFINES)
    defined_by = _first_or_none(store.incoming(symbol_path, DEFINES))

    if not symbol_exists and not any([in_file, at_line, calls, called_by, defines, defined_by]):
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol_path}")

    return {
        "symbol": symbol_path,
        "in_file": in_file,
        "at_line": at_line,
        "calls": calls,
        "called_by": called_by,
        "defines": defines,
        "defined_by": defined_by,
    }


@router.get("/blast-radius/{symbol_path}")
def blast_radius(symbol_path: str, depth: int = 3, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    store = _get_store(x_tenant_id)
    return {
        "symbol": symbol_path,
        "depth": depth,
        "affected": store.blast_radius(symbol_path, depth=depth),
    }


@router.get("/blast-radius-with-context/{symbol_path}")
def blast_radius_with_context(symbol_path: str, depth: int = 3, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    base = blast_radius(symbol_path, depth=depth, x_tenant_id=x_tenant_id)
    result = obsidian_service.search_notes_by_symbol(symbol_path)
    base["notes_context"] = {
        "status": result.status,
        "notes": [n.model_dump() for n in result.notes]
    }
    return base


@router.get("/notes/search")
def search_notes(q: str) -> dict[str, Any]:
    result = obsidian_service.search_notes_by_query(q)
    return {"notes": [n.model_dump() for n in result.notes], "status": result.status}


@router.get("/notes/for-symbol/{symbol_path}")
def notes_for_symbol(symbol_path: str) -> dict[str, Any]:
    result = obsidian_service.search_notes_by_symbol(symbol_path)
    return {"notes": [n.model_dump() for n in result.notes], "status": result.status}


@router.get("/file/{filepath:path}")
def file_detail(filepath: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    store = _get_store(x_tenant_id)
    return {
        "filepath": filepath,
        "symbols": store.file_symbols(filepath),
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


def _get_store(tenant_id: str | None = None) -> GraphStore:
    db_path = DEFAULT_DB_PATH
    if tenant_id and DEFAULT_DB_BACKEND == "cog":
        db_path = f"{DEFAULT_DB_PATH}_{tenant_id}"

    return get_graph_store(
        backend=DEFAULT_DB_BACKEND,
        db_path=db_path,
    )


def _load_metadata(store: GraphStore) -> dict[str, str | None]:
    return store.load_metadata()


def _save_metadata(store: GraphStore, metadata: dict[str, str]) -> None:
    store.save_metadata(metadata)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _first_or_none(values: list[str]) -> str | None:
    return values[0] if values else None


app = create_app()
