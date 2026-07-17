"""Status adapters: health, ready, get_runtime_capabilities, validate_runtime_assets."""
from __future__ import annotations

from fastmcp import FastMCP

from ...runtime_manager import RuntimeManager, VoiceRuntimeError
from ...settings import Settings
from ..models import AssetValidationResponse, CapabilitiesResponse, HealthResponse, ReadyResponse


def register(mcp: FastMCP, runtime: RuntimeManager, settings: Settings) -> None:

    @mcp.tool(name="health", description="Check whether the voice-runtime process is alive.")
    def health() -> dict:
        return HealthResponse(ok=True).model_dump()

    @mcp.tool(
        name="ready",
        description=(
            "Check whether the runtime is ready to handle STT/TTS requests. "
            "Call this before transcribe_audio or synthesize_speech."
        ),
    )
    def ready() -> dict:
        r = runtime.readiness
        return ReadyResponse(
            ready=r.ready,
            stt_loaded=r.stt_loaded,
            tts_loaded=r.tts_loaded,
            error=r.error,
        ).model_dump()

    @mcp.tool(
        name="get_runtime_capabilities",
        description="Return supported STT and TTS models, devices, and languages.",
    )
    def get_runtime_capabilities() -> dict:
        caps = runtime.get_capabilities()
        return CapabilitiesResponse(**caps).model_dump()

    @mcp.tool(
        name="validate_runtime_assets",
        description="Verify that model asset directories exist on disk.",
    )
    def validate_runtime_assets() -> dict:
        result = runtime.validate_assets()
        return AssetValidationResponse(**result).model_dump()
