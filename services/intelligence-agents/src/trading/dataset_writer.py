"""
babyai/trading/dataset_writer.py — LoRA training dataset writer.

Writes one JSONL record per trading decision to data/trading_dataset.jsonl.
Thread-safe via threading.Lock.

Usage:
    writer = DatasetWriter()
    writer.record(rates, opportunities, reasoning="...", action="EXECUTE", trade=trade)
    print(writer.count())
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

_log = logging.getLogger(__name__)

Action = Literal["EXECUTE", "REJECT", "WAIT"]


class DatasetWriter:
    """Append-only JSONL writer for trading LoRA dataset."""

    def __init__(self, output_path: str = "data/trading_dataset.jsonl") -> None:
        self._path  = output_path
        self._lock  = threading.Lock()
        self._count = 0

        # Ensure output directory exists
        os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)

        # Count existing records so count() is accurate across restarts
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                self._count = sum(1 for _ in fh)
        except FileNotFoundError:
            pass

    def record(
        self,
        rates: Dict[str, float],
        opportunities: List[Any],
        reasoning: str,
        action: Action,
        trade: Optional[Any] = None,
    ) -> None:
        """
        Append one JSONL record to the dataset file.

        Parameters
        ----------
        rates:          Current EUR-based FX rates snapshot.
        opportunities:  List of ArbitrageOpportunity (or dicts).
        reasoning:      Agent reasoning string.
        action:         "EXECUTE", "REJECT", or "WAIT".
        trade:          PaperTrade instance, or None for WAIT.
        """
        opp_list = [
            {"path": list(o.path), "net_return": round(o.net_return, 6)}
            if hasattr(o, "path")
            else o
            for o in opportunities
        ]

        outcome: Dict[str, Any] = {"pnl_eur": 0.0, "status": "none"}
        trade_id: Optional[str] = None
        if trade is not None:
            outcome = {
                "pnl_eur": round(trade.pnl_eur, 6),
                "status":  trade.status.value if hasattr(trade.status, "value") else str(trade.status),
            }
            trade_id = trade.trade_id

        record = {
            "timestamp": datetime.utcnow().isoformat(),
            "input": {
                "rates":         rates,
                "opportunities": opp_list,
            },
            "reasoning": reasoning,
            "action":    action,
            "trade_id":  trade_id,
            "outcome":   outcome,
        }

        line = json.dumps(record, ensure_ascii=True)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._count += 1
            _log.debug("dataset_writer_record action=%s count=%d", action, self._count)

    def count(self) -> int:
        """Return number of records written since this instance was created (+ pre-existing)."""
        return self._count
