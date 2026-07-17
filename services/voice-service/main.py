"""voice-runtime — FastAPI service with HTTP API and MCP server (SSE).

MCP endpoint: GET  http://voice-runtime:7080/mcp/sse
              POST http://voice-runtime:7080/mcp/messages/
HTTP healthcheck: GET http://voice-runtime:7080/health
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .runtime_manager import RuntimeManager
from .settings import load_settings
from .mcp_server.server import create_mcp_server

logger = logging.getLogger(__name__)

settings = load_settings()
runtime = RuntimeManager(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime.initialize()
    yield
    runtime.shutdown()


app = FastAPI(title="voice-runtime", version="1.0.0", lifespan=lifespan)

# ── MCP server (SSE transport, mounted at /mcp) ─────────────────────────────
_mcp = create_mcp_server(runtime, settings)
app.mount("/mcp", _mcp.http_app(transport="sse"))


# ── HTTP API ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "service": "voice-runtime"})


@app.get("/ready")
async def ready() -> JSONResponse:
    r = runtime.readiness
    return JSONResponse(
        {"ready": r.ready, "stt_loaded": r.stt_loaded, "tts_loaded": r.tts_loaded},
        status_code=200 if r.ready else 503,
    )


@app.get("/capabilities")
async def capabilities() -> JSONResponse:
    return JSONResponse(runtime.get_capabilities())


@app.post("/v1/transcribe")
async def transcribe(body: dict[str, Any]) -> JSONResponse:
    audio_b64 = str(body.get("audio_b64", ""))
    language = str(body.get("language", "da"))
    result = runtime.transcribe(audio_b64, language=language)
    return JSONResponse(result)


@app.post("/v1/synthesize")
async def synthesize(body: dict[str, Any]) -> JSONResponse:
    text = str(body.get("text", ""))
    language = str(body.get("language", "da"))
    result = runtime.synthesize(text, language=language)
    return JSONResponse(result)


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    uvicorn.run("voice_runtime.main:app", host=settings.host, port=settings.port, reload=False)
