"""
TrainingDataCollector — samler agent-beslutninger til LoRA træningsdata.

Skriver JSONL til babyai/lora/datasets/{archetype}_raw.jsonl.
Opdaterer outcome i {archetype}_with_outcomes.jsonl når resultatet kendes.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class TrainingDataCollector:
    """
    Indsamler context/reasoning/decision/outcome eksempler til LoRA træning.

    Thread-safe skrivning til JSONL filer.
    Ingen database-afhængighed — ren fil-baseret.
    """

    def __init__(
        self,
        archetype: str,
        output_dir: str = "babyai/lora/datasets/",
    ) -> None:
        self.archetype = str(archetype)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self._raw_path = self.output_dir / f"{archetype}_raw.jsonl"
        self._outcomes_path = self.output_dir / f"{archetype}_with_outcomes.jsonl"

        # In-memory index: trade_id → line number i raw file (til outcome update)
        self._id_index: dict[str, dict] = {}
        self._load_index()

    # ── Public API ──────────────────────────────────────────────────────────────

    def record(
        self,
        context: dict[str, Any],
        reasoning: str,
        decision: dict[str, Any],
        outcome: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Skriv ét træningseksempel.

        Returns: trade_id (UUID) til brug i update_outcome().
        """
        trade_id = str(uuid.uuid4())
        example = {
            "id": trade_id,
            "archetype": self.archetype,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "context": context,
            "reasoning": str(reasoning),
            "decision": decision,
            "outcome": outcome,
        }
        with self._raw_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(example, ensure_ascii=False) + "\n")

        self._id_index[trade_id] = example
        logger.debug("TrainingDataCollector.record id=%s archetype=%s", trade_id, self.archetype)
        return trade_id

    def update_outcome(
        self,
        trade_id: str,
        pnl_pct: float,
        capital_after: float,
        success: bool,
    ) -> bool:
        """
        Tilføj outcome til et eksempel identificeret ved trade_id.

        Skriver komplet eksempel med outcome til {archetype}_with_outcomes.jsonl.
        Returnerer True hvis found og skrevet, False hvis ikke fundet.
        """
        example = self._id_index.get(trade_id)
        if example is None:
            logger.warning("TrainingDataCollector.update_outcome: unknown id=%s", trade_id)
            return False

        example["outcome"] = {
            "pnl_pct": round(float(pnl_pct), 6),
            "capital_after": round(float(capital_after), 6),
            "success": bool(success),
            "outcome_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with self._outcomes_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(example, ensure_ascii=False) + "\n")

        return True

    def get_stats(self) -> dict[str, Any]:
        """
        Returnerer statistik over indsamlede eksempler.
        """
        total = 0
        with_outcomes = 0
        pnl_list: list[float] = []
        wins = 0

        if self._raw_path.exists():
            with self._raw_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    total += 1

        if self._outcomes_path.exists():
            with self._outcomes_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ex = json.loads(line)
                        outcome = ex.get("outcome") or {}
                        with_outcomes += 1
                        pnl = float(outcome.get("pnl_pct", 0.0))
                        pnl_list.append(pnl)
                        if outcome.get("success"):
                            wins += 1
                    except (json.JSONDecodeError, TypeError):
                        continue

        win_rate = wins / with_outcomes if with_outcomes > 0 else 0.0
        avg_pnl = sum(pnl_list) / len(pnl_list) if pnl_list else 0.0

        return {
            "total_examples": total,
            "with_outcomes": with_outcomes,
            "win_rate": round(win_rate, 4),
            "avg_pnl": round(avg_pnl, 6),
        }

    # ── Internal ────────────────────────────────────────────────────────────────

    def _load_index(self) -> None:
        """Indlæs eksisterende eksempler i memory index."""
        if not self._raw_path.exists():
            return
        try:
            with self._raw_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ex = json.loads(line)
                        if "id" in ex:
                            self._id_index[ex["id"]] = ex
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass
