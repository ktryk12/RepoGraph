"""STT adapter: transcribe_audio."""
from __future__ import annotations

from fastmcp import FastMCP

from ...runtime_manager import RuntimeManager, VoiceRuntimeError
from ..models import TranscribeResponse


def register(mcp: FastMCP, runtime: RuntimeManager) -> None:

    @mcp.tool(
        name="transcribe_audio",
        description=(
            "Transcribe base64-encoded WAV audio to text. "
            "Returns the transcript, detected language, and duration in milliseconds."
        ),
    )
    def transcribe_audio(audio_b64: str, language: str = "da") -> dict:
        """
        Args:
            audio_b64: Base64-encoded WAV audio bytes.
            language: BCP-47 language hint (e.g. 'da', 'en'). Defaults to 'da'.
        """
        try:
            result = runtime.transcribe(audio_b64, language=language)
        except VoiceRuntimeError as exc:
            raise ValueError(str(exc)) from exc
        return TranscribeResponse(**result).model_dump()
