"""
agents/ecb_trading_agent.py — Paper-only ECB FX currency arbitrage agent.

Polls ECB exchange rates every 30 seconds.
Detects triangular arbitrage opportunities via ArbitrageDetector.
Publishes TRADE_SIGNAL, TRADE_EXECUTED, or TRADE_REJECTED to Kafka.
Writes every decision (execute / reject / wait) to DatasetWriter for LoRA training.

L7 boundary — requires_action is ALWAYS False.
No live orders are ever placed. Paper ledger only.

Usage:
    agent = ECBTradingAgent(
        brokers="localhost:9092",
        ecb_client=ECBClient(),
        detector=ArbitrageDetector(),
        ledger=PaperLedger(),
        dataset_writer=DatasetWriter(),
    )
    asyncio.run(agent.run())
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents._kafka_publisher import _KafkaPublisher
from babyai_shared.bus.protocol import MessageType
from babyai.trading.arbitrage import ArbitrageDetector, ArbitrageOpportunity
from babyai.trading.dataset_writer import DatasetWriter
from babyai.trading.ecb_client import ECBClient
from babyai.trading.paper_ledger import PaperLedger, TradeStatus

_log = logging.getLogger(__name__)

_STOP_LOSS_THRESHOLD = -500.0   # EUR — halt if cumulative PnL drops below this


class ECBTradingAgent:
    """
    Paper-only ECB FX currency arbitrage agent.

    Parameters
    ----------
    brokers:                Kafka bootstrap servers string.
    ecb_client:             ECBClient instance for rate fetching.
    detector:               ArbitrageDetector instance.
    ledger:                 PaperLedger instance.
    dataset_writer:         DatasetWriter instance.
    poll_interval_seconds:  Seconds between ECB polls. Default 30.
    notional_eur:           EUR notional per trade. Default 1 000.
    publisher:              Optional injected _KafkaPublisher (for testing).
    """

    def __init__(
        self,
        brokers: str,
        ecb_client: ECBClient,
        detector: ArbitrageDetector,
        ledger: PaperLedger,
        dataset_writer: DatasetWriter,
        poll_interval_seconds: int = 30,
        notional_eur: float = 1_000.0,
        publisher: Optional[_KafkaPublisher] = None,
    ) -> None:
        self._ecb      = ecb_client
        self._detector = detector
        self._ledger   = ledger
        self._writer   = dataset_writer
        self._interval = poll_interval_seconds
        self._notional = notional_eur
        self._pub: _KafkaPublisher = publisher or _KafkaPublisher(
            brokers=brokers, log_prefix="ecb_trading_agent"
        )
        self._running  = False
        self._agent_id = "ecb-trading-agent-001"

    # ── Main loop ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main loop — poll → detect → decide → record → publish."""
        self._running = True
        _log.info(
            "ecb_trading_agent_started interval_s=%d notional_eur=%.0f",
            self._interval, self._notional,
        )
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                _log.error("ecb_trading_agent_tick_error error=%s", exc)
            await asyncio.sleep(self._interval)

    def stop(self) -> None:
        """Request graceful shutdown after the current tick completes."""
        self._running = False

    # ── Tick ───────────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        rates         = await self._ecb.get_rates()
        opportunities = self._detector.detect(rates)

        if not opportunities:
            await self._wait(rates)
            return

        # Publish TRADE_SIGNAL for the best opportunity
        best = opportunities[0]
        self._pub.publish(
            topic=MessageType.TRADE_SIGNAL.value,
            key=self._agent_id,
            value=self._signal_payload(best, rates),
        )

        for opp in opportunities:
            await self._on_opportunity(opp, rates)

    # ── Opportunity handler ─────────────────────────────────────────────────────

    async def _on_opportunity(
        self, opportunity: ArbitrageOpportunity, rates: Dict[str, float]
    ) -> None:
        """
        Handle one detected opportunity:
        1. Check stop-loss (total_pnl < -500 EUR → REJECT).
        2. Submit to PaperLedger.
        3. Build reasoning string.
        4. Write to DatasetWriter.
        5. Publish TRADE_EXECUTED or TRADE_REJECTED.
        """
        total_pnl = self._ledger.total_pnl()

        # Stop-loss gate
        if total_pnl < _STOP_LOSS_THRESHOLD:
            reasoning = (
                f"STOP_LOSS triggered: cumulative_pnl={total_pnl:.2f} EUR "
                f"< threshold={_STOP_LOSS_THRESHOLD:.0f} EUR. "
                f"Rejecting path={'→'.join(opportunity.path)} "
                f"net_return={opportunity.net_return:.6f}."
            )
            _log.warning("ecb_trading_agent_stop_loss pnl=%.2f", total_pnl)
            self._writer.record(
                rates=rates,
                opportunities=[opportunity],
                reasoning=reasoning,
                action="REJECT",
                trade=None,
            )
            self._pub.publish(
                topic=MessageType.TRADE_REJECTED.value,
                key=self._agent_id,
                value=self._decision_payload(
                    opportunity, action="REJECT", reason="stop_loss", trade_id=None
                ),
            )
            return

        # Submit to paper ledger
        trade  = self._ledger.submit(opportunity, self._notional)
        action = "EXECUTE" if trade.status == TradeStatus.EXECUTED else "REJECT"

        reasoning = (
            f"Triangular arbitrage detected: path={'→'.join(opportunity.path)}, "
            f"gross_return={opportunity.gross_return:.6f}, "
            f"net_return={opportunity.net_return:.6f}, "
            f"notional={self._notional:.0f} EUR, "
            f"decision={action}, "
            f"pnl_eur={trade.pnl_eur:.4f}, "
            f"cumulative_pnl={self._ledger.total_pnl():.4f} EUR."
        )

        self._writer.record(
            rates=rates,
            opportunities=[opportunity],
            reasoning=reasoning,
            action=action,
            trade=trade,
        )

        topic = (
            MessageType.TRADE_EXECUTED.value
            if trade.status == TradeStatus.EXECUTED
            else MessageType.TRADE_REJECTED.value
        )
        self._pub.publish(
            topic=topic,
            key=trade.trade_id,
            value=self._trade_payload(trade, opportunity),
        )

        _log.info(
            "ecb_trading_agent_%s path=%s net_return=%.6f pnl=%.4f",
            action.lower(),
            "→".join(opportunity.path),
            opportunity.net_return,
            trade.pnl_eur,
        )

    # ── Wait (no opportunity) ───────────────────────────────────────────────────

    async def _wait(self, rates: Dict[str, float]) -> None:
        """No opportunities found — write WAIT record to dataset."""
        reasoning = (
            f"No arbitrage opportunities above threshold at "
            f"{datetime.now(timezone.utc).isoformat()}. "
            f"Rates snapshot: "
            f"{json.dumps({k: round(v, 4) for k, v in rates.items()})}."
        )
        self._writer.record(
            rates=rates,
            opportunities=[],
            reasoning=reasoning,
            action="WAIT",
            trade=None,
        )
        _log.debug("ecb_trading_agent_wait rates_count=%d", len(rates))

    # ── Payload builders ────────────────────────────────────────────────────────

    def _signal_payload(
        self, opp: ArbitrageOpportunity, rates: Dict[str, float]
    ) -> Dict[str, Any]:
        return {
            "source":                self._agent_id,
            "timestamp":             datetime.now(timezone.utc).isoformat(),
            "path":                  opp.path,
            "gross_return":          round(opp.gross_return, 6),
            "net_return":            round(opp.net_return, 6),
            "rates_snapshot":        {k: round(v, 4) for k, v in rates.items()},
            "requires_action":       False,
            "requires_human_review": False,
        }

    def _decision_payload(
        self,
        opp: ArbitrageOpportunity,
        action: str,
        reason: str,
        trade_id: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "source":          self._agent_id,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "action":          action,
            "reason":          reason,
            "path":            opp.path,
            "net_return":      round(opp.net_return, 6),
            "trade_id":        trade_id,
            "requires_action": False,
        }

    def _trade_payload(
        self, trade: Any, opp: ArbitrageOpportunity
    ) -> Dict[str, Any]:
        return {
            "source":          self._agent_id,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
            "trade_id":        trade.trade_id,
            "path":            trade.path,
            "notional_eur":    trade.notional_eur,
            "gross_return":    round(trade.gross_return, 6),
            "net_return":      round(trade.net_return, 6),
            "pnl_eur":         round(trade.pnl_eur, 6),
            "status":          trade.status.value,
            "requires_action": False,
        }
