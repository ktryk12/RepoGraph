"""
VideoEditAgent — klipper og samler film fra scene-liste.

Lytter på:  VIDEO_SCENE_COMPLETE  (scene-liste fra VideoSceneDetectAgent)
            VIDEO_EDIT_REQUEST    (direkte kald med scene-liste)
            VOICE_OVERLAY_COMPLETE (WAV klar til at blende ind)
Producerer: VIDEO_EDIT_COMPLETE   (færdig film)
            VOICE_OVERLAY_REQUEST (hvis script sendes med)

Kræver ffmpeg i PATH.
"""
from __future__ import annotations

import logging
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.base import Agent
from babyai_shared.bus.protocol import Context, Message, MessageType

_log = logging.getLogger(__name__)

_WORKSPACE = Path(os.getenv("VIDEO_WORKSPACE", "workspace"))


class VideoEditAgent(Agent):
    def __init__(self, agent_id: str = "video-edit-001") -> None:
        super().__init__(
            agent_id=agent_id,
            role="video_edit",
            accepts={
                MessageType.VIDEO_SCENE_COMPLETE,
                MessageType.VIDEO_EDIT_REQUEST,
                MessageType.VOICE_OVERLAY_COMPLETE,
            },
        )
        # Ventende jobs: context_id → job-data
        self._pending: Dict[str, Dict[str, Any]] = {}

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type in (
            MessageType.VIDEO_SCENE_COMPLETE,
            MessageType.VIDEO_EDIT_REQUEST,
        ):
            return self._handle_edit_request(message, context)
        if message.message_type == MessageType.VOICE_OVERLAY_COMPLETE:
            return self._handle_voice_ready(message, context)
        return []

    def _handle_edit_request(self, message: Message, context: Context) -> List[Message]:
        payload = message.payload or {}

        if payload.get("status") == "error":
            return self._emit_error(message, context, str(payload.get("error", "upstream error")))

        video_path = str(payload.get("video_path") or "")
        scenes = list(payload.get("scenes") or [])
        voice_script = str(payload.get("voice_script") or "").strip()

        if not video_path or not scenes:
            return self._emit_error(message, context, "video_path eller scenes mangler")

        # Gem job i ventekø hvis vi afventer voice-overlay
        if voice_script:
            self._pending[context.context_id] = {
                "video_path": video_path,
                "scenes": scenes,
                "from_agent": message.from_agent,
            }
            return [Message(
                message_id=str(uuid.uuid4()),
                from_agent=self.agent_id,
                to_agent="voice-overlay-001",
                message_type=MessageType.VOICE_OVERLAY_REQUEST,
                payload={
                    "text": voice_script,
                    "output_path": str(_WORKSPACE / "audio" / f"{context.context_id}.wav"),
                    "format": "wav",
                },
                context_id=context.context_id,
                timestamp=datetime.now().isoformat(),
            )]

        return self._run_edit(video_path, scenes, None, message.from_agent, context)

    def _handle_voice_ready(self, message: Message, context: Context) -> List[Message]:
        payload = message.payload or {}
        job = self._pending.pop(context.context_id, None)
        if job is None:
            _log.warning("VideoEditAgent: VOICE_OVERLAY_COMPLETE for ukendt context %s", context.context_id)
            return []

        audio_path: Optional[str] = None
        if payload.get("status") == "ok":
            audio_path = str(payload.get("output_path") or "")

        return self._run_edit(
            job["video_path"], job["scenes"], audio_path, job["from_agent"], context
        )

    def _run_edit(
        self,
        video_path: str,
        scenes: List[Dict[str, Any]],
        audio_path: Optional[str],
        reply_to: str,
        context: Context,
    ) -> List[Message]:
        try:
            output = _edit_video(video_path, scenes, audio_path)
            content: Dict[str, Any] = {"status": "ok", "output": output, "duration_sec": _probe_duration(output)}
        except Exception as exc:
            _log.warning("VideoEditAgent: redigering fejlede: %s", exc)
            content = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent=reply_to,
            message_type=MessageType.VIDEO_EDIT_COMPLETE,
            payload=content,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]

    def _emit_error(self, message: Message, context: Context, error: str) -> List[Message]:
        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type=MessageType.VIDEO_EDIT_COMPLETE,
            payload={"status": "error", "error": error},
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]


def _edit_video(
    video_path: str,
    scenes: List[Dict[str, Any]],
    audio_path: Optional[str],
) -> str:
    """Klip scener ud, concat og blend evt. lyd. Returner output-sti."""
    clips_dir = _WORKSPACE / "clips"
    output_dir = _WORKSPACE / "output"
    clips_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    clip_paths: List[str] = []
    for scene in scenes:
        idx = scene.get("index", len(clip_paths))
        start = scene.get("start_sec", 0)
        end = scene.get("end_sec", start + 1)
        clip = str(clips_dir / f"scene_{idx:04d}.mp4")
        subprocess.run(
            ["ffmpeg", "-y", "-i", video_path,
             "-ss", str(start), "-to", str(end), "-c", "copy", clip],
            capture_output=True, check=True, timeout=120,
        )
        clip_paths.append(clip)

    if not clip_paths:
        raise ValueError("Ingen clips at samle")

    concat_file = clips_dir / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{p}'" for p in clip_paths), encoding="utf-8"
    )

    draft = str(output_dir / f"film_draft_{uuid.uuid4().hex[:8]}.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", draft],
        capture_output=True, check=True, timeout=300,
    )

    if not audio_path or not Path(audio_path).exists():
        return draft

    final = draft.replace("_draft_", "_final_")
    subprocess.run(
        ["ffmpeg", "-y", "-i", draft, "-i", audio_path,
         "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=2",
         "-c:v", "copy", final],
        capture_output=True, check=True, timeout=300,
    )
    return final


def _probe_duration(video_path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip() or "0")
    except Exception:
        return 0.0
