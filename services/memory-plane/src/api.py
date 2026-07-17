from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, TypeAlias

from embedder import Embedder
from store import MemoryStore
from postgresql_memory_store import PostgreSQLMemoryStore

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse
    import uvicorn
    _FASTAPI_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    FastAPI = None  # type: ignore
    HTTPException = Exception  # type: ignore
    Request: TypeAlias = Any
    JSONResponse: TypeAlias = Any
    uvicorn = None  # type: ignore
    _FASTAPI_AVAILABLE = False

_log = logging.getLogger(__name__)

_DB_PATH = os.environ.get("DB_PATH", "/data/memory_plane/memories.db")
_PORT = int(os.environ.get("MEMORY_PLANE_PORT", "8101"))

_store: Optional[MemoryStore | PostgreSQLMemoryStore] = None
_embedder: Optional[Embedder] = None


def _get_store() -> MemoryStore | PostgreSQLMemoryStore:
    global _store
    if _store is None:
        # Check if PostgreSQL backend is configured
        backend = os.environ.get("MEMORY_PLANE_STORE_BACKEND", "sqlite").lower()
        if backend == "postgresql":
            _log.info("Using PostgreSQL backend for memory-plane")
            _store = PostgreSQLMemoryStore()
        else:
            _log.info("Using SQLite backend for memory-plane: %s", _DB_PATH)
            _store = MemoryStore(_DB_PATH)
    return _store


def _get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


if _FASTAPI_AVAILABLE:
    app = FastAPI(title="memory-plane", version="1.0.0")

    @app.get("/health")
    async def health() -> JSONResponse:
        try:
            _get_store()
            db_status = "ok"
        except Exception:
            db_status = "error"
        return JSONResponse({"status": "ok", "db": db_status})

    @app.post("/memory/ingest")
    async def ingest(request: Request) -> JSONResponse:
        body: Dict[str, Any] = await request.json()
        content = str(body.get("content") or "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="content is required")
        source = str(body.get("source") or "unknown")
        entity_type = str(body.get("entity_type") or "")
        entity_id = str(body.get("entity_id") or "")
        metadata = body.get("metadata") or {}
        importance = float(body.get("importance") or 0.5)

        embedding = _get_embedder().embed(content)
        memory_id = _get_store().save(
            content=content,
            source=source,
            entity_type=entity_type,
            entity_id=entity_id,
            metadata=metadata,
            embedding=embedding if embedding else None,
            importance=importance,
        )
        status = "ok" if embedding else "no_embedding"
        return JSONResponse({"id": memory_id, "status": status})

    @app.post("/memory/search")
    async def search(request: Request) -> JSONResponse:
        body: Dict[str, Any] = await request.json()
        query = str(body.get("query") or "").strip()
        if not query:
            return JSONResponse({"results": [], "count": 0})
        top_k = int(body.get("top_k") or 10)
        entity_type = str(body.get("entity_type") or "") or None
        min_importance = float(body.get("min_importance") or 0.3)

        embedding = _get_embedder().embed(query)
        if not embedding:
            return JSONResponse({"results": [], "count": 0})
        results = _get_store().search(
            query_embedding=embedding,
            top_k=top_k,
            entity_type=entity_type,
            min_importance=min_importance,
        )
        return JSONResponse({"results": results, "count": len(results)})

    @app.get("/memory/entity/{entity_id}")
    async def get_by_entity(entity_id: str, limit: int = 20) -> JSONResponse:
        results = _get_store().get_by_entity(entity_id, limit=limit)
        return JSONResponse({"results": results, "count": len(results)})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db_path = _DB_PATH
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    _log.info("memory-plane starting on port %d, db=%s", _PORT, db_path)
    if not _FASTAPI_AVAILABLE:
        raise RuntimeError("fastapi and uvicorn are required to run memory-plane")
    uvicorn.run(app, host="0.0.0.0", port=_PORT)
