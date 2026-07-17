"""
BinanceClientWrapper — paper/live order execution with circuit breakers.

PAPER mode (default):
  All order calls are logged and return a mock response.
  No requests are sent to Binance.

LIVE mode:
  Requires BOTH env vars:
    TRADING_MODE=LIVE
    TRADING_LIVE_CONFIRMED=YES
  Any missing or incorrect value keeps the system in PAPER mode.

Circuit breakers (enforced in BOTH modes):
  MAX_TOTAL_EXPOSURE_USDT (default $500)
  MAX_ORDER_USDT          (default $50)
  MAX_DAILY_LOSS_PCT      (default 3.0%)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

_log = logging.getLogger(__name__)


class BinanceClientWrapper:
    PAPER = "PAPER"
    LIVE = "LIVE"

    def __init__(self) -> None:
        self.api_key = os.getenv("BINANCE_API_KEY", "")
        self.api_secret = os.getenv("BINANCE_SECRET_KEY", "")

        # Mode determination — LIVE requires two independent env vars
        env_mode = os.getenv("TRADING_MODE", "PAPER").upper().strip()
        confirmed = os.getenv("TRADING_LIVE_CONFIRMED", "").upper().strip()

        if env_mode == "LIVE" and confirmed == "YES":
            if not self.api_key or not self.api_secret:
                raise EnvironmentError(
                    "LIVE mode requires BINANCE_API_KEY and BINANCE_SECRET_KEY"
                )
            self.mode = self.LIVE
        else:
            self.mode = self.PAPER
            if env_mode == "LIVE" and confirmed != "YES":
                _log.warning(
                    "TRADING_MODE=LIVE set but TRADING_LIVE_CONFIRMED!=YES — "
                    "falling back to PAPER mode"
                )

        # Capital limits (configurable via env)
        self.max_total_exposure_usdt = float(os.getenv("MAX_TOTAL_EXPOSURE_USDT", "500"))
        self.max_order_usdt = float(os.getenv("MAX_ORDER_USDT", "50"))
        self.max_daily_loss_pct = float(os.getenv("MAX_DAILY_LOSS_PCT", "3.0"))

        # Runtime state
        self.daily_pnl_usdt: float = 0.0
        self.total_exposure_usdt: float = 0.0

        # Lazy-initialize real client only in LIVE mode
        self._client: Any = None
        if self.mode == self.LIVE:
            self._client = self._build_client()

        _log.info(
            "binance_client_init mode=%s max_exposure=%.0f max_order=%.0f",
            self.mode, self.max_total_exposure_usdt, self.max_order_usdt,
        )

    def _build_client(self) -> Any:
        try:
            from binance import Client
            return Client(self.api_key, self.api_secret)
        except Exception as exc:
            raise RuntimeError(f"Failed to create Binance client: {exc}") from exc

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account_balance(self) -> Dict[str, Any]:
        """Return USDT balance. PAPER returns a fixed mock balance."""
        if self.mode == self.PAPER:
            return {"USDT": 500.0, "mode": "PAPER"}
        try:
            info = self._client.get_account()
            balances = {
                b["asset"]: float(b["free"])
                for b in info["balances"]
                if float(b["free"]) > 0
            }
            balances["mode"] = "LIVE"
            return balances
        except Exception as exc:
            _log.warning("get_balance_failed error=%s", exc)
            return {}

    def get_price(self, symbol: str) -> Optional[float]:
        """Fetch current best bid price. Returns None on failure."""
        if self.mode == self.PAPER:
            # Return None — caller should use their own price estimate
            return None
        try:
            ticker = self._client.get_symbol_ticker(symbol=symbol)
            return float(ticker["price"])
        except Exception as exc:
            _log.warning("get_price_failed symbol=%s error=%s", symbol, exc)
            return None

    # ── Orders ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        current_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Place BUY or SELL order.
        PAPER: logs and returns mock order.
        LIVE: places real limit order on Binance.

        circuit_breaker checks run in BOTH modes.
        Raises ValueError if any circuit breaker fires.
        """
        ref_price = price or current_price or 1.0
        order_value = quantity * ref_price

        self._check_circuit_breakers(order_value, side)

        if self.mode == self.PAPER:
            order_id = f"PAPER-{symbol}-{side}-{int(ref_price)}"
            _log.info(
                "[PAPER] %s %s %.6f @ %.4f USDT (value=%.2f)",
                side, symbol, quantity, ref_price, order_value,
            )
            return {
                "orderId": order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": ref_price,
                "status": "PAPER_FILLED",
                "mode": "PAPER",
            }

        # LIVE
        try:
            from binance import (
                SIDE_BUY, SIDE_SELL,
                ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET,
                TIME_IN_FORCE_GTC,
            )
            binance_side = SIDE_BUY if side == "BUY" else SIDE_SELL
            if price:
                order = self._client.create_order(
                    symbol=symbol,
                    side=binance_side,
                    type=ORDER_TYPE_LIMIT,
                    timeInForce=TIME_IN_FORCE_GTC,
                    quantity=round(quantity, 6),
                    price=str(round(price, 2)),
                )
            else:
                order = self._client.create_order(
                    symbol=symbol,
                    side=binance_side,
                    type=ORDER_TYPE_MARKET,
                    quantity=round(quantity, 6),
                )
            self.total_exposure_usdt += order_value
            _log.info(
                "[LIVE] %s %s qty=%.6f price=%s orderId=%s",
                side, symbol, quantity, price, order.get("orderId"),
            )
            return order
        except Exception as exc:
            _log.error("place_order_failed symbol=%s side=%s error=%s", symbol, side, exc)
            raise

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        if self.mode == self.PAPER:
            return {"status": "PAPER_CANCELLED", "orderId": order_id}
        try:
            return self._client.cancel_order(symbol=symbol, orderId=order_id)
        except Exception as exc:
            _log.warning("cancel_order_failed symbol=%s order_id=%s error=%s", symbol, order_id, exc)
            return {}

    def record_pnl(self, pnl_usdt: float) -> None:
        """Update daily P&L tracker. Called after position close."""
        self.daily_pnl_usdt += pnl_usdt
        if pnl_usdt < 0:
            self.total_exposure_usdt = max(0.0, self.total_exposure_usdt + pnl_usdt)

    def reset_daily(self) -> None:
        """Reset daily counters. Call at midnight."""
        self.daily_pnl_usdt = 0.0

    # ── Circuit breakers ──────────────────────────────────────────────────────

    def _check_circuit_breakers(self, order_value: float, side: str) -> None:
        if order_value > self.max_order_usdt:
            raise ValueError(
                f"Order value {order_value:.2f} USDT exceeds "
                f"MAX_ORDER_USDT={self.max_order_usdt}"
            )
        if side == "BUY" and self.total_exposure_usdt + order_value > self.max_total_exposure_usdt:
            raise ValueError(
                f"Total exposure {self.total_exposure_usdt + order_value:.2f} USDT would exceed "
                f"MAX_TOTAL_EXPOSURE_USDT={self.max_total_exposure_usdt}"
            )
        if self.max_total_exposure_usdt > 0:
            daily_loss_pct = abs(min(0.0, self.daily_pnl_usdt)) / self.max_total_exposure_usdt * 100
            if daily_loss_pct >= self.max_daily_loss_pct:
                raise ValueError(
                    f"Daily loss {daily_loss_pct:.1f}% has reached limit "
                    f"MAX_DAILY_LOSS_PCT={self.max_daily_loss_pct}% — "
                    "trading halted for today"
                )
