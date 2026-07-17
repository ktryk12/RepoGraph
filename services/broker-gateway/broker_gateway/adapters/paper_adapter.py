"""
broker_gateway/adapters/paper_adapter.py — Paper trading adapter.

Implementerer BrokerAdapter med simulerede fills. Ingen rigtige ordrer sendes.
Bruger en intern virtuel kasse (USDT) og registrerer positioner in-memory.

Env vars:
  PAPER_INITIAL_USDT : startkapital (default: 10000)
  PAPER_COMMISSION   : kommission per fill som decimal (default: 0.001 = 0.1%)
  PAPER_SLIPPAGE     : prisslippage som decimal (default: 0.0005 = 0.05%)
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from broker_gateway.interfaces.broker_adapter import (
    AccountBalance,
    BrokerAdapter,
    OrderIntent,
    OrderReceipt,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

_log = logging.getLogger("paper-adapter")

_INITIAL_USDT  = float(os.getenv("PAPER_INITIAL_USDT", "10000"))
_COMMISSION    = float(os.getenv("PAPER_COMMISSION", "0.001"))
_SLIPPAGE      = float(os.getenv("PAPER_SLIPPAGE", "0.0005"))

_DEFAULT_WHITELIST = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
]


class PaperAdapter(BrokerAdapter):
    """
    Simuleret broker-adapter til paper trading og backtesting.

    Fill-model:
    - MARKET: fill til current_price ± slippage
    - LIMIT: fill kun hvis limit_price er opnåeligt (altid i paper mode)
    - STOP_LOSS / TAKE_PROFIT: behandles som MARKET
    """

    def __init__(
        self,
        initial_usdt: float = _INITIAL_USDT,
        commission: float = _COMMISSION,
        slippage: float = _SLIPPAGE,
        whitelisted_symbols: Optional[List[str]] = None,
    ) -> None:
        self._usdt           = initial_usdt
        self._commission     = commission
        self._slippage       = slippage
        self._whitelist      = list(whitelisted_symbols or _DEFAULT_WHITELIST)
        self._positions: Dict[str, Position]     = {}
        self._orders:   Dict[str, OrderReceipt]  = {}
        self._price_overrides: Dict[str, float]  = {}

    @property
    def exchange_name(self) -> str:
        return "paper"

    def is_paper(self) -> bool:
        return True

    def health_check(self) -> bool:
        return True

    # ── Price ─────────────────────────────────────────────────────────────────

    def set_price(self, symbol: str, price: float) -> None:
        """Test-helper: override ticker price for a symbol."""
        self._price_overrides[symbol] = price

    def get_ticker_price(self, symbol: str) -> float:
        if symbol in self._price_overrides:
            return self._price_overrides[symbol]
        # Default stub prices for common pairs
        _DEFAULTS = {
            "BTCUSDT": 65000.0, "ETHUSDT": 3200.0, "SOLUSDT": 170.0,
            "BNBUSDT": 580.0,   "XRPUSDT": 0.52,   "ADAUSDT": 0.45,
        }
        return _DEFAULTS.get(symbol, 100.0)

    # ── Balance ───────────────────────────────────────────────────────────────

    def get_balance(self) -> List[AccountBalance]:
        balances = [
            AccountBalance(currency="USDT", available=self._usdt, locked=0.0, total=self._usdt)
        ]
        for sym, pos in self._positions.items():
            currency = sym.removesuffix("USDT").removesuffix("BTC")
            balances.append(AccountBalance(
                currency=currency,
                available=pos.quantity,
                locked=0.0,
                total=pos.quantity,
            ))
        return balances

    # ── Orders ────────────────────────────────────────────────────────────────

    def submit_order(self, intent: OrderIntent) -> OrderReceipt:
        now = datetime.now(timezone.utc)
        exchange_oid = f"paper-{uuid.uuid4().hex[:12]}"

        if intent.symbol not in self._whitelist:
            _log.warning("paper_adapter_rejected symbol=%s not_in_whitelist", intent.symbol)
            receipt = OrderReceipt(
                order_id=intent.order_id,
                exchange_order_id=exchange_oid,
                symbol=intent.symbol,
                side=intent.side,
                order_type=intent.order_type,
                status=OrderStatus.REJECTED,
                requested_quantity=intent.quantity,
                submitted_at=now,
                raw_response={"error": "symbol_not_whitelisted"},
            )
            self._orders[intent.order_id] = receipt
            return receipt

        price = self._fill_price(intent)
        commission_cost = intent.quantity * price * self._commission

        if intent.side == OrderSide.BUY:
            total_cost = intent.quantity * price + commission_cost
            if total_cost > self._usdt:
                _log.warning("paper_adapter_rejected order_id=%s insufficient_usdt=%.2f needed=%.2f",
                             intent.order_id, self._usdt, total_cost)
                receipt = OrderReceipt(
                    order_id=intent.order_id,
                    exchange_order_id=exchange_oid,
                    symbol=intent.symbol,
                    side=intent.side,
                    order_type=intent.order_type,
                    status=OrderStatus.REJECTED,
                    requested_quantity=intent.quantity,
                    submitted_at=now,
                    raw_response={"error": "insufficient_balance", "available_usdt": self._usdt},
                )
                self._orders[intent.order_id] = receipt
                return receipt
            self._usdt -= total_cost
            pos = self._positions.get(intent.symbol)
            if pos:
                new_qty = pos.quantity + intent.quantity
                new_entry = (pos.entry_price * pos.quantity + price * intent.quantity) / new_qty
                self._positions[intent.symbol] = Position(
                    symbol=intent.symbol, quantity=new_qty,
                    entry_price=new_entry, current_price=price, side=OrderSide.BUY,
                )
            else:
                self._positions[intent.symbol] = Position(
                    symbol=intent.symbol, quantity=intent.quantity,
                    entry_price=price, current_price=price, side=OrderSide.BUY,
                )

        else:  # SELL
            pos = self._positions.get(intent.symbol)
            available_qty = pos.quantity if pos else 0.0
            sell_qty = min(intent.quantity, available_qty)
            if sell_qty <= 0:
                receipt = OrderReceipt(
                    order_id=intent.order_id,
                    exchange_order_id=exchange_oid,
                    symbol=intent.symbol,
                    side=intent.side,
                    order_type=intent.order_type,
                    status=OrderStatus.REJECTED,
                    requested_quantity=intent.quantity,
                    submitted_at=now,
                    raw_response={"error": "no_position_to_sell"},
                )
                self._orders[intent.order_id] = receipt
                return receipt
            proceeds = sell_qty * price - commission_cost
            self._usdt += proceeds
            remaining = available_qty - sell_qty
            if remaining > 0.0:
                self._positions[intent.symbol] = Position(
                    symbol=intent.symbol, quantity=remaining,
                    entry_price=pos.entry_price, current_price=price, side=OrderSide.BUY,
                )
            else:
                self._positions.pop(intent.symbol, None)

        receipt = OrderReceipt(
            order_id=intent.order_id,
            exchange_order_id=exchange_oid,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            status=OrderStatus.FILLED,
            requested_quantity=intent.quantity,
            filled_quantity=intent.quantity,
            average_price=price,
            commission=commission_cost,
            submitted_at=now,
            filled_at=now,
        )
        self._orders[intent.order_id] = receipt
        _log.info("paper_adapter_fill order_id=%s symbol=%s side=%s qty=%.6f price=%.4f",
                  intent.order_id, intent.symbol, intent.side.value, intent.quantity, price)
        return receipt

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        for receipt in self._orders.values():
            if receipt.exchange_order_id == exchange_order_id:
                receipt.status = OrderStatus.CANCELLED
                return True
        return False

    def get_order_status(self, symbol: str, exchange_order_id: str) -> OrderReceipt:
        for receipt in self._orders.values():
            if receipt.exchange_order_id == exchange_order_id:
                return receipt
        return OrderReceipt(
            order_id="unknown", exchange_order_id=exchange_order_id,
            symbol=symbol, side=OrderSide.BUY, order_type=OrderType.MARKET,
            status=OrderStatus.FAILED, requested_quantity=0.0,
            raw_response={"error": "not_found"},
        )

    def get_open_positions(self) -> List[Position]:
        price_updated = []
        for sym, pos in self._positions.items():
            current = self.get_ticker_price(sym)
            price_updated.append(Position(
                symbol=sym,
                quantity=pos.quantity,
                entry_price=pos.entry_price,
                current_price=current,
                unrealized_pnl=(current - pos.entry_price) * pos.quantity,
                side=pos.side,
            ))
        return price_updated

    def get_whitelisted_symbols(self) -> List[str]:
        return list(self._whitelist)

    def on_kill_switch(self) -> None:
        _log.info("paper_adapter_kill_switch clearing %d positions", len(self._positions))
        self._positions.clear()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _fill_price(self, intent: OrderIntent) -> float:
        base = self.get_ticker_price(intent.symbol)
        if intent.order_type == OrderType.LIMIT and intent.price:
            base = intent.price
        slip = base * self._slippage
        if intent.side == OrderSide.BUY:
            return base + slip
        return base - slip
