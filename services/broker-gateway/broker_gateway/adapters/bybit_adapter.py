"""
broker_gateway/adapters/bybit_adapter.py — Bybit Spot/Unified adapter (v5 API).

Env vars (bruges KUN hvis adapteren instansieres uden eksplicitte nøgler):
  BYBIT_API_KEY
  BYBIT_API_SECRET
  BYBIT_TESTNET : "1" → brug testnet endpoint
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

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

_log = logging.getLogger("bybit-adapter")

_BASE_URL = (
    "https://api-testnet.bybit.com" if os.getenv("BYBIT_TESTNET") == "1"
    else "https://api.bybit.com"
)
_RECV_WINDOW = "5000"


class BybitAdapter(BrokerAdapter):
    """
    Bybit Unified Trading Account adapter (API v5).

    Spot-kategori brugt som default. Ændres til 'linear' for USDT-perps.
    """

    def __init__(self, api_key: str, api_secret: str, category: str = "spot") -> None:
        self._api_key    = api_key
        self._api_secret = api_secret
        self._category   = category

    @property
    def exchange_name(self) -> str:
        return "bybit"

    def is_paper(self) -> bool:
        return False

    def health_check(self) -> bool:
        try:
            data = self._get("/v5/market/time")
            return data.get("retCode") == 0
        except Exception:
            return False

    def get_ticker_price(self, symbol: str) -> float:
        try:
            data = self._get("/v5/market/tickers", {"category": self._category, "symbol": symbol})
            items = data.get("result", {}).get("list", [])
            if items:
                return float(items[0]["lastPrice"])
        except Exception as exc:
            _log.error("bybit_get_price_error symbol=%s error=%s", symbol, exc)
        return 0.0

    def get_balance(self) -> List[AccountBalance]:
        try:
            data = self._signed_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
            balances = []
            for acct in data.get("result", {}).get("list", []):
                for coin in acct.get("coin", []):
                    total = float(coin.get("walletBalance", 0))
                    locked = float(coin.get("locked", 0))
                    if total > 0:
                        balances.append(AccountBalance(
                            currency=coin["coin"],
                            available=total - locked,
                            locked=locked,
                            total=total,
                        ))
            return balances
        except Exception as exc:
            _log.error("bybit_get_balance_error error=%s", exc)
            return []

    def submit_order(self, intent: OrderIntent) -> OrderReceipt:
        now = datetime.now(timezone.utc)
        try:
            body: Dict[str, Any] = {
                "category":   self._category,
                "symbol":     intent.symbol,
                "side":       "Buy" if intent.side == OrderSide.BUY else "Sell",
                "orderType":  "Market" if intent.is_market() else "Limit",
                "qty":        f"{intent.quantity:.8f}",
                "orderLinkId": intent.client_order_id or intent.order_id[:36],
            }
            if intent.order_type == OrderType.LIMIT and intent.price:
                body["price"] = f"{intent.price:.8f}"
            if intent.stop_price:
                body["stopLoss"] = f"{intent.stop_price:.8f}"

            data = self._signed_post("/v5/order/create", body)
            if data.get("retCode") != 0:
                raise RuntimeError(data.get("retMsg", "unknown error"))

            result = data.get("result", {})
            return OrderReceipt(
                order_id=intent.order_id,
                exchange_order_id=result.get("orderId", ""),
                symbol=intent.symbol,
                side=intent.side,
                order_type=intent.order_type,
                status=OrderStatus.SUBMITTED,
                requested_quantity=intent.quantity,
                submitted_at=now,
                raw_response=result,
            )
        except Exception as exc:
            _log.error("bybit_submit_order_error order_id=%s error=%s", intent.order_id, exc)
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
            data = self._signed_post("/v5/order/cancel", {
                "category": self._category,
                "symbol": symbol,
                "orderId": exchange_order_id,
            })
            return data.get("retCode") == 0
        except Exception as exc:
            _log.error("bybit_cancel_error symbol=%s oid=%s error=%s", symbol, exchange_order_id, exc)
            return False

    def get_order_status(self, symbol: str, exchange_order_id: str) -> OrderReceipt:
        try:
            data = self._signed_get("/v5/order/realtime", {
                "category": self._category,
                "symbol": symbol,
                "orderId": exchange_order_id,
            })
            items = data.get("result", {}).get("list", [])
            if not items:
                raise ValueError("order not found")
            o = items[0]
            return OrderReceipt(
                order_id=o.get("orderLinkId", exchange_order_id),
                exchange_order_id=exchange_order_id,
                symbol=symbol,
                side=OrderSide.BUY if o["side"] == "Buy" else OrderSide.SELL,
                order_type=OrderType.MARKET,
                status=self._map_status(o.get("orderStatus", "")),
                requested_quantity=float(o.get("qty", 0)),
                filled_quantity=float(o.get("cumExecQty", 0)),
                average_price=float(o.get("avgPrice", 0)),
                raw_response=o,
            )
        except Exception as exc:
            return OrderReceipt(
                order_id="unknown", exchange_order_id=exchange_order_id,
                symbol=symbol, side=OrderSide.BUY, order_type=OrderType.MARKET,
                status=OrderStatus.FAILED, requested_quantity=0.0,
                raw_response={"error": str(exc)},
            )

    def get_open_positions(self) -> List[Position]:
        try:
            data = self._signed_get("/v5/position/list", {"category": self._category})
            positions = []
            for p in data.get("result", {}).get("list", []):
                qty = float(p.get("size", 0))
                if qty > 0:
                    positions.append(Position(
                        symbol=p["symbol"],
                        quantity=qty,
                        entry_price=float(p.get("avgPrice", 0)),
                        current_price=float(p.get("markPrice", 0)),
                        unrealized_pnl=float(p.get("unrealisedPnl", 0)),
                        side=OrderSide.BUY if p.get("side") == "Buy" else OrderSide.SELL,
                    ))
            return positions
        except Exception as exc:
            _log.error("bybit_get_positions_error error=%s", exc)
            return []

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
        with urlopen(Request(url), timeout=10) as resp:
            return json.loads(resp.read())

    def _signed_get(self, path: str, params: Optional[Dict] = None) -> Any:
        p = dict(params or {})
        ts = str(int(time.time() * 1000))
        query = urllib.parse.urlencode(p)
        sig = self._sign(f"{ts}{self._api_key}{_RECV_WINDOW}{query}")
        url = f"{_BASE_URL}{path}?{query}"
        headers = {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
            "X-BAPI-SIGN": sig,
        }
        with urlopen(Request(url, headers=headers), timeout=10) as resp:
            return json.loads(resp.read())

    def _signed_post(self, path: str, body: Dict) -> Any:
        ts = str(int(time.time() * 1000))
        raw = json.dumps(body, separators=(",", ":"))
        sig = self._sign(f"{ts}{self._api_key}{_RECV_WINDOW}{raw}")
        headers = {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
            "X-BAPI-SIGN": sig,
            "Content-Type": "application/json",
        }
        req = Request(f"{_BASE_URL}{path}", data=raw.encode(), method="POST", headers=headers)
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def _sign(self, payload: str) -> str:
        return hmac.new(self._api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def _map_status(status: str) -> OrderStatus:
        return {
            "New":            OrderStatus.SUBMITTED,
            "PartiallyFilled": OrderStatus.PARTIAL,
            "Filled":         OrderStatus.FILLED,
            "Cancelled":      OrderStatus.CANCELLED,
            "Rejected":       OrderStatus.REJECTED,
        }.get(status, OrderStatus.FAILED)
