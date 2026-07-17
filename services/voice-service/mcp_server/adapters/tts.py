"""TTS adapter: synthesize_speech."""
from __future__ import annotations

from fastmcp import FastMCP

from ...runtime_manager import RuntimeManager, VoiceRuntimeError
from ..models import SynthesizeResponse


def register(mcp: FastMCP, runtime: RuntimeManager) -> None:

    @mcp.tool(
        name="synthesize_speech",
        description=(
            "Convert text to speech. "
            "Returns base64-encoded WAV audio, sample rate, and duration in milliseconds."
        ),
    )
    def synthesize_speech(text: str, language: str = "da") -> dict:
        """
        Args:
            text: The text to synthesize. Must not be empty.
            language: BCP-47 language code (e.g. 'da', 'en'). Defaults to 'da'.
        """
        try:
            result = runtime.synthesize(text, language=language)
        except VoiceRuntimeError as exc:
            raise ValueError(str(exc)) from exc
        return SynthesizeResponse(**result).model_dump()
