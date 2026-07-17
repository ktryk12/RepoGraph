"""
VoiceOverlayAgent — bro mellem video-pipeline og VoiceIOAgent.

Lytter på:  VOICE_OVERLAY_REQUEST  (fra VideoEditAgent)
Producerer: VOICE_OVERLAY_COMPLETE (WAV-sti klar til blending)
            VOICE_OUTPUT           (til VoiceIOAgent for TTS)

Falder graceful tilbage hvis VoiceServiceClient ikke er tilgængeligt:
returner VOICE_OVERLAY_COMPLETE med status="unavailable" så VideoEditAgent
kan eksportere film uden voice-over.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from agents.base import Agent
from babyai_shared.bus.protocol import Context, Message, MessageType

_log = logging.getLogger(__name__)

_VOICE_SERVICE_BASE_URL = os.getenv("VOICE_SERVICE_BASE_URL", "http://localhost:8111")
_WORKSPACE = Path(os.getenv("VIDEO_WORKSPACE", "workspace"))


class VoiceOverlayAgent(Agent):
    def __init__(self, agent_id: str = "voice-overlay-001") -> None:
        super().__init__(
            agent_id=agent_id,
            role="voice_overlay",
            accepts={MessageType.VOICE_OVERLAY_REQUEST},
        )

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type == MessageType.VOICE_OVERLAY_REQUEST:
            return self._handle_overlay_request(message, context)
        return []

    def _handle_overlay_request(self, message: Message, context: Context) -> List[Message]:
        payload = message.payload or {}
        text = str(payload.get("text") or "").strip()
        output_path = str(payload.get("output_path") or "").strip()

        if not text:
            _log.warning("VoiceOverlayAgent: tomt text-felt i VOICE_OVERLAY_REQUEST")
            return self._complete(message, context, status="error", error="tomt script", output_path=output_path)

        if not output_path:
            audio_dir = _WORKSPACE / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(audio_dir / f"overlay_{context.context_id[:8]}.wav")

        # Forsøg TTS via VoiceServiceClient
        try:
            from babyai.voice.voice_service_client import VoiceServiceClient
            client = VoiceServiceClient(base_url=_VOICE_SERVICE_BASE_URL)
            if not client.is_available():
                raise RuntimeError("voice service ikke tilgængeligt")
            result = client.speak(text, output_path=output_path)
            actual_path = str((result or {}).get("file_path") or output_path)
            _log.info("VoiceOverlayAgent: TTS OK output=%s", actual_path)
            return self._complete(message, context, status="ok", output_path=actual_path)
        except Exception as exc:
            _log.warning("VoiceOverlayAgent: TTS fejlede (%s) — video fortsætter uden voice-over", exc)
            return self._complete(message, context, status="unavailable", error=str(exc), output_path="")

    def _complete(
        self,
        message: Message,
        context: Context,
        *,
        status: str,
        output_path: str,
        error: str = "",
    ) -> List[Message]:
        content: Dict[str, Any] = {"status": status, "output_path": output_path}
        if error:
            content["error"] = error
        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type=MessageType.VOICE_OVERLAY_COMPLETE,
            payload=content,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]
