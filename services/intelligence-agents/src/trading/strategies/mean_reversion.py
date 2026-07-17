"""
babyai/trading/strategies/mean_reversion.py — Bollinger Band mean-reversion.

Signal-logik:
  BUY  når close krydser under lower band (oversolgt) + volumen spike
  SELL når close krydser over upper band (overkøbt) + volumen spike

Params (i StrategyConfig.params):
  bb_period : int   (default 20)
  bb_std    : float (default 2.0)
  vol_mult  : float volumen-spike multiplier (default 1.5)
"""
from __future__ import annotations

import statistics
from typing import Dict, List

from babyai.trading.strategy_base import (
    Candle, SignalDirection, SignalEvent, StrategyBase, StrategyConfig,
)


def _bollinger(closes: List[float], period: int, n_std: float):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid  = statistics.mean(window)
    std  = statistics.stdev(window)
    return mid - n_std * std, mid, mid + n_std * std


class MeanReversionStrategy(StrategyBase):

    def __init__(self, config: StrategyConfig) -> None:
        super().__init__(config)
        self._period   = int(config.params.get("bb_period", 20))
        self._n_std    = float(config.params.get("bb_std", 2.0))
        self._vol_mult = float(config.params.get("vol_mult", 1.5))

    def generate_signals(self, candles: Dict[str, List[Candle]]) -> List[SignalEvent]:
        signals = []
        for symbol, bars in candles.items():
            if len(bars) < self._period + 1:
                continue
            closes  = [b.close for b in bars]
            volumes = [b.volume for b in bars]
            lower, mid, upper = _bollinger(closes, self._period, self._n_std)
            if lower is None:
                continue

            price     = closes[-1]
            prev      = closes[-2]
            avg_vol   = statistics.mean(volumes[-self._period:])
            cur_vol   = volumes[-1]
            vol_spike = cur_vol > avg_vol * self._vol_mult

            sl = round(price * (1 - self.config.stop_loss_pct), 8)
            tp = round(mid, 8)  # target: reversion to mean

            if prev >= lower and price < lower and vol_spike:
                signals.append(SignalEvent(
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    direction=SignalDirection.BUY,
                    confidence=0.70,
                    quantity=0.0,
                    stop_loss_price=sl,
                    take_profit_price=tp,
                    rationale=f"bb_lower_break price={price:.4f} lower={lower:.4f} vol_spike={vol_spike}",
                ))

            elif prev <= upper and price > upper and vol_spike:
                signals.append(SignalEvent(
                    strategy_id=self.strategy_id,
                    symbol=symbol,
                    direction=SignalDirection.SELL,
                    confidence=0.70,
                    quantity=0.0,
                    rationale=f"bb_upper_break price={price:.4f} upper={upper:.4f} vol_spike={vol_spike}",
                ))

        return signals
