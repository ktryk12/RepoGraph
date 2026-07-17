"""
broker_gateway/adapters/binance_adapter.py — Binance Spot adapter.

Implementerer BrokerAdapter mod Binance REST API v3.
API-nøgler injiceres; aldrig logget.

Env vars (bruges KUN hvis adapteren instansieres uden eksplicitte nøgler):
  BINANCE_API_KEY
  BINANCE_API_SECRET
  BINANCE_TESTNET : "1" → brug testnet endpoint
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
import json

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

_log = logging.getLogger("binance-adapter")

_BASE_URL      = "https://testnet.binance.vision/api" if os.getenv("BINANCE_TESTNET") == "1" \
                 else "https://api.binance.com/api"
_RECV_WINDOW   = 5000


class BinanceAdapter(BrokerAdapter):
    """
    Binance Spot REST adapter.

    Alle kald er synkrone (urllib — ingen tredjeparts-afhængighed udover stdlib).
    Fejl fanger catch-all og returnerer FAILED OrderReceipt — aldrig raise.
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        self._api_key    = api_key
        self._api_secret = api_secret

    @property
    def exchange_name(self) -> str:
        return "binance"

    def is_paper(self) -> bool:
        return False

    def health_check(self) -> bool:
        try:
            self._get("/v3/ping")
            return True
        except Exception:
            return False

    def get_ticker_price(self, symbol: str) -> float:
        try:
            data = self._get("/v3/ticker/price", {"symbol": symbol})
            return float(data["price"])
        except Exception as exc:
            _log.error("binance_get_price_error symbol=%s error=%s", symbol, exc)
            return 0.0

    def get_balance(self) -> List[AccountBalance]:
        try:
            data = self._signed_get("/v3/account")
            balances = []
            for b in data.get("balances", []):
                free  = float(b["free"])
                locked = float(b["locked"])
                if free + locked > 0:
                    balances.append(AccountBalance(
                        currency=b["asset"],
                        available=free,
                        locked=locked,
                        total=free + locked,
                    ))
            return balances
        except Exception as exc:
            _log.error("binance_get_balance_error error=%s", exc)
            return []

    def submit_order(self, intent: OrderIntent) -> OrderReceipt:
        now = datetime.now(timezone.utc)
        try:
            params: Dict[str, Any] = {
                "symbol":           intent.symbol,
                "side":             intent.side.value,
                "type":             self._map_order_type(intent.order_type),
                "quantity":         f"{intent.quantity:.8f}",
                "newClientOrderId": intent.client_order_id or intent.order_id[:36],
                "recvWindow":       _RECV_WINDOW,
            }
            if intent.order_type == OrderType.LIMIT:
                if not intent.price:
                    raise ValueError("LIMIT order requires price")
                params["price"]       = f"{intent.price:.8f}"
                params["timeInForce"] = "GTC"
            if intent.order_type == OrderType.STOP_LOSS and intent.stop_price:
                params["stopPrice"] = f"{intent.stop_price:.8f}"

            data = self._signed_post("/v3/order", params)
            status = self._map_status(data.get("status", "NEW"))
            return OrderReceipt(
                order_id=intent.order_id,
                exchange_order_id=str(data.get("orderId", "")),
                symbol=intent.symbol,
                side=intent.side,
                order_type=intent.order_type,
                status=status,
                requested_quantity=intent.quantity,
                filled_quantity=float(data.get("executedQty", 0)),
                average_price=float(data.get("fills", [{}])[0].get("price", 0)) if data.get("fills") else 0.0,
                commission=sum(float(f.get("commission", 0)) for f in data.get("fills", [])),
                submitted_at=now,
                filled_at=now if status == OrderStatus.FILLED else None,
                raw_response=data,
            )
        except Exception as exc:
            _log.error("binance_submit_order_error order_id=%s error=%s", intent.order_id, exc)
            return OrderReceipt(
                order_id=intent.order_id,
                exchange_order_id="",
                symbol=intent.symbol,
                side=intent.side,
                order_type=intent.order_type,
                status=OrderStatus.FAILED,
                requested_quantity=intent.quantity,
                submitted_at=now,
                raw_response={"error": str(exc)},
            )

    def cancel_order(self, symbol: str, exchange_order_id: str) -> bool:
        try:
            self._signed_delete("/v3/order", {
                "symbol": symbol, "orderId": exchange_order_id, "recvWindow": _RECV_WINDOW,
            })
            return True
        except Exception as exc:
            _log.error("binance_cancel_error symbol=%s oid=%s error=%s", symbol, exchange_order_id, exc)
            return False

    def get_order_status(self, symbol: str, exchange_order_id: str) -> OrderReceipt:
        try:
            data = self._signed_get("/v3/order", {
                "symbol": symbol, "orderId": exchange_order_id, "recvWindow": _RECV_WINDOW,
            })
            return OrderReceipt(
                order_id=str(data.get("clientOrderId", exchange_order_id)),
                exchange_order_id=exchange_order_id,
                symbol=symbol,
                side=OrderSide(data["side"]),
                order_type=OrderType.MARKET,
                status=self._map_status(data.get("status", "NEW")),
                requested_quantity=float(data.get("origQty", 0)),
                filled_quantity=float(data.get("executedQty", 0)),
                average_price=float(data.get("price", 0)),
                raw_response=data,
            )
        except Exception as exc:
            return OrderReceipt(
                order_id="unknown", exchange_order_id=exchange_order_id,
                symbol=symbol, side=OrderSide.BUY, order_type=OrderType.MARKET,
                status=OrderStatus.FAILED, requested_quantity=0.0,
                raw_response={"error": str(exc)},
            )

    def get_open_positions(self) -> List[Position]:
        # Spot: positions = non-zero balances with a known pair
        balances = self.get_balance()
        positions = []
        for bal in balances:
            if bal.currency == "USDT":
                continue
            symbol = f"{bal.currency}USDT"
            price = self.get_ticker_price(symbol)
            if price > 0:
                positions.append(Position(
                    symbol=symbol,
                    quantity=bal.total,
                    entry_price=price,
                    current_price=price,
                    unrealized_pnl=0.0,
                    side=OrderSide.BUY,
                ))
        return positions

    def get_whitelisted_symbols(self) -> List[str]:
        return [
            "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
            "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT",
        ]

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _get(self, path: str, params: Optional[Dict] = None) -> Any:
        url = f"{_BASE_URL}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = Request(url, headers={"X-MBX-APIKEY": self._api_key})
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _signed_get(self, path: str, params: Optional[Dict] = None) -> Any:
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        p["signature"] = self._sign(urllib.parse.urlencode(p))
        url = f"{_BASE_URL}{path}?" + urllib.parse.urlencode(p)
        req = Request(url, headers={"X-MBX-APIKEY": self._api_key})
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _signed_post(self, path: str, params: Dict) -> Any:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(urllib.parse.urlencode(params))
        body = urllib.parse.urlencode(params).encode()
        req = Request(
            f"{_BASE_URL}{path}", data=body, method="POST",
            headers={"X-MBX-APIKEY": self._api_key, "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _signed_delete(self, path: str, params: Dict) -> Any:
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(urllib.parse.urlencode(params))
        url = f"{_BASE_URL}{path}?" + urllib.parse.urlencode(params)
        req = Request(url, method="DELETE", headers={"X-MBX-APIKEY": self._api_key})
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _sign(self, query_string: str) -> str:
        return hmac.new(
            self._api_secret.encode(), query_string.encode(), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _map_order_type(ot: OrderType) -> str:
        return {
            OrderType.MARKET:      "MARKET",
            OrderType.LIMIT:       "LIMIT",
            OrderType.STOP_LOSS:   "STOP_LOSS_LIMIT",
            OrderType.TAKE_PROFIT: "TAKE_PROFIT_LIMIT",
        }.get(ot, "MARKET")

    @staticmethod
    def _map_status(status: str) -> OrderStatus:
        return {
            "NEW":              OrderStatus.SUBMITTED,
            "PARTIALLY_FILLED": OrderStatus.PARTIAL,
            "FILLED":           OrderStatus.FILLED,
            "CANCELED":         OrderStatus.CANCELLED,
            "REJECTED":         OrderStatus.REJECTED,
            "EXPIRED":          OrderStatus.CANCELLED,
        }.get(status, OrderStatus.FAILED)
