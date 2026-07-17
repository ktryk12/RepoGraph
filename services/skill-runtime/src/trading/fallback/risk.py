"""
Risk management: Kelly criterion, position sizing, max drawdown.
Pure Python — no external dependencies.
"""
from __future__ import annotations

from typing import Dict, List, Optional


def kelly_fraction(win_rate: float, win_loss_ratio: float) -> float:
    """
    Kelly criterion: f* = (bp - q) / b
    win_rate:       probability of winning (0..1)
    win_loss_ratio: average win / average loss (must be > 0)
    Returns fraction of capital to risk (clamped 0..1).
    """
    if win_loss_ratio <= 0:
        return 0.0
    q = 1.0 - win_rate
    fraction = (win_loss_ratio * win_rate - q) / win_loss_ratio
    return max(0.0, min(1.0, fraction))


def position_size(
    account_equity: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
) -> Dict[str, float]:
    """
    Fixed-fraction position sizing.
    Returns units to buy and capital at risk.
    """
    if entry_price <= 0 or stop_loss_price <= 0 or account_equity <= 0:
        return {"units": 0.0, "capital_at_risk": 0.0}
    risk_amount = account_equity * min(max(risk_pct, 0.0), 1.0)
    price_risk = abs(entry_price - stop_loss_price)
    if price_risk == 0:
        return {"units": 0.0, "capital_at_risk": 0.0}
    units = risk_amount / price_risk
    capital_at_risk = units * price_risk
    return {"units": units, "capital_at_risk": capital_at_risk}


def max_drawdown(equity_curve: List[float]) -> float:
    """Maximum drawdown as a fraction (0..1)."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def sharpe_ratio(returns: List[float], risk_free_rate: float = 0.0) -> Optional[float]:
    """Annualized Sharpe ratio from a list of periodic returns."""
    if len(returns) < 2:
        return None
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = variance ** 0.5
    if std == 0:
        return None
    excess = mean - risk_free_rate
    # Annualize assuming daily returns (252 trading days)
    return (excess / std) * (252 ** 0.5)


def assess_risk(
    confidence: float,
    account_equity: float,
    entry_price: float,
    stop_loss_price: float,
    max_position_pct: float = 0.05,
) -> Dict[str, object]:
    """
    Given a trade signal confidence, compute recommended position size
    using a fraction of Kelly, capped at max_position_pct.
    """
    # Estimate win_rate from confidence, win/loss ratio = 2:1 assumption
    win_rate = max(0.0, min(1.0, confidence))
    raw_kelly = kelly_fraction(win_rate, 2.0)
    # Use half-Kelly for safety
    risk_pct = min(raw_kelly * 0.5, max_position_pct)
    sizing = position_size(account_equity, risk_pct, entry_price, stop_loss_price)
    return {
        "kelly_fraction": raw_kelly,
        "recommended_risk_pct": risk_pct,
        "units": sizing["units"],
        "capital_at_risk": sizing["capital_at_risk"],
        "max_position_pct": max_position_pct,
    }
