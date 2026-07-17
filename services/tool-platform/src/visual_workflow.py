from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import imageio.v2 as imageio
from fpdf import FPDF
from PIL import Image

from babyai.memory.visual_memory import VisualMemory, VisualEntry
from babyai.tools.consistency_agent import ConsistencyAgent
from babyai.tools.visual_tool import VisualResult, VisualTool


@dataclass(frozen=True)
class SequenceSummary:
    sequence_id: str
    name: str
    project_id: str
    style_profile: str
    frame_count: int
    frames: list[str]
    consistency_profile: dict[str, Any]


class VisualWorkflow:
    def __init__(
        self,
        visual_tool: VisualTool,
        visual_memory: VisualMemory,
        consistency_agent: ConsistencyAgent,
    ) -> None:
        self.visual_tool = visual_tool
        self.visual_memory = visual_memory
        self.consistency_agent = consistency_agent
        self._sequences: dict[str, dict[str, str]] = {}

    def create_sequence(self, name: str, style_profile: str, project_id: str) -> str:
        clean_name = str(name or "").strip()
        clean_style = str(style_profile or "").strip().lower()
        clean_project_id = str(project_id or "").strip()
        if not clean_name:
            raise ValueError("name must be non-empty")
        if not clean_style:
            raise ValueError("style_profile must be non-empty")
        if not clean_project_id:
            raise ValueError("project_id must be non-empty")

        next_number = self._next_sequence_number()
        sequence_id = f"seq-{next_number:03d}"
        self._sequences[sequence_id] = {
            "name": clean_name,
            "style_profile": clean_style,
            "project_id": clean_project_id,
        }
        return sequence_id

    def add_frame(self, sequence_id: str, prompt: str) -> VisualResult:
        clean_sequence_id = str(sequence_id or "").strip()
        clean_prompt = str(prompt or "").strip()
        if not clean_sequence_id:
            raise ValueError("sequence_id must be non-empty")
        if not clean_prompt:
            raise ValueError("prompt must be non-empty")

        meta = self._sequence_meta(clean_sequence_id)
        previous_entries = self.visual_memory.get_sequence(clean_sequence_id)
        reference_image = previous_entries[-1].file_path if previous_entries else None

        consistency_profile = self.consistency_agent.analyze_sequence(clean_sequence_id)
        enriched_prompt = self.consistency_agent.enrich_prompt(clean_prompt, consistency_profile)

        return self.visual_tool.generate_image(
            prompt=enriched_prompt,
            style_profile=meta["style_profile"],
            sequence_id=clean_sequence_id,
            reference_image=reference_image,
        )

    def get_sequence_summary(self, sequence_id: str) -> SequenceSummary:
        clean_sequence_id = str(sequence_id or "").strip()
        if not clean_sequence_id:
            raise ValueError("sequence_id must be non-empty")

        meta = self._sequence_meta(clean_sequence_id)
        entries = self.visual_memory.get_sequence(clean_sequence_id)
        consistency_profile = self.consistency_agent.analyze_sequence(clean_sequence_id).to_dict()
        return SequenceSummary(
            sequence_id=clean_sequence_id,
            name=meta["name"],
            project_id=meta["project_id"],
            style_profile=meta["style_profile"],
            frame_count=len(entries),
            frames=[entry.file_path for entry in entries],
            consistency_profile=consistency_profile,
        )

    def export_sequence(self, sequence_id: str, format: str) -> str:
        clean_sequence_id = str(sequence_id or "").strip()
        clean_format = str(format or "").strip().lower()
        if not clean_sequence_id:
            raise ValueError("sequence_id must be non-empty")
        if clean_format not in {"gif", "mp4", "pdf"}:
            raise ValueError("format must be one of: gif, mp4, pdf")

        entries = self.visual_memory.get_sequence(clean_sequence_id)
        if not entries:
            raise ValueError(f"no frames found for sequence: {clean_sequence_id}")

        output_dir = Path(entries[-1].file_path).resolve().parent
        output_path = output_dir / f"{clean_sequence_id}.{clean_format}"

        if clean_format == "gif":
            self._export_gif(entries, output_path)
        elif clean_format == "mp4":
            self._export_mp4(entries, output_path)
        else:
            self._export_pdf(entries, output_path)

        return output_path.resolve().as_posix()

    def _export_gif(self, entries: list[VisualEntry], output_path: Path) -> None:
        frames = [imageio.imread(Path(entry.file_path).resolve().as_posix()) for entry in entries]
        imageio.mimsave(output_path.as_posix(), frames, format="GIF", duration=0.8)

    def _export_mp4(self, entries: list[VisualEntry], output_path: Path) -> None:
        with imageio.get_writer(output_path.as_posix(), format="FFMPEG", fps=1) as writer:
            for entry in entries:
                frame = imageio.imread(Path(entry.file_path).resolve().as_posix())
                writer.append_data(frame)

    def _export_pdf(self, entries: list[VisualEntry], output_path: Path) -> None:
        pdf = FPDF(unit="mm", format="A4")
        pdf.set_auto_page_break(auto=True, margin=8)

        columns = 2
        page_width = 210.0
        margin = 10.0
        gap = 4.0
        cell_width = (page_width - (2 * margin) - ((columns - 1) * gap)) / columns
        cell_height = 90.0
        row_index = 0

        pdf.add_page()
        for index, entry in enumerate(entries, start=1):
            col = (index - 1) % columns
            if col == 0 and index > 1:
                row_index += 1
            y = margin + row_index * (cell_height + 12.0)
            if y + cell_height > 287.0:
                pdf.add_page()
                row_index = 0
                y = margin
            x = margin + col * (cell_width + gap)
            pdf.set_xy(x, y)
            pdf.set_font("Helvetica", size=10)
            pdf.cell(cell_width, 5.0, text=f"Frame {index}", border=0)

            image_path = Path(entry.file_path).resolve()
            with Image.open(image_path.as_posix()) as image:
                width, height = image.size
            ratio = float(height) / float(width) if width else 1.0
            target_height = min(cell_height, cell_width * ratio)
            pdf.image(image_path.as_posix(), x=x, y=y + 6.0, w=cell_width, h=target_height)

        pdf.output(output_path.as_posix())

    def _next_sequence_number(self) -> int:
        max_index = 0
        for key in self._sequences.keys():
            if key.startswith("seq-"):
                raw = key.split("seq-", 1)[1]
                try:
                    max_index = max(max_index, int(raw))
                except Exception:
                    continue

        root = self.visual_memory.visuals_root
        if root.exists():
            for child in root.iterdir():
                if not child.is_dir():
                    continue
                name = child.name
                if not name.startswith("seq-"):
                    continue
                raw = name.split("seq-", 1)[1]
                try:
                    max_index = max(max_index, int(raw))
                except Exception:
                    continue
        return max_index + 1

    def _sequence_meta(self, sequence_id: str) -> dict[str, str]:
        direct = self._sequences.get(sequence_id)
        if isinstance(direct, dict):
            return {
                "name": str(direct.get("name") or sequence_id),
                "style_profile": str(direct.get("style_profile") or "safe"),
                "project_id": str(direct.get("project_id") or self.visual_memory.project_id),
            }

        entries = self.visual_memory.get_sequence(sequence_id)
        if entries:
            return {
                "name": sequence_id,
                "style_profile": str(entries[-1].style_profile or "safe"),
                "project_id": self.visual_memory.project_id,
            }

        raise ValueError(f"sequence not found: {sequence_id}")
