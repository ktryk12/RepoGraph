"""RuntimeManager — owns model lifecycle for STT and TTS.

Both models are loaded once during initialize() and reused for all requests.
readiness reflects whether initialize() completed without error.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class ReadinessState:
    ready: bool = False
    error: str | None = None
    initialized_at: float | None = None
    stt_loaded: bool = False
    tts_loaded: bool = False


class VoiceRuntimeError(RuntimeError):
    pass


class RuntimeManager:
    """Owns STT + TTS model instances and exposes a minimal API.

    Callers (MCP adapters, HTTP handlers) never touch model objects directly.
    All I/O uses base64-encoded WAV bytes so no file paths leak through the API.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._stt: Any = None
        self._tts: Any = None
        self.readiness = ReadinessState()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Load models. Idempotent — safe to call more than once."""
        if self.readiness.ready:
            return
        logger.info("RuntimeManager: initializing STT and TTS models")
        t0 = time.monotonic()
        try:
            self._stt = self._load_stt()
            self.readiness.stt_loaded = True
            logger.info("RuntimeManager: STT loaded (model=%s device=%s)",
                        self._settings.stt_model, self._settings.stt_device)

            self._tts = self._load_tts()
            self.readiness.tts_loaded = True
            logger.info("RuntimeManager: TTS loaded (model=%s device=%s)",
                        self._settings.tts_model, self._settings.tts_device)

            elapsed = time.monotonic() - t0
            self.readiness.ready = True
            self.readiness.initialized_at = time.time()
            logger.info("RuntimeManager: ready after %.1fs", elapsed)
        except Exception as exc:
            self.readiness.ready = False
            self.readiness.error = str(exc)
            logger.error("RuntimeManager: initialization failed: %s", exc)
            raise

    def shutdown(self) -> None:
        self._stt = None
        self._tts = None
        self.readiness.ready = False
        logger.info("RuntimeManager: shut down")

    # ------------------------------------------------------------------
    # STT
    # ------------------------------------------------------------------

    def transcribe(self, audio_b64: str, *, language: str = "da") -> dict[str, Any]:
        """Transcribe base64-encoded WAV audio. Returns {text, language, duration_ms}."""
        self._require_ready()
        audio_bytes = base64.b64decode(audio_b64)
        try:
            result = self._stt.transcribe(audio_bytes, language=language)
        except Exception as exc:
            raise VoiceRuntimeError(f"transcribe failed: {exc}") from exc
        return {
            "text": result.get("text", ""),
            "language": result.get("language", language),
            "duration_ms": result.get("duration_ms", 0),
        }

    # ------------------------------------------------------------------
    # TTS
    # ------------------------------------------------------------------

    def synthesize(self, text: str, *, language: str = "da") -> dict[str, Any]:
        """Synthesize speech. Returns {audio_b64, sample_rate, duration_ms}."""
        self._require_ready()
        if not text.strip():
            raise VoiceRuntimeError("synthesize: text must not be empty")
        try:
            wav_bytes = self._tts.synthesize(
                text,
                language=language,
                speaker=self._settings.tts_speaker,
            )
        except Exception as exc:
            raise VoiceRuntimeError(f"synthesize failed: {exc}") from exc
        return {
            "audio_b64": base64.b64encode(wav_bytes).decode("ascii"),
            "sample_rate": 22050,
            "duration_ms": int(len(wav_bytes) / 2 / 22050 * 1000),
        }

    # ------------------------------------------------------------------
    # Capabilities & asset validation
    # ------------------------------------------------------------------

    def get_capabilities(self) -> dict[str, Any]:
        return {
            "stt": {
                "model": self._settings.stt_model,
                "device": self._settings.stt_device,
                "languages": ["da", "en", "de", "sv", "nb"],
            },
            "tts": {
                "model": self._settings.tts_model,
                "device": self._settings.tts_device,
                "speaker": self._settings.tts_speaker,
                "languages": ["da", "en", "de", "sv", "nb"],
            },
        }

    def validate_assets(self) -> dict[str, Any]:
        """Check that model asset directories exist."""
        assets_dir = Path(self._settings.model_assets_dir)
        issues: list[str] = []
        if not assets_dir.exists():
            issues.append(f"model_assets_dir not found: {assets_dir}")
        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "assets_dir": str(assets_dir),
        }

    # ------------------------------------------------------------------
    # Internal model loaders — swap out for real implementations
    # ------------------------------------------------------------------

    def _load_stt(self) -> Any:
        """Load Whisper STT model. Override or monkey-patch in tests."""
        try:
            import whisper  # type: ignore[import]
            model_name = os.path.basename(self._settings.stt_model)
            model = whisper.load_model(model_name, device=self._settings.stt_device)
            return _WhisperAdapter(model)
        except ImportError:
            logger.warning("whisper not installed — using stub STT")
            return _StubSTT()

    def _load_tts(self) -> Any:
        """Load Coqui XTTS model. Override or monkey-patch in tests."""
        try:
            from TTS.api import TTS  # type: ignore[import]
            tts = TTS(model_name=self._settings.tts_model, progress_bar=False)
            tts.to(self._settings.tts_device)
            return _CoquiTTSAdapter(tts)
        except ImportError:
            logger.warning("TTS (Coqui) not installed — using stub TTS")
            return _StubTTS()

    def _require_ready(self) -> None:
        if not self.readiness.ready:
            msg = self.readiness.error or "runtime not initialized"
            raise VoiceRuntimeError(f"runtime not ready: {msg}")


# ---------------------------------------------------------------------------
# Model adapters — thin wrappers that normalize library-specific APIs
# ---------------------------------------------------------------------------

class _WhisperAdapter:
    def __init__(self, model: Any) -> None:
        self._model = model

    def transcribe(self, audio_bytes: bytes, *, language: str) -> dict[str, Any]:
        import tempfile, wave, struct, io
        # whisper.transcribe takes a file path
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        try:
            result = self._model.transcribe(tmp_path, language=language)
            return {
                "text": result.get("text", "").strip(),
                "language": result.get("language", language),
                "duration_ms": 0,
            }
        finally:
            os.unlink(tmp_path)


class _CoquiTTSAdapter:
    def __init__(self, tts: Any) -> None:
        self._tts = tts

    def synthesize(self, text: str, *, language: str, speaker: str) -> bytes:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            out_path = tmp.name
        try:
            self._tts.tts_to_file(
                text=text,
                speaker=speaker,
                language=language,
                file_path=out_path,
            )
            return Path(out_path).read_bytes()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass


class _StubSTT:
    """Returns a fixed transcript — used when whisper is not installed."""

    def transcribe(self, audio_bytes: bytes, *, language: str) -> dict[str, Any]:
        return {"text": "[stub transcription]", "language": language, "duration_ms": 0}


class _StubTTS:
    """Returns a minimal valid WAV — used when Coqui TTS is not installed."""

    def synthesize(self, text: str, *, language: str, speaker: str) -> bytes:
        # 44-byte WAV header for 0 PCM samples, 22050 Hz, 16-bit mono
        import struct
        data_size = 0
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_size, b"WAVE",
            b"fmt ", 16, 1, 1, 22050, 44100, 2, 16,
            b"data", data_size,
        )
        return header
