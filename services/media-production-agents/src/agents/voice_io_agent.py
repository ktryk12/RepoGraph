"""VoiceIOAgent – to-vejs voice bridge til BabyAI pipeline.

Ansvarsområder:
  A) INPUT:  Lytter kontinuerligt via ScreenReader → STT → publisher VOICE_INPUT
  B) OUTPUT: Modtager VOICE_OUTPUT besked → TTS via VoiceServiceClient → afspiller audio

Design-principper:
  - Aldrig crash hele agenten ved voice-fejl (log + fortsæt)
  - Graceful degradation: virker normalt uden voice service
  - Blocking I/O køres i executor (ingen event-loop blokering)
  - ScreenReader og VoiceServiceClient er injectable (testbart)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from babyai_shared.bus.protocol import Message, MessageType

logger = logging.getLogger(__name__)

_LISTEN_CHUNK_SECONDS = 3.0
_CONSUME_POLL_SECONDS = 0.05
_FALLBACK_AUDIO_PATH = "/tmp/babyai_voice_output.wav"


# ---------------------------------------------------------------------------
# Protocols for injectable dependencies
# ---------------------------------------------------------------------------


class _ScreenReaderLike(Protocol):
    """Minimal interface — ScreenReader fra babyai.tools.screen_reader eller mock."""

    def capture_and_transcribe(self, duration_seconds: float) -> Any:
        ...


class _VoiceClientLike(Protocol):
    """Minimal interface — VoiceServiceClient fra babyai.voice.voice_service_client."""

    def is_available(self) -> bool:
        ...

    def speak(self, text: str, voice_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
        ...

    def transcribe(self, audio_path: str, **kwargs: Any) -> str:
        ...


class _BusLike(Protocol):
    """Minimal message bus interface — MessageBus fra bus.message_bus."""

    def publish(self, message: Message) -> None:
        ...

    def consume(self, max_messages: int = 1):
        ...


# ---------------------------------------------------------------------------
# VoiceIOAgent
# ---------------------------------------------------------------------------


class VoiceIOAgent:
    """To-vejs voice bridge.

    Args:
        bus:           Message bus til publish/consume.
        voice_client:  VoiceServiceClient — speak/transcribe HTTP-klient.
        screen_reader: ScreenReader til mikrofon-capture + STT (optional).
        project_id:    Projekt-ID sendt til voice service.
        agent_id:      Identifikation i publishede beskeder.
    """

    def __init__(
        self,
        bus: _BusLike,
        voice_client: _VoiceClientLike,
        screen_reader: _ScreenReaderLike | None = None,
        *,
        project_id: str = "default",
        agent_id: str = "voice-io-agent",
    ) -> None:
        self._bus = bus
        self._voice_client = voice_client
        self._screen_reader = screen_reader
        self._project_id = str(project_id or "default").strip() or "default"
        self._agent_id = str(agent_id or "voice-io-agent").strip() or "voice-io-agent"
        self._running = False

    # ------------------------------------------------------------------
    # STT INPUT FLOW
    # ------------------------------------------------------------------

    async def start_listening(self) -> None:
        """Kontinuerlig lytning: capture → transcribe → publish VOICE_INPUT.

        Returnerer straks uden fejl hvis:
          - voice service ikke er tilgængeligt (graceful degradation)
          - ingen screen_reader er konfigureret
        Loop-fejl logges og gestartes (crash aldrig agenten).
        """
        if not self._voice_client.is_available():
            logger.warning(
                "voice_io_agent: voice service unavailable — listening disabled"
            )
            return

        if self._screen_reader is None:
            logger.warning(
                "voice_io_agent: no screen_reader configured — listening disabled"
            )
            return

        logger.info("voice_io_agent: start_listening project_id=%s", self._project_id)
        self._running = True
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: self._screen_reader.capture_and_transcribe(_LISTEN_CHUNK_SECONDS),  # type: ignore[union-attr]
                )
                text = str(getattr(result, "transcript", "") or "").strip()
                if text:
                    self._publish_voice_input(text)
            except Exception as exc:
                logger.error(
                    "voice_io_agent: listen_error %s — continuing loop", exc
                )
            await asyncio.sleep(0)  # yield to event loop

    # ------------------------------------------------------------------
    # TTS OUTPUT FLOW
    # ------------------------------------------------------------------

    async def handle_voice_output(self, message: Message) -> None:
        """Modtager VOICE_OUTPUT → speak → afspil audio.

        Fejler graceful: logger 'voice unavailable' eller speak-fejl, ingen crash.
        """
        if not self._voice_client.is_available():
            logger.warning(
                "voice_io_agent: voice unavailable — skipping VOICE_OUTPUT"
            )
            return

        text = str((message.payload or {}).get("text") or "").strip()
        if not text:
            logger.debug("voice_io_agent: handle_voice_output received empty text")
            return

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._voice_client.speak(
                    text,
                    project_id=self._project_id,
                ),
            )
            file_path = str((result or {}).get("file_path") or "").strip()
            if file_path:
                await self._play_audio_file(file_path)
            else:
                logger.warning(
                    "voice_io_agent: speak returned no file_path — audio not played"
                )
        except Exception as exc:
            logger.error("voice_io_agent: speak_error %s", exc)

    # ------------------------------------------------------------------
    # RUN (both flows in parallel)
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start begge flows parallelt. Returnerer aldrig ved normal drift."""
        self._running = True
        await asyncio.gather(
            self.start_listening(),
            self._consume_outputs(),
            return_exceptions=True,
        )

    def stop(self) -> None:
        """Signal til at stoppe begge loops."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _consume_outputs(self) -> None:
        """Poll bus for VOICE_OUTPUT beskeder og forward til handle_voice_output."""
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                messages = await loop.run_in_executor(
                    None,
                    lambda: list(self._bus.consume(max_messages=10)),
                )
                for msg in messages:
                    if msg.message_type == MessageType.VOICE_OUTPUT:
                        await self.handle_voice_output(msg)
            except Exception as exc:
                logger.error("voice_io_agent: consume_error %s", exc)
            await asyncio.sleep(_CONSUME_POLL_SECONDS)

    def _publish_voice_input(self, text: str) -> None:
        now = datetime.now(tz=timezone.utc).isoformat()
        msg = Message(
            message_id=str(uuid.uuid4()),
            from_agent=self._agent_id,
            to_agent="pipeline",
            message_type=MessageType.VOICE_INPUT,
            payload={
                "event_type": MessageType.VOICE_INPUT.value,
                "payload": {
                    "text": text,
                    "source": "microphone",
                    "timestamp": now,
                },
                "timestamp": now,
            },
            context_id=str(uuid.uuid4()),
            timestamp=now,
        )
        self._bus.publish(msg)
        logger.info("voice_io_agent: published VOICE_INPUT text_len=%d", len(text))

    async def _play_audio_file(self, file_path: str) -> None:
        """Afspil audio fra filsti via sounddevice/soundfile.

        Fallback: logger filstien hvis afspilning ikke er mulig.
        """
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, lambda: _play_wav_blocking(file_path))
        except Exception as exc:
            logger.warning(
                "voice_io_agent: audio_playback_failed file=%s error=%s — file is at %s",
                file_path,
                exc,
                file_path,
            )


# ---------------------------------------------------------------------------
# Audio playback helper (sync, runs in executor)
# ---------------------------------------------------------------------------


def _play_wav_blocking(file_path: str) -> None:
    """Afspil WAV-fil via sounddevice+soundfile. Rejser exception ved fejl."""
    try:
        import numpy as np
        import sounddevice as sd
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError(
            f"audio playback requires sounddevice+soundfile: {exc}"
        ) from exc

    data, samplerate = sf.read(str(file_path))
    sd.play(np.asarray(data), samplerate)
    sd.wait()
