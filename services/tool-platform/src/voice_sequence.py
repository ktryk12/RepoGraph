from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from babyai.memory.voice_memory import VoiceMemory
from babyai.tools.voice_tool import AudioResult, VoiceTool


@dataclass(frozen=True)
class VoiceSequenceMeta:
    id: str
    name: str
    voice_id: str
    language: str
    project_id: str


class VoiceSequence:
    def __init__(self, voice_tool: VoiceTool, voice_memory: VoiceMemory) -> None:
        self.voice_tool = voice_tool
        self.voice_memory = voice_memory
        self._sequences: dict[str, VoiceSequenceMeta] = {}

    def create_sequence(self, name: str, voice_id: str, language: str, project_id: str) -> str:
        clean_name = str(name or "").strip()
        clean_voice = str(voice_id or "").strip()
        clean_lang = str(language or "").strip()
        clean_project = str(project_id or "").strip()
        if not clean_name:
            raise ValueError("name must be non-empty")
        if not clean_voice:
            raise ValueError("voice_id must be non-empty")
        if not clean_lang:
            raise ValueError("language must be non-empty")
        if not clean_project:
            raise ValueError("project_id must be non-empty")

        _ = self.voice_memory.get_voice(clean_voice)
        seq_id = f"vseq-{self._next_sequence_number():03d}"
        self._sequences[seq_id] = VoiceSequenceMeta(
            id=seq_id,
            name=clean_name,
            voice_id=clean_voice,
            language=clean_lang,
            project_id=clean_project,
        )
        return seq_id

    def add_segment(self, seq_id: str, text: str) -> AudioResult:
        clean_seq = str(seq_id or "").strip()
        clean_text = str(text or "").strip()
        if not clean_seq:
            raise ValueError("seq_id must be non-empty")
        if not clean_text:
            raise ValueError("text must be non-empty")

        meta = self._get_meta(clean_seq)
        base = self.voice_tool.clone_voice(
            text=clean_text,
            voice_id=meta.voice_id,
            language=meta.language,
        )
        audio_id = self.voice_memory.save(
            {
                "file_path": base.file_path,
                "text": base.text,
                "voice_id": base.voice_id,
                "language": base.language,
                "duration_seconds": base.duration_seconds,
                "domain": "voice",
                "sequence_id": clean_seq,
            },
            sequence_id=clean_seq,
        )
        return AudioResult(
            audio_id=audio_id,
            file_path=base.file_path,
            text=base.text,
            voice_id=base.voice_id,
            language=base.language,
            duration_seconds=base.duration_seconds,
            metadata=dict(base.metadata),
        )

    def export_sequence(self, seq_id: str, format: str) -> str:
        clean_seq = str(seq_id or "").strip()
        clean_format = str(format or "").strip().lower()
        if not clean_seq:
            raise ValueError("seq_id must be non-empty")
        if clean_format not in {"mp3", "wav", "srt"}:
            raise ValueError("format must be one of: mp3, wav, srt")

        entries = self.voice_memory.get_sequence(clean_seq)
        if not entries:
            raise ValueError(f"sequence has no segments: {clean_seq}")

        output_dir = Path(entries[-1].file_path).resolve().parent
        output_path = output_dir / f"{clean_seq}.{clean_format}"

        if clean_format == "srt":
            output_path.write_text(self._build_srt(entries), encoding="utf-8")
            return output_path.resolve().as_posix()

        if clean_format == "wav":
            self._concat_wav(entries, output_path)
            return output_path.resolve().as_posix()

        # mp3 fallback: generate wav and copy bytes into mp3 path when encoder is unavailable.
        wav_path = output_dir / f"{clean_seq}.wav"
        self._concat_wav(entries, wav_path)
        output_path.write_bytes(wav_path.read_bytes())
        return output_path.resolve().as_posix()

    def get_full_transcript(self, seq_id: str) -> str:
        clean_seq = str(seq_id or "").strip()
        if not clean_seq:
            raise ValueError("seq_id must be non-empty")
        entries = self.voice_memory.get_sequence(clean_seq)
        return "\n".join(str(entry.text).strip() for entry in entries if str(entry.text).strip())

    def _concat_wav(self, entries: list[Any], output_path: Path) -> None:
        try:
            import numpy as np
            import soundfile as sf
        except Exception as exc:
            raise RuntimeError("audio export dependencies are missing") from exc

        chunks: list[Any] = []
        target_sr: int | None = None
        for entry in entries:
            data, sr = sf.read(Path(entry.file_path).resolve().as_posix(), always_2d=True)
            if target_sr is None:
                target_sr = int(sr)
            if int(sr) != int(target_sr):
                raise RuntimeError("cannot concatenate sequence with mixed sample rates")
            chunks.append(data)

        if not chunks or target_sr is None:
            raise RuntimeError("no audio chunks to export")

        merged = np.concatenate(chunks, axis=0)
        sf.write(output_path.as_posix(), merged, target_sr)

    def _build_srt(self, entries: list[Any]) -> str:
        lines: list[str] = []
        cursor = 0.0
        for index, entry in enumerate(entries, start=1):
            duration = max(0.2, float(entry.duration_seconds or 0.0))
            start = cursor
            end = cursor + duration
            cursor = end
            lines.extend(
                [
                    str(index),
                    f"{_format_srt_ts(start)} --> {_format_srt_ts(end)}",
                    str(entry.text or "").strip(),
                    "",
                ]
            )
        return "\n".join(lines)

    def _get_meta(self, seq_id: str) -> VoiceSequenceMeta:
        meta = self._sequences.get(seq_id)
        if meta is not None:
            return meta

        entries = self.voice_memory.get_sequence(seq_id)
        if entries:
            return VoiceSequenceMeta(
                id=seq_id,
                name=seq_id,
                voice_id=str(entries[-1].voice_id),
                language=str(entries[-1].language),
                project_id=self.voice_memory.project_id,
            )
        raise ValueError(f"voice sequence not found: {seq_id}")

    def _next_sequence_number(self) -> int:
        max_index = 0
        for key in self._sequences.keys():
            if key.startswith("vseq-"):
                raw = key.split("vseq-", 1)[1]
                try:
                    max_index = max(max_index, int(raw))
                except Exception:
                    continue

        root = self.voice_memory.audio_root
        if root.exists():
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                name = child.name
                if not name.startswith("vseq-"):
                    continue
                raw = name.split("vseq-", 1)[1]
                try:
                    max_index = max(max_index, int(raw))
                except Exception:
                    continue
        return max_index + 1


def _format_srt_ts(value: float) -> str:
    clean = max(0.0, float(value))
    total_ms = int(round(clean * 1000.0))
    hours = total_ms // 3_600_000
    total_ms -= hours * 3_600_000
    minutes = total_ms // 60_000
    total_ms -= minutes * 60_000
    seconds = total_ms // 1000
    millis = total_ms - seconds * 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
