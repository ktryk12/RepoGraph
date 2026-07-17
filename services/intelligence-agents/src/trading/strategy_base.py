"""
babyai/trading/strategy_base.py — Abstrakt strategi-interface.

Alle strategier implementerer StrategyBase og returnerer SignalEvent.
broker-gateway modtager signal via Kafka topic 'signal.generated'.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


class SignalDirection(str, Enum):
    BUY   = "BUY"
    SELL  = "SELL"
    HOLD  = "HOLD"


@dataclass(frozen=True)
class Candle:
    symbol:    str
    timestamp: datetime
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float


@dataclass
class SignalEvent:
    signal_id:        str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id:      str = ""
    symbol:           str = ""
    direction:        SignalDirection = SignalDirection.HOLD
    confidence:       float = 0.0          # 0.0–1.0
    quantity:         float = 0.0
    price:            Optional[float] = None
    stop_loss_price:  Optional[float] = None
    take_profit_price: Optional[float] = None
    order_type:       str = "MARKET"
    rationale:        str = ""
    meta:             Dict[str, Any] = field(default_factory=dict)
    generated_at:     str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_kafka_payload(self) -> Dict[str, Any]:
        return {
            "signal_id":          self.signal_id,
            "strategy_id":        self.strategy_id,
            "symbol":             self.symbol,
            "side":               self.direction.value,
            "quantity":           self.quantity,
            "price":              self.price,
            "stop_loss_price":    self.stop_loss_price,
            "take_profit_price":  self.take_profit_price,
            "order_type":         self.order_type,
            "confidence":         self.confidence,
            "rationale":          self.rationale,
            "meta":               self.meta,
            "timestamp":          self.generated_at,
        }

    @property
    def is_actionable(self) -> bool:
        return self.direction != SignalDirection.HOLD and self.quantity > 0


@dataclass
class StrategyConfig:
    strategy_id:      str
    symbols:          List[str]
    timeframe:        str = "1h"           # "1m", "5m", "15m", "1h", "4h", "1d"
    sizing_method:    str = "fixed_fractional"
    max_risk_pct:     float = 0.01
    stop_loss_pct:    float = 0.03
    take_profit_pct:  float = 0.06
    enabled:          bool = True
    params:           Dict[str, Any] = field(default_factory=dict)


class StrategyBase(ABC):
    """
    Abstrakt strategi. Implementér `generate_signals()`.

    Lifecycle:
      1. `on_start()` — kaldt én gang ved opstart
      2. `generate_signals(candles)` — kaldt per ny candle-batch
      3. `on_stop()` — kaldt ved shutdown
    """

    def __init__(self, config: StrategyConfig) -> None:
        self.config = config

    @property
    def strategy_id(self) -> str:
        return self.config.strategy_id

    @abstractmethod
    def generate_signals(self, candles: Dict[str, List[Candle]]) -> List[SignalEvent]:
        """
        Modtag seneste candles per symbol.
        Returnér liste af SignalEvents (kan være tom).
        """

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def _size(self, symbol: str, price: float, capital_usdt: float) -> float:
        """Convenience: beregn quantity via fixed-fractional."""
        from babyai.trading.position_sizer import PositionSizer, SizingMethod
        sizer = PositionSizer(
            max_risk_pct=self.config.max_risk_pct,
            max_position_pct=0.10,
        )
        result = sizer.size(
            method=SizingMethod.FIXED_FRACTIONAL,
            capital_usdt=capital_usdt,
            price=price,
            stop_loss_pct=self.config.stop_loss_pct,
        )
        return result.quantity
