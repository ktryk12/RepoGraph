"""Pipeline adapter: convert_speech (STT → TTS in one call)."""
from __future__ import annotations

from fastmcp import FastMCP

from ...runtime_manager import RuntimeManager, VoiceRuntimeError
from ..models import ConvertSpeechResponse


def register(mcp: FastMCP, runtime: RuntimeManager) -> None:

    @mcp.tool(
        name="convert_speech",
        description=(
            "Transcribe audio and re-synthesize it in a target language in a single call. "
            "Useful for speech-to-speech translation or voice pipeline testing."
        ),
    )
    def convert_speech(
        audio_b64: str,
        source_language: str = "da",
        target_language: str = "en",
    ) -> dict:
        """
        Args:
            audio_b64: Base64-encoded WAV audio bytes to transcribe.
            source_language: BCP-47 language of the input audio (e.g. 'da').
            target_language: BCP-47 language for synthesized output (e.g. 'en').
        """
        try:
            stt_result = runtime.transcribe(audio_b64, language=source_language)
            transcript = stt_result["text"]
            tts_result = runtime.synthesize(transcript, language=target_language)
        except VoiceRuntimeError as exc:
            raise ValueError(str(exc)) from exc

        return ConvertSpeechResponse(
            transcript=transcript,
            audio_b64=tts_result["audio_b64"],
            source_language=source_language,
            target_language=target_language,
            sample_rate=tts_result["sample_rate"],
        ).model_dump()
