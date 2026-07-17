"""
broker_gateway/interfaces/broker_adapter.py

Abstrakt broker-adapter-interface. Alle exchange-adaptere (Binance, Bybit,
paper) implementerer denne kontrakt. broker-gateway bruger udelukkende
denne ABC — ingen adapter-specifik kode lækker opad.

BYOK-model: API-nøgler injiceres via __init__; adapteren er ansvarlig for
at holde dem i hukommelse og aldrig logge dem.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ── Domæne-typer ──────────────────────────────────────────────────────────────

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class OrderIntent:
    """Ren order-intention, ingen broker-specifik logik."""
    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None       # None → market order
    stop_price: Optional[float] = None
    client_order_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_market(self) -> bool:
        return self.order_type == OrderType.MARKET

    def notional(self, current_price: float) -> float:
        """Estimeret USDT-notional."""
        p = self.price if self.price else current_price
        return self.quantity * p


@dataclass
class OrderReceipt:
    """Broker-kvittering for submitted ordre."""
    order_id: str
    exchange_order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    requested_quantity: float
    filled_quantity: float = 0.0
    average_price: float = 0.0
    commission: float = 0.0
    submitted_at: datetime = field(default_factory=datetime.utcnow)
    filled_at: Optional[datetime] = None
    raw_response: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.FAILED,
        )


@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float = 0.0
    side: OrderSide = OrderSide.BUY


@dataclass
class AccountBalance:
    currency: str
    available: float
    locked: float
    total: float


# ── Abstract Broker Adapter ────────────────────────────────────────────────────

class BrokerAdapter(ABC):
    """
    Abstrakt kontrakt for alle exchange-adaptere.

    Implementerings-krav:
    - API-nøgler må ALDRIG logges, serialiseres eller returneres i svar.
    - Alle ordre-kald skal returnere OrderReceipt (aldrig raise direkte til caller).
    - is_paper() = True → adapteren eksekverer aldrig rigtige ordrer.
    - health_check() skal svare inden 5 sekunder.
    """

    @property
    @abstractmethod
    def exchange_name(self) -> str:
        """Unikt navn: 'binance', 'bybit', 'paper', osv."""

    @abstractmethod
    def is_paper(self) -> bool:
        """True hvis adapteren er i paper/simulation-mode."""

    @abstractmethod
    def health_check(self) -> bool:
        """Returnér True hvis exchange-API er tilgængeligt."""

    @abstractmethod
    def get_balance(self) -> List[AccountBalance]:
        """Hent alle ikke-nul balancer for kontoen."""

    @abstractmethod
    def get_ticker_price(self, symbol: str) -> float:
        """Hent seneste pris for symbol (fx 'BTCUSDT')."""

    @abstractmethod
    def submit_order(self, intent: OrderIntent) -> OrderReceipt:
        """
        Submit en ordre. Returnér altid OrderReceipt — aldrig raise.
        Fejl skrives til receipt.status = FAILED + raw_response["error"].
        """

    @abstractmethod
    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        """Annullér åben ordre. Returnér True hvis annulleret."""

    @abstractmethod
    def get_order_status(self, symbol: str, exchange_order_id: str) -> OrderReceipt:
        """Slå ordrestatus op."""

    @abstractmethod
    def get_open_positions(self) -> List[Position]:
        """Hent alle åbne positioner."""

    @abstractmethod
    def get_whitelisted_symbols(self) -> List[str]:
        """
        Returnér per-kunde godkendte trading-par.
        Broker-gateway afviser ordrer på ikke-whitelistede symbols.
        """

    # ── Optional hooks med default no-op ──────────────────────────────────────

    def on_kill_switch(self) -> None:
        """Kaldt af global kill-switch. Annullér alle åbne ordrer."""

    def on_account_suspended(self) -> None:
        """Kaldt ved automatisk konto-pause (drawdown-tærskel m.m.)."""
