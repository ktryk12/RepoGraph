"""
BacktestEngine — walk-forward backtesting for TradingAgent signals.
Paper trading only — no real orders.

Walk-forward protocol:
  - Warmup period: first 30 rows are observation-only (no trades).
  - On each day N, the engine sees ONLY data[0:N+1] (no lookahead).
  - Entry: close price of signal day (simplified — no next-open slippage).
  - Exit triggers (checked on each subsequent day):
      stop-loss:   close < entry * (1 - stop_loss_pct)   → exit immediately
      take-profit: close > entry * (1 + take_profit_pct)  → exit immediately
      opposing signal: BUY open position + SELL signal    → exit
      end of data: close any remaining open position at last close
  - Only one open position per symbol at a time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

_log = logging.getLogger(__name__)

_WARMUP_DAYS = 30
_STOP_LOSS_PCT = 0.02    # 2% stop loss
_TAKE_PROFIT_PCT = 0.04  # 4% take profit
_POSITION_PCT = 0.05     # 5% of capital per trade


@dataclass
class BacktestResult:
    symbol: str
    period_days: int
    initial_capital: float
    final_capital: float
    total_return_pct: float
    num_trades: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trades: List[Dict[str, Any]] = field(default_factory=list)
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.symbol}: return={self.total_return_pct:+.1f}% "
            f"trades={self.num_trades} win={self.win_rate:.0%} "
            f"dd={self.max_drawdown_pct:.1f}% sharpe={self.sharpe_ratio:.2f}"
        )


class BacktestEngine:
    """
    Walk-forward backtester. Paper trading only.

    Usage:
        engine = BacktestEngine(initial_capital=10_000.0)
        result = engine.run(symbol='BTC', ohlcv_df=df, analyze_fn=analyze)
    """

    def __init__(
        self,
        initial_capital: float = 10_000.0,
        stop_loss_pct: float = _STOP_LOSS_PCT,
        take_profit_pct: float = _TAKE_PROFIT_PCT,
        position_pct: float = _POSITION_PCT,
        warmup_days: int = _WARMUP_DAYS,
    ) -> None:
        self.initial_capital = initial_capital
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.position_pct = position_pct
        self.warmup_days = warmup_days

        # State (reset on each run)
        self.capital: float = initial_capital
        self._position: Optional[Dict[str, Any]] = None  # current open position
        self._trades: List[Dict[str, Any]] = []
        self._equity_curve: List[Dict[str, Any]] = []

    def run(
        self,
        symbol: str,
        ohlcv_df: pd.DataFrame,
        analyze_fn: Callable[[Any], Dict[str, Any]],
    ) -> BacktestResult:
        """
        Run walk-forward backtest. analyze_fn is called with a slice of ohlcv_df.
        Returns BacktestResult — never raises.
        """
        self.capital = self.initial_capital
        self._position = None
        self._trades = []
        self._equity_curve = []

        if not isinstance(ohlcv_df, pd.DataFrame) or len(ohlcv_df) < self.warmup_days + 5:
            _log.warning("backtest_insufficient_data symbol=%s rows=%d", symbol, len(ohlcv_df) if hasattr(ohlcv_df, '__len__') else 0)
            return self._empty_result(symbol)

        rows = ohlcv_df.reset_index(drop=True)
        n = len(rows)

        for i in range(self.warmup_days, n):
            # Walk-forward slice: only rows 0..i (inclusive) visible
            window = rows.iloc[: i + 1]
            close = float(rows["close"].iloc[i])
            date_val = rows["timestamp"].iloc[i] if "timestamp" in rows.columns else str(i)

            # ── Check exit conditions on open position ────────────────────────
            if self._position is not None:
                entry_price = self._position["entry_price"]
                side = self._position["side"]  # "long" or "short"

                if side == "long":
                    pnl_pct = (close - entry_price) / entry_price
                    exit_reason = None
                    if pnl_pct <= -self.stop_loss_pct:
                        exit_reason = "stop_loss"
                    elif pnl_pct >= self.take_profit_pct:
                        exit_reason = "take_profit"

                    if exit_reason:
                        self._close_position(close, date_val, exit_reason)

                elif side == "short":
                    pnl_pct = (entry_price - close) / entry_price
                    exit_reason = None
                    if pnl_pct <= -self.stop_loss_pct:
                        exit_reason = "stop_loss"
                    elif pnl_pct >= self.take_profit_pct:
                        exit_reason = "take_profit"

                    if exit_reason:
                        self._close_position(close, date_val, exit_reason)

            # ── Get signal ────────────────────────────────────────────────────
            try:
                signals = analyze_fn(window)
            except Exception:
                signals = {}

            action = str(signals.get("action", "HOLD"))
            confidence = float(signals.get("confidence", 0.0))

            # ── Execute signal ────────────────────────────────────────────────
            if action == "BUY" and self._position is None:
                self._open_position("long", close, date_val, confidence)
            elif action == "SELL" and self._position is None:
                self._open_position("short", close, date_val, confidence)
            elif action == "SELL" and self._position is not None and self._position["side"] == "long":
                self._close_position(close, date_val, "opposing_signal")
            elif action == "BUY" and self._position is not None and self._position["side"] == "short":
                self._close_position(close, date_val, "opposing_signal")

            # ── Record equity ─────────────────────────────────────────────────
            equity = self._current_equity(close)
            self._equity_curve.append({"date": str(date_val)[:10], "equity": equity})

        # Close any remaining open position at last price
        if self._position is not None and n > 0:
            last_close = float(rows["close"].iloc[-1])
            last_date = rows["timestamp"].iloc[-1] if "timestamp" in rows.columns else str(n - 1)
            self._close_position(last_close, last_date, "end_of_data")

        return self._build_result(symbol, len(rows))

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _open_position(self, side: str, price: float, date: Any, confidence: float) -> None:
        size_capital = self.capital * self.position_pct
        units = size_capital / price if price > 0 else 0
        self._position = {
            "side": side,
            "entry_price": price,
            "entry_date": str(date)[:10],
            "units": units,
            "size_capital": size_capital,
            "confidence": confidence,
        }

    def _close_position(self, exit_price: float, date: Any, reason: str) -> None:
        if self._position is None:
            return
        pos = self._position
        units = pos["units"]
        entry = pos["entry_price"]
        side = pos["side"]

        if side == "long":
            pnl = (exit_price - entry) * units
        else:  # short
            pnl = (entry - exit_price) * units

        self.capital += pnl
        pnl_pct = pnl / pos["size_capital"] if pos["size_capital"] > 0 else 0.0

        self._trades.append({
            "side": side,
            "entry_price": entry,
            "exit_price": exit_price,
            "entry_date": pos["entry_date"],
            "exit_date": str(date)[:10],
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct * 100, 2),
            "reason": reason,
        })
        self._position = None

    def _current_equity(self, current_price: float) -> float:
        if self._position is None:
            return self.capital
        pos = self._position
        if pos["side"] == "long":
            unrealized = (current_price - pos["entry_price"]) * pos["units"]
        else:
            unrealized = (pos["entry_price"] - current_price) * pos["units"]
        return self.capital + unrealized

    def _empty_result(self, symbol: str) -> BacktestResult:
        return BacktestResult(
            symbol=symbol,
            period_days=0,
            initial_capital=self.initial_capital,
            final_capital=self.initial_capital,
            total_return_pct=0.0,
            num_trades=0,
            win_rate=0.0,
            avg_win_pct=0.0,
            avg_loss_pct=0.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
        )

    def _build_result(self, symbol: str, period_days: int) -> BacktestResult:
        final_cap = self.capital
        total_return_pct = (final_cap - self.initial_capital) / self.initial_capital * 100

        wins = [t for t in self._trades if t["pnl"] > 0]
        losses = [t for t in self._trades if t["pnl"] <= 0]
        num_trades = len(self._trades)
        win_rate = len(wins) / num_trades if num_trades > 0 else 0.0
        avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0.0
        avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0.0

        # Max drawdown from equity curve
        equity_vals = [e["equity"] for e in self._equity_curve]
        max_dd = _max_drawdown(equity_vals)

        # Sharpe ratio from daily equity returns
        sharpe = _sharpe_ratio(equity_vals)

        return BacktestResult(
            symbol=symbol,
            period_days=period_days,
            initial_capital=self.initial_capital,
            final_capital=round(final_cap, 2),
            total_return_pct=round(total_return_pct, 2),
            num_trades=num_trades,
            win_rate=round(win_rate, 4),
            avg_win_pct=round(avg_win, 2),
            avg_loss_pct=round(avg_loss, 2),
            max_drawdown_pct=round(max_dd * 100, 2),
            sharpe_ratio=round(sharpe, 3) if sharpe is not None else 0.0,
            trades=list(self._trades),
            equity_curve=list(self._equity_curve),
        )


# ── Statistics helpers ────────────────────────────────────────────────────────

def _max_drawdown(equity: List[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = (peak - val) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _sharpe_ratio(equity: List[float], risk_free: float = 0.0) -> Optional[float]:
    if len(equity) < 3:
        return None
    returns = [(equity[i] - equity[i - 1]) / equity[i - 1] for i in range(1, len(equity)) if equity[i - 1] > 0]
    if len(returns) < 2:
        return None
    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = variance ** 0.5
    if std < 1e-12:
        return None
    excess = mean - risk_free
    return (excess / std) * (252 ** 0.5)  # annualized
