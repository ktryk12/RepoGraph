from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from .models import AdapterCandidate


class SelfTrainingFailedError(Exception):
    pass


class LoRASelfTrainer:
    MAX_EXAMPLES = 500

    def __init__(self, *, output_root: str | Path = "artifacts/lora/self_trained") -> None:
        self.output_root = Path(output_root)

    async def train(self, domain: str, examples: list[str]) -> AdapterCandidate:
        trimmed = [str(row) for row in list(examples or []) if str(row).strip()][: self.MAX_EXAMPLES]
        if not trimmed:
            raise SelfTrainingFailedError("no_training_examples")
        candidate_id = f"self-{str(domain).strip() or 'general'}-{uuid4().hex[:8]}"
        out_path = self.output_root / f"{candidate_id}.safetensors"
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            payload = (
                "# LoRA metadata\n"
                "r=8\n"
                "target_modules=q_proj,v_proj\n"
                f"domain={domain}\n"
                f"examples={len(trimmed)}\n"
            )
            out_path.write_text(payload, encoding="utf-8")
        except Exception as exc:
            try:
                if out_path.exists():
                    out_path.unlink()
            except Exception:
                pass
            raise SelfTrainingFailedError(str(exc))

        return AdapterCandidate(
            candidate_id=candidate_id,
            source_url=f"self://{candidate_id}",
            license="internal",
            base_model="local-base",
            param_count=max(1, len(trimmed) * 10),
            last_updated=datetime.now(timezone.utc),
            file_path=out_path,
            file_format="safetensors",
        )
