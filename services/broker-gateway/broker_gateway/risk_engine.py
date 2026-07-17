"""
broker_gateway/risk_engine.py — Pre-trade risk checks.

Alle checks returnerer (passed: bool, reason: str).
broker-gateway afviser ordren hvis passed=False.

Policy-parametre læses fra env vars med konservative defaults.

Env vars:
  RISK_MAX_ORDER_USDT      : max enkelt-ordres notional (default: 500)
  RISK_MAX_POSITION_USDT   : max åben position per symbol (default: 2000)
  RISK_MAX_DAILY_LOSS_USDT : stop-loss for dagligt tab (default: 200)
  RISK_MAX_OPEN_POSITIONS  : max antal åbne positioner (default: 5)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Tuple

from broker_gateway.interfaces.broker_adapter import OrderIntent, OrderSide

_log = logging.getLogger("risk-engine")

_MAX_ORDER_USDT     = float(os.getenv("RISK_MAX_ORDER_USDT", "500"))
_MAX_POSITION_USDT  = float(os.getenv("RISK_MAX_POSITION_USDT", "2000"))
_MAX_DAILY_LOSS     = float(os.getenv("RISK_MAX_DAILY_LOSS_USDT", "200"))
_MAX_OPEN_POSITIONS = int(os.getenv("RISK_MAX_OPEN_POSITIONS", "5"))


@dataclass
class RiskState:
    daily_pnl: float = 0.0
    open_position_count: int = 0
    position_notionals: Dict[str, float] = field(default_factory=dict)
    _day: date = field(default_factory=lambda: datetime.now(timezone.utc).date())

    def record_fill(self, symbol: str, side: OrderSide, notional: float, pnl: float = 0.0) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self.daily_pnl = 0.0
            self._day = today
        self.daily_pnl += pnl
        if side == OrderSide.BUY:
            self.position_notionals[symbol] = self.position_notionals.get(symbol, 0.0) + notional
            self.open_position_count = len(self.position_notionals)
        elif side == OrderSide.SELL:
            remaining = self.position_notionals.get(symbol, 0.0) - notional
            if remaining <= 0:
                self.position_notionals.pop(symbol, None)
            else:
                self.position_notionals[symbol] = remaining
            self.open_position_count = len(self.position_notionals)


class RiskEngine:
    """
    Stateful pre-trade risk gate.

    check(intent, current_price) → (True, "") eller (False, reason)
    """

    def __init__(self) -> None:
        self._state = RiskState()

    def check(self, intent: OrderIntent, current_price: float) -> Tuple[bool, str]:
        checks = [
            self._check_notional(intent, current_price),
            self._check_position_size(intent, current_price),
            self._check_daily_loss(),
            self._check_open_positions(intent),
        ]
        for passed, reason in checks:
            if not passed:
                _log.warning("risk_check_failed order_id=%s reason=%s", intent.order_id, reason)
                return False, reason
        return True, ""

    def record_fill(self, symbol: str, side: OrderSide, notional: float, pnl: float = 0.0) -> None:
        self._state.record_fill(symbol, side, notional, pnl)

    def snapshot(self) -> dict:
        return {
            "daily_pnl":          round(self._state.daily_pnl, 4),
            "open_position_count": self._state.open_position_count,
            "position_notionals": dict(self._state.position_notionals),
            "limits": {
                "max_order_usdt":      _MAX_ORDER_USDT,
                "max_position_usdt":   _MAX_POSITION_USDT,
                "max_daily_loss_usdt": _MAX_DAILY_LOSS,
                "max_open_positions":  _MAX_OPEN_POSITIONS,
            },
        }

    # ── Checks ────────────────────────────────────────────────────────────────

    def _check_notional(self, intent: OrderIntent, price: float) -> Tuple[bool, str]:
        notional = intent.notional(price)
        if notional > _MAX_ORDER_USDT:
            return False, f"order_notional={notional:.2f} exceeds max={_MAX_ORDER_USDT}"
        return True, ""

    def _check_position_size(self, intent: OrderIntent, price: float) -> Tuple[bool, str]:
        if intent.side == OrderSide.SELL:
            return True, ""
        existing = self._state.position_notionals.get(intent.symbol, 0.0)
        new_total = existing + intent.notional(price)
        if new_total > _MAX_POSITION_USDT:
            return False, f"position_notional={new_total:.2f} exceeds max={_MAX_POSITION_USDT}"
        return True, ""

    def _check_daily_loss(self) -> Tuple[bool, str]:
        if self._state.daily_pnl < -_MAX_DAILY_LOSS:
            return False, f"daily_pnl={self._state.daily_pnl:.2f} below max_loss=-{_MAX_DAILY_LOSS}"
        return True, ""

    def _check_open_positions(self, intent: OrderIntent) -> Tuple[bool, str]:
        if intent.side == OrderSide.SELL:
            return True, ""
        symbol_already_open = intent.symbol in self._state.position_notionals
        if not symbol_already_open and self._state.open_position_count >= _MAX_OPEN_POSITIONS:
            return False, f"open_positions={self._state.open_position_count} at max={_MAX_OPEN_POSITIONS}"
        return True, ""
