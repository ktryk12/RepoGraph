"""
BinancePublicClient — unauthenticated Binance REST API v3.

No API key required for market data endpoints.
Rate limit: 1200 requests/minute (weight-based); this client uses a
conservative token-bucket of 1 000 req/min to stay safely under.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

import requests

_log = logging.getLogger(__name__)

_BASE_URL = "https://api.binance.com"
_TIMEOUT  = 10  # seconds


class _RateLimiter:
    """Token-bucket rate limiter."""

    def __init__(self, rate_per_minute: int = 1000) -> None:
        self._rate        = rate_per_minute
        self._tokens      = float(rate_per_minute)
        self._lock        = threading.Lock()
        self._last_refill = time.monotonic()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now     = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self._rate,
                    self._tokens + elapsed * (self._rate / 60.0),
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
            time.sleep(0.1)


class BinancePublicClient:
    """
    Wrapper for Binance public market data endpoints.

    All methods return raw parsed JSON (dict or list).
    On error they return an empty container and log the failure.
    """

    def __init__(self, rate_per_minute: int = 1000) -> None:
        """
        Args:
            rate_per_minute: soft cap on outgoing requests (default 1 000).
        """
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._limiter = _RateLimiter(rate_per_minute)

    # ── Public methods ────────────────────────────────────────────────────────

    def get_price(self, symbol: str) -> Dict[str, Any]:
        """
        Latest price for a trading pair.

        Args:
            symbol: e.g. "BTCUSDT"

        Returns:
            { "symbol": "BTCUSDT", "price": "62000.00" }
        """
        return self._get("/api/v3/ticker/price", {"symbol": symbol.upper()}) or {}

    def get_24h_stats(self, symbol: str) -> Dict[str, Any]:
        """
        24-hour rolling window statistics.

        Args:
            symbol: e.g. "ETHUSDT"

        Returns:
            Dict with open, high, low, close, volume, priceChangePercent, etc.
        """
        return self._get("/api/v3/ticker/24hr", {"symbol": symbol.upper()}) or {}

    def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """
        Current order book (bids and asks).

        Args:
            symbol: trading pair
            limit:  depth (valid: 5, 10, 20, 50, 100, 500, 1000, 5000)

        Returns:
            { "lastUpdateId": int, "bids": [[price, qty], …], "asks": [[price, qty], …] }
        """
        params = {"symbol": symbol.upper(), "limit": limit}
        return self._get("/api/v3/depth", params) or {}

    def get_recent_trades(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Most recent trades.

        Args:
            symbol: trading pair
            limit:  number of trades to return (max 1 000)

        Returns:
            List of trade dicts: id, price, qty, time, isBuyerMaker, …
        """
        params = {"symbol": symbol.upper(), "limit": limit}
        return self._get("/api/v3/trades", params) or []

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[List[Any]]:
        """
        Candlestick (kline) data.

        Args:
            symbol:     trading pair, e.g. "SOLUSDT"
            interval:   "1m" | "5m" | "15m" | "1h" | "4h" | "1d" | "1w" | …
            limit:      number of candles (max 1 000)
            start_time: epoch ms (optional)
            end_time:   epoch ms (optional)

        Returns:
            List of OHLCV lists:
            [open_time, open, high, low, close, volume, close_time, …]
        """
        params: Dict[str, Any] = {
            "symbol":   symbol.upper(),
            "interval": interval,
            "limit":    limit,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return self._get("/api/v3/klines", params) or []

    def get_exchange_info(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Exchange trading rules and symbol metadata.

        Args:
            symbol: if provided, filter to a single pair

        Returns:
            Full exchange info dict (or filtered single-symbol dict).
        """
        params = {"symbol": symbol.upper()} if symbol else {}
        return self._get("/api/v3/exchangeInfo", params) or {}

    def get_all_prices(self) -> List[Dict[str, Any]]:
        """
        Latest prices for ALL trading pairs.

        Returns:
            List of { "symbol": …, "price": … } dicts.
        """
        return self._get("/api/v3/ticker/price") or []

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        _attempt: int = 0,
    ) -> Any:
        """Acquire rate-limit token, then GET; retry with backoff on 429."""
        self._limiter.acquire()
        url = _BASE_URL + path
        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            if resp.status_code == 429:
                # Binance returns Retry-After header when rate-limited
                retry_after = int(resp.headers.get("Retry-After", 5 * (2 ** _attempt)))
                wait = min(retry_after, 60)
                _log.warning(
                    "binance_rate_limited attempt=%d wait=%ds", _attempt, wait
                )
                time.sleep(wait)
                return self._get(path, params, _attempt=_attempt + 1)
            if resp.status_code == 418:
                # IP banned — back off hard
                wait = 60 * (2 ** _attempt)
                _log.error("binance_ip_banned attempt=%d wait=%ds", _attempt, wait)
                time.sleep(min(wait, 300))
                return self._get(path, params, _attempt=_attempt + 1)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            _log.error("binance_request_failed url=%s error=%s", url, exc)
            return None
