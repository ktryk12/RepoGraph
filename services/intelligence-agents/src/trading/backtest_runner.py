"""
babyai/trading/backtest_runner.py — Historisk backtest.

Kører en StrategyBase mod historiske OHLCV-data og producerer
en BacktestReport med Sharpe, max drawdown, win rate, P&L-kurve.

Data-kilde: OpenBB via services/openbb-server (eller lokal CSV-fallback).

Usage:
    runner = BacktestRunner(strategy, initial_capital=10_000)
    report = runner.run(candles_by_symbol)
    print(report.summary())
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from babyai.trading.strategy_base import (
    Candle, SignalDirection, StrategyBase, StrategyConfig,
)
from babyai.trading.position_sizer import PositionSizer, SizingMethod


@dataclass
class BacktestTrade:
    symbol:       str
    direction:    str
    entry_price:  float
    exit_price:   float
    quantity:     float
    entry_time:   datetime
    exit_time:    datetime
    pnl:          float
    pnl_pct:      float
    strategy_id:  str
    rationale:    str = ""


@dataclass
class BacktestReport:
    strategy_id:      str
    initial_capital:  float
    final_capital:    float
    total_trades:     int
    winning_trades:   int
    losing_trades:    int
    gross_pnl:        float
    net_pnl:          float
    total_commission: float
    win_rate:         float
    sharpe_ratio:     float
    max_drawdown_pct: float
    max_drawdown_usdt: float
    largest_win:      float
    largest_loss:     float
    avg_trade_pnl:    float
    equity_curve:     List[float] = field(default_factory=list)
    trades:           List[BacktestTrade] = field(default_factory=list)
    period_start:     Optional[datetime] = None
    period_end:       Optional[datetime] = None

    def summary(self) -> Dict[str, Any]:
        return {
            "strategy_id":       self.strategy_id,
            "period":            f"{self.period_start} → {self.period_end}",
            "initial_capital":   round(self.initial_capital, 2),
            "final_capital":     round(self.final_capital, 2),
            "net_pnl":           round(self.net_pnl, 4),
            "net_pnl_pct":       round(self.net_pnl / self.initial_capital * 100, 2),
            "total_trades":      self.total_trades,
            "win_rate":          round(self.win_rate, 4),
            "sharpe_ratio":      round(self.sharpe_ratio, 4),
            "max_drawdown_pct":  round(self.max_drawdown_pct, 4),
            "largest_win":       round(self.largest_win, 4),
            "largest_loss":      round(self.largest_loss, 4),
            "avg_trade_pnl":     round(self.avg_trade_pnl, 4),
        }


class BacktestRunner:
    """
    Event-driven backtest. Kører én candle ad gangen.

    Parameters
    ----------
    strategy       : StrategyBase
    initial_capital : float
    commission_pct  : float (default 0.001 = 0.1%)
    slippage_pct    : float (default 0.0005)
    sizing_method   : SizingMethod
    """

    def __init__(
        self,
        strategy: StrategyBase,
        initial_capital: float = 10_000.0,
        commission_pct: float = 0.001,
        slippage_pct: float = 0.0005,
        sizing_method: SizingMethod = SizingMethod.FIXED_FRACTIONAL,
    ) -> None:
        self._strategy      = strategy
        self._initial_cap   = initial_capital
        self._commission    = commission_pct
        self._slippage      = slippage_pct
        self._sizing_method = sizing_method
        self._sizer = PositionSizer(
            max_risk_pct=strategy.config.max_risk_pct,
            max_position_pct=0.10,
        )

    def run(self, candles_by_symbol: Dict[str, List[Candle]]) -> BacktestReport:
        capital    = self._initial_cap
        positions: Dict[str, Dict] = {}
        trades:    List[BacktestTrade] = []
        equity:    List[float] = [capital]
        commission_total = 0.0

        # Align all symbols to same length
        min_len = min(len(v) for v in candles_by_symbol.values()) if candles_by_symbol else 0
        aligned = {s: bars[-min_len:] for s, bars in candles_by_symbol.items()}

        period_start = None
        period_end   = None

        for i in range(1, min_len):
            window = {s: bars[:i + 1] for s, bars in aligned.items()}
            signals = self._strategy.generate_signals(window)

            for sig in signals:
                if not sig.is_actionable:
                    continue
                symbol = sig.symbol
                price  = aligned[symbol][i].close
                ts     = aligned[symbol][i].timestamp
                if period_start is None:
                    period_start = ts
                period_end = ts

                if sig.direction == SignalDirection.BUY and symbol not in positions:
                    result = self._sizer.size(
                        method=self._sizing_method,
                        capital_usdt=capital,
                        price=price,
                        stop_loss_pct=self._strategy.config.stop_loss_pct,
                    )
                    qty = result.quantity
                    if qty <= 0:
                        continue
                    fill_price   = price * (1 + self._slippage)
                    cost         = qty * fill_price
                    commission   = cost * self._commission
                    if cost + commission > capital:
                        continue
                    capital     -= cost + commission
                    commission_total += commission
                    positions[symbol] = {
                        "qty": qty, "entry": fill_price, "ts": ts,
                        "sl": sig.stop_loss_price, "tp": sig.take_profit_price,
                        "rationale": sig.rationale,
                    }

                elif sig.direction == SignalDirection.SELL and symbol in positions:
                    pos        = positions.pop(symbol)
                    fill_price = price * (1 - self._slippage)
                    proceeds   = pos["qty"] * fill_price
                    commission = proceeds * self._commission
                    capital   += proceeds - commission
                    commission_total += commission
                    pnl      = (fill_price - pos["entry"]) * pos["qty"] - commission
                    pnl_pct  = (fill_price - pos["entry"]) / pos["entry"]
                    trades.append(BacktestTrade(
                        symbol=symbol, direction="BUY→SELL",
                        entry_price=pos["entry"], exit_price=fill_price,
                        quantity=pos["qty"], entry_time=pos["ts"], exit_time=ts,
                        pnl=pnl, pnl_pct=pnl_pct,
                        strategy_id=self._strategy.strategy_id,
                        rationale=pos["rationale"],
                    ))

            # Check SL/TP for open positions
            for symbol, pos in list(positions.items()):
                bar   = aligned[symbol][i]
                close = bar.close
                sl, tp = pos.get("sl"), pos.get("tp")
                exit_reason = None
                if sl and close <= sl:
                    exit_reason = "stop_loss"
                    exit_price  = sl * (1 - self._slippage)
                elif tp and close >= tp:
                    exit_reason = "take_profit"
                    exit_price  = tp * (1 - self._slippage)
                if exit_reason:
                    proceeds   = pos["qty"] * exit_price
                    commission = proceeds * self._commission
                    capital   += proceeds - commission
                    commission_total += commission
                    pnl      = (exit_price - pos["entry"]) * pos["qty"] - commission
                    trades.append(BacktestTrade(
                        symbol=symbol, direction=exit_reason,
                        entry_price=pos["entry"], exit_price=exit_price,
                        quantity=pos["qty"], entry_time=pos["ts"],
                        exit_time=bar.timestamp, pnl=pnl,
                        pnl_pct=(exit_price - pos["entry"]) / pos["entry"],
                        strategy_id=self._strategy.strategy_id,
                    ))
                    del positions[symbol]

            equity.append(capital + sum(
                aligned[s][i].close * p["qty"] for s, p in positions.items()
                if i < len(aligned[s])
            ))

        # Close remaining positions at last price
        for symbol, pos in positions.items():
            last = aligned[symbol][-1]
            pnl  = (last.close - pos["entry"]) * pos["qty"]
            capital += pos["qty"] * last.close
            trades.append(BacktestTrade(
                symbol=symbol, direction="open_at_end",
                entry_price=pos["entry"], exit_price=last.close,
                quantity=pos["qty"], entry_time=pos["ts"],
                exit_time=last.timestamp, pnl=pnl,
                pnl_pct=(last.close - pos["entry"]) / pos["entry"],
                strategy_id=self._strategy.strategy_id,
            ))

        wins   = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        total  = len(trades)
        gross  = sum(t.pnl for t in trades)
        net    = capital - self._initial_cap

        # Sharpe (annualised, assuming daily equity points)
        returns = [
            (equity[i] - equity[i - 1]) / equity[i - 1]
            for i in range(1, len(equity)) if equity[i - 1] > 0
        ]
        sharpe = 0.0
        if len(returns) > 1:
            import statistics as _st
            avg_r = _st.mean(returns)
            std_r = _st.stdev(returns) or 1e-9
            sharpe = (avg_r / std_r) * math.sqrt(252)

        # Max drawdown
        peak = self._initial_cap
        max_dd_usdt = 0.0
        for e in equity:
            if e > peak:
                peak = e
            dd = peak - e
            if dd > max_dd_usdt:
                max_dd_usdt = dd
        max_dd_pct = max_dd_usdt / self._initial_cap if self._initial_cap else 0.0

        return BacktestReport(
            strategy_id=self._strategy.strategy_id,
            initial_capital=self._initial_cap,
            final_capital=capital,
            total_trades=total,
            winning_trades=len(wins),
            losing_trades=len(losses),
            gross_pnl=gross,
            net_pnl=net,
            total_commission=commission_total,
            win_rate=len(wins) / total if total else 0.0,
            sharpe_ratio=sharpe,
            max_drawdown_pct=max_dd_pct,
            max_drawdown_usdt=max_dd_usdt,
            largest_win=max((t.pnl for t in wins), default=0.0),
            largest_loss=min((t.pnl for t in losses), default=0.0),
            avg_trade_pnl=gross / total if total else 0.0,
            equity_curve=equity,
            trades=trades,
            period_start=period_start,
            period_end=period_end,
        )
