"""
babyai/trading/strategies/momentum.py — Dual EMA momentum strategi.

Signal-logik:
  BUY  når EMA_fast krydser over EMA_slow (golden cross) + RSI < 70
  SELL når EMA_fast krydser under EMA_slow (death cross) + RSI > 30

Params (i StrategyConfig.params):
  ema_fast   : int (default 9)
  ema_slow   : int (default 21)
  rsi_period : int (default 14)
  rsi_ob     : float overbought (default 70)
  rsi_os     : float oversold   (default 30)
"""
from __future__ import annotations

from typing import Dict, List, Optional

from babyai.trading.strategy_base import (
    Candle, SignalDirection, SignalEvent, StrategyBase, StrategyConfig,
)


def _ema(values: List[float], period: int) -> List[float]:
    if len(values) < period:
        return []
    k = 2.0 / (period + 1)
    result = [sum(values[:period]) / period]
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


class MomentumStrategy(StrategyBase):

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._ema_fast  = int(config.params.get("ema_fast", 9))
        self._ema_slow  = int(config.params.get("ema_slow", 21))
        self._rsi_period = int(config.params.get("rsi_period", 14))
        self._rsi_ob    = float(config.params.get("rsi_ob", 70))
        self._rsi_os    = float(config.params.get("rsi_os", 30))
        self._prev_cross: Dict[str, Optional[str]] = {}

    def generate_signals(self, candles: Dict[str, List[Candle]]) -> List[SignalEvent]:
        signals = []
        for symbol, bars in candles.items():
            if len(bars) < self._ema_slow + 2:
                continue
            closes = [b.close for b in bars]
            fast   = _ema(closes, self._ema_fast)
            slow   = _ema(closes, self._ema_slow)
            if len(fast) < 2 or len(slow) < 2:
                continue

            rsi = _rsi(closes, self._rsi_period)
            cur_cross = "above" if fast[-1] > slow[-1] else "below"
            prv_cross = self._prev_cross.get(symbol)
            self._prev_cross[symbol] = cur_cross

            price = closes[-1]
            sl    = round(price * (1 - self.config.stop_loss_pct), 8)
            tp    = round(price * (1 + self.config.take_profit_pct), 8)

            if prv_cross == "below" and cur_cross == "above":
                if rsi is None or rsi < self._rsi_ob:
                    signals.append(SignalEvent(
                        strategy_id=self.strategy_id,
                        symbol=symbol,
                        direction=SignalDirection.BUY,
                        confidence=min(0.9, 0.6 + (self._rsi_ob - (rsi or 50)) / 100),
                        quantity=0.0,  # order-manager sizing
                        stop_loss_price=sl,
                        take_profit_price=tp,
                        rationale=f"golden_cross ema{self._ema_fast}/{self._ema_slow} rsi={rsi:.1f}" if rsi else "golden_cross",
                    ))

            elif prv_cross == "above" and cur_cross == "below":
                if rsi is None or rsi > self._rsi_os:
                    signals.append(SignalEvent(
                        strategy_id=self.strategy_id,
                        symbol=symbol,
                        direction=SignalDirection.SELL,
                        confidence=min(0.9, 0.6 + ((rsi or 50) - self._rsi_os) / 100),
                        quantity=0.0,
                        stop_loss_price=None,
                        take_profit_price=None,
                        rationale=f"death_cross ema{self._ema_fast}/{self._ema_slow} rsi={rsi:.1f}" if rsi else "death_cross",
                    ))

        return signals
