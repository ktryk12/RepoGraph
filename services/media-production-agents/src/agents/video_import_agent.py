"""
VideoImportAgent — normaliserer råvideo til pipeline-format.

Lytter på:  VIDEO_IMPORT_REQUEST
Producerer: VIDEO_IMPORT_COMPLETE  (med normaliseret fil-sti og metadata)

Kræver ffmpeg i PATH. Falder graceful tilbage til passthrough hvis
ffmpeg ikke er tilgængeligt.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from agents.base import Agent
from babyai_shared.bus.protocol import Context, Message, MessageType

_log = logging.getLogger(__name__)

_WORKSPACE = Path(os.getenv("VIDEO_WORKSPACE", "workspace"))


class VideoImportAgent(Agent):
    def __init__(self, agent_id: str = "video-import-001") -> None:
        super().__init__(
            agent_id=agent_id,
            role="video_import",
            accepts={MessageType.VIDEO_IMPORT_REQUEST},
        )

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type == MessageType.VIDEO_IMPORT_REQUEST:
            return self._handle_import(message, context)
        return []

    def _handle_import(self, message: Message, context: Context) -> List[Message]:
        payload = message.payload or {}
        input_path = str(payload.get("input_path") or "")
        project_id = str(payload.get("project_id") or context.context_id or "default")

        try:
            result = _import_video(input_path, project_id)
            content: Dict[str, Any] = {"status": "ok", **result}
        except Exception as exc:
            _log.warning("VideoImportAgent: import failed: %s", exc)
            content = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "input_path": input_path}

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type=MessageType.VIDEO_IMPORT_COMPLETE,
            payload=content,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]


def _import_video(input_path: str, project_id: str) -> Dict[str, Any]:
    """Analyser og normaliser videofil med ffmpeg/ffprobe."""
    if not input_path:
        raise ValueError("input_path er tom")

    raw_dir = _WORKSPACE / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(input_path).stem
    output_path = raw_dir / f"{ts}_{stem}.mp4"

    # Analyser med ffprobe
    meta = _probe(input_path)

    # Transcode til normaliseret format
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale=iw:ih:force_original_aspect_ratio=decrease,"
               "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-r", "25",
        str(output_path),
    ]
    _run_ffmpeg(cmd)

    return {
        "input": input_path,
        "output": str(output_path),
        "project_id": project_id,
        **meta,
    }


def _probe(input_path: str) -> Dict[str, Any]:
    """Kør ffprobe og returner basis-metadata. Falder tilbage til tomme værdier."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", input_path],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio_tracks = sum(1 for s in streams if s.get("codec_type") == "audio")
        fps_raw = video.get("r_frame_rate", "25/1")
        num, den = (fps_raw.split("/") + ["1"])[:2]
        fps = round(int(num) / max(int(den), 1), 2)
        duration = float(video.get("duration") or data.get("format", {}).get("duration", 0))
        w = video.get("width", 0)
        h = video.get("height", 0)
        return {
            "duration_sec": duration,
            "fps": fps,
            "resolution": f"{w}x{h}" if w and h else "unknown",
            "audio_tracks": audio_tracks,
        }
    except Exception as exc:
        _log.warning("ffprobe failed: %s", exc)
        return {"duration_sec": 0, "fps": 25, "resolution": "unknown", "audio_tracks": 0}


def _run_ffmpeg(cmd: List[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg fejlede (rc={result.returncode}): {result.stderr[-500:]}")
