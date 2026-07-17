"""
babyai/trading/position_sizer.py — Position sizing algorithms.

Three methods:
  - Kelly criterion (fractional Kelly med cap)
  - Fixed fractional (fast % af kapital)
  - Risk parity (equal volatility contribution)

Alle returnerer quantity i base-asset (fx BTC for BTCUSDT).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class SizingMethod(str, Enum):
    KELLY           = "kelly"
    FIXED_FRACTIONAL = "fixed_fractional"
    RISK_PARITY     = "risk_parity"


@dataclass(frozen=True)
class SizingResult:
    quantity: float          # base-asset antal
    notional_usdt: float     # estimeret USDT-værdi
    method: SizingMethod
    fraction_used: float     # andel af kapital brugt
    capped: bool             # True hvis kelly-cap eller max-notional ramtes


class PositionSizer:
    """
    Beregner ordrestørrelse ud fra kontokapital, risiko-parametre og pris.

    Parameters
    ----------
    max_risk_pct : float
        Maks % af kapital der risikeres per trade (default 1%).
    kelly_fraction : float
        Kelly-brøk — 0.25 = quarter-Kelly (default).
    max_position_pct : float
        Absolut loft: maks % af kapital i én position (default 10%).
    min_quantity : float
        Minimum ordre-størrelse (default 0.0001).
    """

    def __init__(
        self,
        max_risk_pct: float = 0.01,
        kelly_fraction: float = 0.25,
        max_position_pct: float = 0.10,
        min_quantity: float = 0.0001,
    ) -> None:
        self._max_risk_pct    = max_risk_pct
        self._kelly_fraction  = kelly_fraction
        self._max_position_pct = max_position_pct
        self._min_quantity    = min_quantity

    def size(
        self,
        *,
        method: SizingMethod,
        capital_usdt: float,
        price: float,
        win_rate: float = 0.55,
        avg_win: float = 0.02,
        avg_loss: float = 0.01,
        stop_loss_pct: float = 0.03,
        volatility: Optional[float] = None,
        peer_volatilities: Optional[List[float]] = None,
    ) -> SizingResult:
        if price <= 0 or capital_usdt <= 0:
            return SizingResult(0.0, 0.0, method, 0.0, False)

        if method == SizingMethod.KELLY:
            return self._kelly(capital_usdt, price, win_rate, avg_win, avg_loss)
        if method == SizingMethod.FIXED_FRACTIONAL:
            return self._fixed_fractional(capital_usdt, price, stop_loss_pct)
        if method == SizingMethod.RISK_PARITY:
            return self._risk_parity(capital_usdt, price, volatility, peer_volatilities)
        return self._fixed_fractional(capital_usdt, price, stop_loss_pct)

    # ── Algorithms ────────────────────────────────────────────────────────────

    def _kelly(
        self, capital: float, price: float,
        win_rate: float, avg_win: float, avg_loss: float
    ) -> SizingResult:
        if avg_loss <= 0:
            return SizingResult(0.0, 0.0, SizingMethod.KELLY, 0.0, False)
        b = avg_win / avg_loss
        p = win_rate
        q = 1.0 - p
        kelly_full = (b * p - q) / b if b > 0 else 0.0
        kelly_used = max(0.0, kelly_full * self._kelly_fraction)

        max_frac  = self._max_position_pct
        capped    = kelly_used > max_frac
        fraction  = min(kelly_used, max_frac)
        notional  = capital * fraction
        quantity  = notional / price
        quantity  = max(self._min_quantity, round(quantity, 8))

        return SizingResult(
            quantity=quantity,
            notional_usdt=quantity * price,
            method=SizingMethod.KELLY,
            fraction_used=fraction,
            capped=capped,
        )

    def _fixed_fractional(
        self, capital: float, price: float, stop_loss_pct: float
    ) -> SizingResult:
        # Risk amount = capital × max_risk_pct
        # Position size = risk_amount / stop_loss_distance
        stop_dist  = price * stop_loss_pct if stop_loss_pct > 0 else price * 0.03
        risk_usdt  = capital * self._max_risk_pct
        notional   = risk_usdt / stop_loss_pct if stop_loss_pct > 0 else risk_usdt * 10

        max_notional = capital * self._max_position_pct
        capped   = notional > max_notional
        notional = min(notional, max_notional)
        quantity = max(self._min_quantity, round(notional / price, 8))

        return SizingResult(
            quantity=quantity,
            notional_usdt=quantity * price,
            method=SizingMethod.FIXED_FRACTIONAL,
            fraction_used=notional / capital,
            capped=capped,
        )

    def _risk_parity(
        self,
        capital: float,
        price: float,
        volatility: Optional[float],
        peer_volatilities: Optional[List[float]],
    ) -> SizingResult:
        vol = volatility or 0.02
        peers = list(peer_volatilities or [vol])
        if not peers:
            peers = [vol]
        # Equal risk contribution: w_i = (1/vol_i) / sum(1/vol_j)
        inv_vol   = 1.0 / vol if vol > 0 else 1.0
        sum_inv   = sum(1.0 / v for v in peers if v > 0) or 1.0
        weight    = inv_vol / sum_inv
        max_w     = self._max_position_pct
        capped    = weight > max_w
        weight    = min(weight, max_w)
        notional  = capital * weight
        quantity  = max(self._min_quantity, round(notional / price, 8))

        return SizingResult(
            quantity=quantity,
            notional_usdt=quantity * price,
            method=SizingMethod.RISK_PARITY,
            fraction_used=weight,
            capped=capped,
        )
