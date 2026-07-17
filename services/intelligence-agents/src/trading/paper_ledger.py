"""
babyai/trading/paper_ledger.py — In-memory paper trading ledger.

Records paper trades from ArbitrageOpportunities.
No live orders are ever sent. This is for LoRA dataset generation only.

Usage:
    ledger = PaperLedger(max_notional_eur=10_000.0)
    trade  = ledger.submit(opportunity, notional_eur=1000.0)
    print(ledger.total_pnl())
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from babyai.trading.arbitrage import ArbitrageOpportunity


class TradeStatus(Enum):
    PENDING  = "pending"
    EXECUTED = "executed"
    REJECTED = "rejected"


@dataclass
class PaperTrade:
    trade_id:     str
    path:         List[str]
    notional_eur: float
    gross_return: float
    net_return:   float
    status:       TradeStatus
    created_at:   datetime
    executed_at:  Optional[datetime] = None
    pnl_eur:      float = 0.0


class PaperLedger:
    """
    In-memory paper trade ledger.

    Parameters
    ----------
    max_notional_eur:
        Maximum single-trade notional in EUR. Default 10 000.
    """

    def __init__(self, max_notional_eur: float = 10_000.0) -> None:
        self._max_notional = max_notional_eur
        self._trades: List[PaperTrade] = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def submit(
        self, opportunity: ArbitrageOpportunity, notional_eur: float
    ) -> PaperTrade:
        """
        Submit a paper trade.

        Rejects if notional_eur <= 0 or notional_eur > max_notional_eur.
        Otherwise executes immediately (no latency simulation).
        pnl_eur = notional_eur * (net_return - 1.0)
        """
        now = datetime.utcnow()
        trade_id = str(uuid.uuid4())

        if notional_eur <= 0 or notional_eur > self._max_notional:
            trade = PaperTrade(
                trade_id=trade_id,
                path=list(opportunity.path),
                notional_eur=notional_eur,
                gross_return=opportunity.gross_return,
                net_return=opportunity.net_return,
                status=TradeStatus.REJECTED,
                created_at=now,
                pnl_eur=0.0,
            )
            self._trades.append(trade)
            return trade

        pnl = notional_eur * (opportunity.net_return - 1.0)
        trade = PaperTrade(
            trade_id=trade_id,
            path=list(opportunity.path),
            notional_eur=notional_eur,
            gross_return=opportunity.gross_return,
            net_return=opportunity.net_return,
            status=TradeStatus.EXECUTED,
            created_at=now,
            executed_at=now,
            pnl_eur=pnl,
        )
        self._trades.append(trade)
        return trade

    def get_trades(self) -> List[PaperTrade]:
        """Return all trades, newest first."""
        return list(reversed(self._trades))

    def total_pnl(self) -> float:
        """Sum of pnl_eur for all EXECUTED trades."""
        return sum(t.pnl_eur for t in self._trades if t.status == TradeStatus.EXECUTED)

    def summary(self) -> Dict:
        """Return dict with: total_trades, executed, rejected, total_pnl_eur, win_rate."""
        executed = [t for t in self._trades if t.status == TradeStatus.EXECUTED]
        rejected = [t for t in self._trades if t.status == TradeStatus.REJECTED]
        wins     = [t for t in executed if t.pnl_eur > 0]
        win_rate = len(wins) / len(executed) if executed else 0.0
        return {
            "total_trades":  len(self._trades),
            "executed":      len(executed),
            "rejected":      len(rejected),
            "total_pnl_eur": round(self.total_pnl(), 6),
            "win_rate":      round(win_rate, 4),
        }
