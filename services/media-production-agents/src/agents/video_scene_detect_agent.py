"""
VideoSceneDetectAgent — detekterer scene-skift og genererer kapitel-navne.

Lytter på:  VIDEO_IMPORT_COMPLETE  (eller VIDEO_SCENE_REQUEST direkte)
Producerer: VIDEO_SCENE_COMPLETE   (med scene-liste inkl. LLM-genererede titler)

Bruger ffmpeg scene-filter. LLM-kald til kapitel-navne sker via
CLAUDE_MODEL / ANTHROPIC_BASE_URL env-vars (samme mønster som resten).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.base import Agent
from babyai_shared.bus.protocol import Context, Message, MessageType

_log = logging.getLogger(__name__)

_SCENE_THRESHOLD = float(os.getenv("VIDEO_SCENE_THRESHOLD", "0.3"))
_CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")


class VideoSceneDetectAgent(Agent):
    def __init__(self, agent_id: str = "video-scene-detect-001") -> None:
        super().__init__(
            agent_id=agent_id,
            role="video_scene_detect",
            accepts={MessageType.VIDEO_IMPORT_COMPLETE, MessageType.VIDEO_SCENE_REQUEST},
        )

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type in (
            MessageType.VIDEO_IMPORT_COMPLETE,
            MessageType.VIDEO_SCENE_REQUEST,
        ):
            return self._handle_scene_detect(message, context)
        return []

    def _handle_scene_detect(self, message: Message, context: Context) -> List[Message]:
        payload = message.payload or {}
        video_path = str(payload.get("output") or payload.get("video_path") or "")

        if payload.get("status") == "error" or not video_path:
            _log.warning("VideoSceneDetectAgent: ingen gyldig video_path i payload")
            content: Dict[str, Any] = {
                "status": "error",
                "error": "ingen gyldig video_path",
                "scenes": [],
            }
        else:
            try:
                scenes = _detect_scenes(video_path, _SCENE_THRESHOLD)
                scenes = _name_scenes_with_llm(scenes, video_path)
                content = {"status": "ok", "video_path": video_path, "scenes": scenes}
            except Exception as exc:
                _log.warning("VideoSceneDetectAgent: scene-detect fejlede: %s", exc)
                content = {"status": "error", "error": f"{type(exc).__name__}: {exc}", "scenes": []}

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent=message.from_agent,
            message_type=MessageType.VIDEO_SCENE_COMPLETE,
            payload=content,
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )]


def _detect_scenes(video_path: str, threshold: float) -> List[Dict[str, Any]]:
    """Kør ffmpeg scene-filter og returner liste af scene-tidspunkter."""
    cmd = [
        "ffmpeg", "-i", video_path,
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    output = result.stderr  # ffmpeg showinfo skriver til stderr

    pts_times: List[float] = []
    for match in re.finditer(r"pts_time:([\d.]+)", output):
        pts_times.append(float(match.group(1)))

    # Byg scene-liste: [0.0, cut1, cut2, ...] som start-tidspunkter
    duration = _probe_duration(video_path)
    boundaries = [0.0] + sorted(pts_times)

    scenes = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else duration
        scenes.append({
            "index": i,
            "start_sec": round(start, 2),
            "end_sec": round(end, 2),
            "title": f"Scene {i + 1}",
        })
    return scenes


def _probe_duration(video_path: str) -> float:
    """Hent varighed med ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip() or "0")
    except Exception:
        return 0.0


def _name_scenes_with_llm(scenes: List[Dict[str, Any]], video_path: str) -> List[Dict[str, Any]]:
    """Forsøg at navngive scener med LLM. Returner uændrede scener ved fejl."""
    if not scenes:
        return scenes
    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", "local"),
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        )
        scene_list = "\n".join(
            f"  Scene {s['index']}: {s['start_sec']}s – {s['end_sec']}s"
            for s in scenes
        )
        prompt = (
            f"Videofil: {os.path.basename(video_path)}\n"
            f"Scene-tidspunkter:\n{scene_list}\n\n"
            "Giv hvert kapitel et kort beskrivende navn på maks 4 ord. "
            "Svar med én linje per scene i formatet: 'Scene N: Navn'"
        )
        resp = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text if resp.content else ""
        return _apply_llm_names(scenes, text)
    except Exception as exc:
        _log.debug("VideoSceneDetectAgent: LLM-navngivning fejlede: %s — bruger standard-navne", exc)
        return scenes


def _apply_llm_names(scenes: List[Dict[str, Any]], llm_text: str) -> List[Dict[str, Any]]:
    """Parse LLM-svar og anvend navne på scene-listen."""
    lines = llm_text.strip().splitlines()
    name_map: Dict[int, str] = {}
    for line in lines:
        match = re.search(r"Scene\s+(\d+)\s*:\s*(.+)", line, re.IGNORECASE)
        if match:
            idx = int(match.group(1))
            name_map[idx] = match.group(2).strip()
    for scene in scenes:
        title = name_map.get(scene["index"])
        if title:
            scene["title"] = title
    return scenes
