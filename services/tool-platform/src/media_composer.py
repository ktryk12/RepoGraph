from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any

import imageio.v2 as imageio
from fpdf import FPDF

from babyai.memory.visual_memory import VisualMemory
from babyai.memory.voice_memory import VoiceMemory
from babyai.tools.voice_sequence import VoiceSequence


@dataclass(frozen=True)
class SceneResult:
    visual_id: str
    audio_id: str
    scene_number: int


class MediaComposer:
    def __init__(
        self,
        visual_workflow: Any,
        voice_sequence: VoiceSequence,
        voice_memory: VoiceMemory,
        visual_memory: VisualMemory,
    ) -> None:
        self.visual_workflow = visual_workflow
        self.voice_sequence = voice_sequence
        self.voice_memory = voice_memory
        self.visual_memory = visual_memory
        self._media_sequences: dict[str, dict[str, Any]] = {}

    def create_narrated_sequence(
        self,
        name: str,
        style_profile: str,
        voice_id: str,
        project_id: str,
    ) -> str:
        clean_name = str(name or "").strip()
        clean_style = str(style_profile or "").strip()
        clean_voice_id = str(voice_id or "").strip()
        clean_project_id = str(project_id or "").strip()
        if not clean_name:
            raise ValueError("name must be non-empty")
        if not clean_style:
            raise ValueError("style_profile must be non-empty")
        if not clean_voice_id:
            raise ValueError("voice_id must be non-empty")
        if not clean_project_id:
            raise ValueError("project_id must be non-empty")

        profile = self.voice_memory.get_voice(clean_voice_id)
        visual_seq = self.visual_workflow.create_sequence(clean_name, clean_style, clean_project_id)
        voice_seq = self.voice_sequence.create_sequence(clean_name, clean_voice_id, profile.language, clean_project_id)

        media_seq = f"media-{self._next_sequence_number():03d}"
        self._media_sequences[media_seq] = {
            "id": media_seq,
            "name": clean_name,
            "project_id": clean_project_id,
            "style_profile": clean_style,
            "voice_id": clean_voice_id,
            "language": profile.language,
            "visual_sequence_id": visual_seq,
            "voice_sequence_id": voice_seq,
            "scene_count": 0,
        }
        return media_seq

    def add_scene(self, seq_id: str, visual_prompt: str, narration_text: str) -> SceneResult:
        meta = self._get_meta(seq_id)

        visual_result = self.visual_workflow.add_frame(meta["visual_sequence_id"], str(visual_prompt or ""))
        audio_result = self.voice_sequence.add_segment(meta["voice_sequence_id"], str(narration_text or ""))

        meta["scene_count"] = int(meta.get("scene_count", 0)) + 1
        return SceneResult(
            visual_id=str(visual_result.visual_id),
            audio_id=str(audio_result.audio_id),
            scene_number=int(meta["scene_count"]),
        )

    def export(self, seq_id: str, format: str) -> str:
        meta = self._get_meta(seq_id)
        clean_format = str(format or "").strip().lower()
        if clean_format not in {"pdf", "video"}:
            raise ValueError("format must be one of: pdf, video")

        visual_entries = self.visual_memory.get_sequence(meta["visual_sequence_id"])
        voice_entries = self.voice_memory.get_sequence(meta["voice_sequence_id"])
        if not visual_entries:
            raise ValueError(f"media sequence has no visual scenes: {seq_id}")

        output_dir = Path(visual_entries[-1].file_path).resolve().parent
        if clean_format == "pdf":
            target = output_dir / f"{seq_id}.pdf"
            self._export_pdf(target, visual_entries, voice_entries)
            return target.resolve().as_posix()

        target = output_dir / f"{seq_id}.mp4"
        if voice_entries:
            self._export_video_with_audio(target, visual_entries, str(meta["voice_sequence_id"]))
        else:
            self._export_video(target, visual_entries)
        return target.resolve().as_posix()

    def _export_pdf(self, target: Path, visual_entries: list[Any], voice_entries: list[Any]) -> None:
        pdf = FPDF(unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=10)

        scene_count = min(len(visual_entries), len(voice_entries)) if voice_entries else len(visual_entries)
        for index in range(scene_count):
            visual = visual_entries[index]
            voice_text = str(voice_entries[index].text).strip() if index < len(voice_entries) else ""

            pdf.add_page()
            pdf.set_font("Helvetica", size=12)
            pdf.cell(0, 8, text=f"Scene {index + 1}", new_x="LMARGIN", new_y="NEXT")
            pdf.image(Path(visual.file_path).resolve().as_posix(), x=10, y=20, w=190)
            pdf.set_xy(10, 260)
            pdf.set_font("Helvetica", size=11)
            pdf.multi_cell(190, 6, text=voice_text)

        pdf.output(target.as_posix())

    def _export_video(self, target: Path, visual_entries: list[Any]) -> None:
        with imageio.get_writer(target.as_posix(), format="FFMPEG", fps=1) as writer:
            for entry in visual_entries:
                frame = imageio.imread(Path(entry.file_path).resolve().as_posix())
                writer.append_data(frame)

    def _export_video_with_audio(self, target: Path, visual_entries: list[Any], voice_sequence_id: str) -> None:
        silent_target = target.with_suffix(".silent.mp4")
        self._export_video(silent_target, visual_entries)

        voice_wav = Path(self.voice_sequence.export_sequence(voice_sequence_id, "wav")).resolve()
        if not voice_wav.exists():
            raise FileNotFoundError(f"voice sequence export missing: {voice_wav.as_posix()}")

        try:
            import imageio_ffmpeg
        except Exception as exc:
            raise RuntimeError("imageio-ffmpeg is required for media video export with audio") from exc

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            silent_target.as_posix(),
            "-i",
            voice_wav.as_posix(),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            target.as_posix(),
        ]
        proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg_mux_failed: {proc.stderr.strip()}")
        silent_target.unlink(missing_ok=True)

    def _get_meta(self, seq_id: str) -> dict[str, Any]:
        clean = str(seq_id or "").strip()
        if not clean:
            raise ValueError("seq_id must be non-empty")
        meta = self._media_sequences.get(clean)
        if meta is None:
            raise ValueError(f"media sequence not found: {clean}")
        return meta

    def _next_sequence_number(self) -> int:
        max_index = 0
        for key in self._media_sequences.keys():
            if key.startswith("media-"):
                raw = key.split("media-", 1)[1]
                try:
                    max_index = max(max_index, int(raw))
                except Exception:
                    continue
        return max_index + 1
