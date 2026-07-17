"""
babyai/trading/strategies/ecb_event.py — ECB-event-driven strategi.

Lytter på makro-events (ECB rentebeslutninger, CPI-tal) og
genererer crypto-signaler baseret på historisk correlationsmodel.

Korrelationsmodel (forenklet):
  - ECB hæver rente → RISK-OFF → BTC/ETH sælg-signal
  - ECB sænker rente → RISK-ON → BTC/ETH køb-signal
  - Overraskende inflation (høj) → RISK-OFF
  - Overraskende inflation (lav)  → RISK-ON

Input: event-payload fra Kafka topic 'macro.event' (emitteres af openbb-server)
Output: SignalEvent per konfigureret symbol

Params (i StrategyConfig.params):
  risk_on_symbols  : list (default ["BTCUSDT","ETHUSDT"])
  risk_off_symbols : list (default ["BTCUSDT","ETHUSDT"])
  confidence_base  : float (default 0.65)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from babyai.trading.strategy_base import (
    Candle, SignalDirection, SignalEvent, StrategyBase, StrategyConfig,
)


class ECBEventStrategy(StrategyBase):
    """
    Event-driven: drives signals from macro events, not candles.

    Kald `on_macro_event(payload)` direkte fra Kafka-consumer.
    `generate_signals()` returnerer altid [] — signaler skabes via events.
    """

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._risk_on_symbols  = config.params.get("risk_on_symbols",  ["BTCUSDT", "ETHUSDT"])
        self._risk_off_symbols = config.params.get("risk_off_symbols", ["BTCUSDT", "ETHUSDT"])
        self._confidence_base  = float(config.params.get("confidence_base", 0.65))
        self._pending_signals: List[SignalEvent] = []

    def generate_signals(self, candles: Dict[str, List[Candle]]) -> List[SignalEvent]:
        out = list(self._pending_signals)
        self._pending_signals.clear()
        return out

    def on_macro_event(self, payload: Dict[str, Any]) -> List[SignalEvent]:
        event_type = str(payload.get("event_type", "")).lower()
        surprise   = float(payload.get("surprise", 0.0))  # actual - consensus
        signals    = []

        direction, rationale = self._classify(event_type, surprise)
        if direction == SignalDirection.HOLD:
            return []

        symbols = self._risk_on_symbols if direction == SignalDirection.BUY else self._risk_off_symbols
        conf    = min(0.95, self._confidence_base + abs(surprise) * 0.5)

        for symbol in symbols:
            sig = SignalEvent(
                strategy_id=self.strategy_id,
                symbol=symbol,
                direction=direction,
                confidence=conf,
                quantity=0.0,
                rationale=rationale,
                meta={"event": payload},
            )
            signals.append(sig)
            self._pending_signals.append(sig)

        return signals

    def _classify(
        self, event_type: str, surprise: float
    ):
        if "rate" in event_type or "interest" in event_type:
            if surprise > 0:
                return SignalDirection.SELL, f"ecb_rate_hike_surprise={surprise:+.2f}"
            if surprise < 0:
                return SignalDirection.BUY, f"ecb_rate_cut_surprise={surprise:+.2f}"

        if "cpi" in event_type or "inflation" in event_type:
            if surprise > 0.1:
                return SignalDirection.SELL, f"inflation_surprise_high={surprise:+.2f}"
            if surprise < -0.1:
                return SignalDirection.BUY, f"inflation_surprise_low={surprise:+.2f}"

        return SignalDirection.HOLD, ""
