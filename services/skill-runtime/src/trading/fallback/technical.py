"""
Technical analysis indicators.
All indicator functions accept a list of floats (closing prices).
analyze() accepts either a pandas DataFrame (with 'close' column) or a list of floats.

Minimums before computing each indicator:
  RSI:  >= 15 prices   (period 14 + 1 delta)
  MACD: >= 35 prices   (slow 26 + signal 9)
  SMA20: >= 20 prices
  SMA50: >= 50 prices  (None if insufficient — never falls back to shorter window)
  BB:   >= 20 prices

Signal thresholds (crypto-adjusted):
  RSI oversold:  < 35   (wider than stock market 30 to catch more crypto moves)
  RSI overbought: > 65

Confidence scoring:
  Base: 0.50
  +0.10 per confirming signal
  Min 2 confirming signals required for BUY or SELL action.
  Single confirming signal → HOLD with confidence = base + 0.10.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

# ─────────────────────────────────────────────────────────────────────────────
# Minimum data requirements
# ─────────────────────────────────────────────────────────────────────────────
_MIN_RSI = 15
_MIN_MACD = 35   # slow(26) + signal(9)
_MIN_SMA20 = 20
_MIN_SMA50 = 50
_MIN_BB = 20

_RSI_OVERSOLD = 35.0
_RSI_OVERBOUGHT = 65.0


# ─────────────────────────────────────────────────────────────────────────────
# Low-level indicators (list[float] → list[Optional[float]])
# ─────────────────────────────────────────────────────────────────────────────

def sma(prices: List[float], period: int) -> List[Optional[float]]:
    """Simple Moving Average. Returns None for positions with insufficient history."""
    result: List[Optional[float]] = [None] * len(prices)
    if period <= 0 or len(prices) < period:
        return result
    for i in range(period - 1, len(prices)):
        result[i] = sum(prices[i - period + 1 : i + 1]) / period
    return result


def ema(prices: List[float], period: int) -> List[Optional[float]]:
    """Exponential Moving Average. Seeded from SMA of first `period` values."""
    result: List[Optional[float]] = [None] * len(prices)
    if not prices or period <= 0 or len(prices) < period:
        return result
    k = 2.0 / (period + 1)
    seed = sum(prices[:period]) / period
    result[period - 1] = seed
    for i in range(period, len(prices)):
        prev = result[i - 1]
        result[i] = prices[i] * k + (prev if prev is not None else prices[i]) * (1 - k)
    return result


def rsi(prices: List[float], period: int = 14) -> List[Optional[float]]:
    """
    Relative Strength Index using Wilder's smoothing.
    Returns None for indices where there is insufficient history.
    """
    result: List[Optional[float]] = [None] * len(prices)
    if len(prices) < period + 1:
        return result

    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(prices)):
        delta = prices[i] - prices[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def _rs_to_rsi(avg_g: float, avg_l: float) -> float:
        if avg_l < 1e-12:
            return 100.0 if avg_g > 0 else 50.0  # avoid artificial extremes
        rs = avg_g / avg_l
        return 100.0 - (100.0 / (1.0 + rs))

    result[period] = _rs_to_rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(prices)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        result[i] = _rs_to_rsi(avg_gain, avg_loss)

    return result


def macd(
    prices: List[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Dict[str, List[Optional[float]]]:
    """MACD line, signal line, and histogram. All None when insufficient data."""
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)

    macd_line: List[Optional[float]] = [
        (f - s) if f is not None and s is not None else None
        for f, s in zip(ema_fast, ema_slow)
    ]

    # Signal line = EMA of MACD values only (no Nones)
    macd_values = [v for v in macd_line if v is not None]
    signal_values = ema(macd_values, signal_period)

    signal_line: List[Optional[float]] = [None] * len(macd_line)
    none_count = sum(1 for v in macd_line if v is None)
    for idx, val in enumerate(signal_values):
        signal_line[none_count + idx] = val

    histogram: List[Optional[float]] = [
        (m - s) if m is not None and s is not None else None
        for m, s in zip(macd_line, signal_line)
    ]

    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def bollinger_bands(
    prices: List[float], period: int = 20, num_std: float = 2.0
) -> Dict[str, List[Optional[float]]]:
    """Bollinger Bands: upper, middle (SMA), lower."""
    middle = sma(prices, period)
    upper: List[Optional[float]] = [None] * len(prices)
    lower: List[Optional[float]] = [None] * len(prices)

    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1 : i + 1]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance ** 0.5
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std

    return {"upper": upper, "middle": middle, "lower": lower}


# ─────────────────────────────────────────────────────────────────────────────
# Helper to extract close prices from DataFrame or list
# ─────────────────────────────────────────────────────────────────────────────

def _extract_closes(data: Any) -> List[float]:
    """Accept DataFrame (with 'close' column) or list of floats."""
    try:
        import pandas as pd
        if isinstance(data, pd.DataFrame):
            if "close" not in data.columns:
                return []
            return [float(v) for v in data["close"].dropna().tolist()]
    except ImportError:
        pass
    if isinstance(data, list):
        result = []
        for v in data:
            try:
                result.append(float(v))
            except Exception:
                pass
        return result
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def analyze(data: Any) -> Dict[str, Any]:
    """
    Run all technical indicators and return a summary dict.

    Args:
        data: pandas DataFrame with 'close' column, OR list of float closing prices.

    Returns dict with keys:
        rsi, macd, macd_signal, macd_histogram, bb_upper, bb_lower,
        sma20, sma50, price, signals, action, confidence
    Returns empty dict on error or insufficient data.
    """
    prices = _extract_closes(data)
    if not prices or len(prices) < _MIN_RSI:
        return {}

    try:
        n = len(prices)

        # ── RSI ───────────────────────────────────────────────────────────────
        latest_rsi: Optional[float] = None
        if n >= _MIN_RSI:
            rsi_vals = rsi(prices)
            latest_rsi = next((v for v in reversed(rsi_vals) if v is not None), None)

        # ── MACD ──────────────────────────────────────────────────────────────
        latest_macd: Optional[float] = None
        latest_signal_line: Optional[float] = None
        latest_hist: Optional[float] = None
        if n >= _MIN_MACD:
            macd_vals = macd(prices)
            latest_macd = next((v for v in reversed(macd_vals["macd"]) if v is not None), None)
            latest_signal_line = next((v for v in reversed(macd_vals["signal"]) if v is not None), None)
            latest_hist = next((v for v in reversed(macd_vals["histogram"]) if v is not None), None)

        # ── SMA ───────────────────────────────────────────────────────────────
        latest_sma20: Optional[float] = None
        if n >= _MIN_SMA20:
            sma20_vals = sma(prices, 20)
            latest_sma20 = next((v for v in reversed(sma20_vals) if v is not None), None)

        latest_sma50: Optional[float] = None
        if n >= _MIN_SMA50:
            sma50_vals = sma(prices, 50)
            latest_sma50 = next((v for v in reversed(sma50_vals) if v is not None), None)

        # ── Bollinger Bands ───────────────────────────────────────────────────
        latest_bb_upper: Optional[float] = None
        latest_bb_lower: Optional[float] = None
        if n >= _MIN_BB:
            bb_vals = bollinger_bands(prices)
            latest_bb_upper = next((v for v in reversed(bb_vals["upper"]) if v is not None), None)
            latest_bb_lower = next((v for v in reversed(bb_vals["lower"]) if v is not None), None)

        price = prices[-1]

        # ── Signal generation ─────────────────────────────────────────────────
        bullish_signals: List[str] = []
        bearish_signals: List[str] = []

        if latest_rsi is not None:
            if latest_rsi < _RSI_OVERSOLD:
                bullish_signals.append("RSI_OVERSOLD")
            elif latest_rsi > _RSI_OVERBOUGHT:
                bearish_signals.append("RSI_OVERBOUGHT")

        if latest_hist is not None:
            if latest_hist > 0:
                bullish_signals.append("MACD_BULLISH")
            else:
                bearish_signals.append("MACD_BEARISH")

        if latest_bb_upper is not None and price > latest_bb_upper:
            bearish_signals.append("PRICE_ABOVE_BB_UPPER")
        if latest_bb_lower is not None and price < latest_bb_lower:
            bullish_signals.append("PRICE_BELOW_BB_LOWER")

        if latest_sma20 is not None and latest_sma50 is not None:
            if latest_sma20 > latest_sma50:
                bullish_signals.append("GOLDEN_CROSS")
            else:
                bearish_signals.append("DEATH_CROSS")

        all_signals = bullish_signals + bearish_signals

        # ── Action + confidence ───────────────────────────────────────────────
        # Require ≥2 confirming signals for a directional call
        action = "HOLD"
        confidence = 0.50 + 0.10 * len(all_signals)  # partial credit even on HOLD
        confidence = min(confidence, 0.90)

        if len(bullish_signals) >= 2 and len(bullish_signals) > len(bearish_signals):
            action = "BUY"
            confidence = min(0.50 + 0.10 * len(bullish_signals), 0.90)
        elif len(bearish_signals) >= 2 and len(bearish_signals) > len(bullish_signals):
            action = "SELL"
            confidence = min(0.50 + 0.10 * len(bearish_signals), 0.90)

        return {
            "rsi": round(latest_rsi, 2) if latest_rsi is not None else None,
            "macd": latest_macd,
            "macd_signal": latest_signal_line,
            "macd_histogram": latest_hist,
            "bb_upper": latest_bb_upper,
            "bb_lower": latest_bb_lower,
            "sma20": latest_sma20,
            "sma50": latest_sma50,
            "price": price,
            "signals": all_signals,
            "action": action,
            "confidence": round(confidence, 3),
        }

    except Exception:
        return {}
