"""FastMCP factory — wires all adapter registrations.

Usage in main.py:
    _mcp = create_mcp_server(runtime, settings)
    app.mount("/mcp", _mcp.http_app(transport="sse"))
"""
from __future__ import annotations

from fastmcp import FastMCP

from ..runtime_manager import RuntimeManager
from ..settings import Settings
from .adapters import status, stt, tts, pipeline


def create_mcp_server(runtime: RuntimeManager, settings: Settings) -> FastMCP:
    mcp = FastMCP(
        name="voice-runtime",
        instructions=(
            "Voice processing runtime. "
            "Call 'ready' before any STT/TTS tool to verify the runtime is initialized. "
            "Tools: health, ready, get_runtime_capabilities, validate_runtime_assets, "
            "transcribe_audio, synthesize_speech, convert_speech."
        ),
    )

    status.register(mcp, runtime, settings)
    stt.register(mcp, runtime)
    tts.register(mcp, runtime)
    pipeline.register(mcp, runtime)

    return mcp
