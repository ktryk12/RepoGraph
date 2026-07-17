"""
CoinGeckoClient — free CoinGecko API v3, no key required.

Rate limit: 30 calls/minute on the demo (public) tier.
A token-bucket rate limiter is enforced internally.
Exponential backoff is applied on HTTP 429 responses.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List

import requests

_log = logging.getLogger(__name__)

_BASE_URL = "https://api.coingecko.com/api/v3"
_TIMEOUT  = 15  # seconds per request


class _RateLimiter:
    """Simple token-bucket: max `rate` calls per 60 seconds."""

    def __init__(self, rate: int = 30) -> None:
        self._rate      = rate
        self._tokens    = float(rate)
        self._lock      = threading.Lock()
        self._last_refill = time.monotonic()

    def acquire(self) -> None:
        """Block until a token is available."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self._rate,
                    self._tokens + elapsed * (self._rate / 60.0),
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
            time.sleep(0.5)


class CoinGeckoClient:
    """
    Thin wrapper around the CoinGecko public REST API.

    All methods return raw parsed JSON (dict or list).
    On unrecoverable error they return an empty container and log the failure.
    """

    def __init__(self, rate_per_minute: int = 28) -> None:
        """
        Args:
            rate_per_minute: max requests per minute (default 28, safely under the 30 limit).
        """
        self._session     = requests.Session()
        self._session.headers.update({"Accept": "application/json"})
        self._limiter     = _RateLimiter(rate=rate_per_minute)

    # ── Public methods ────────────────────────────────────────────────────────

    def get_price(
        self,
        coin_ids: List[str],
        vs_currency: str = "usd",
    ) -> Dict[str, Any]:
        """
        Fetch current prices for one or more coins.

        Args:
            coin_ids:    CoinGecko coin id list, e.g. ["bitcoin", "ethereum"]
            vs_currency: target currency, default "usd"

        Returns:
            { "bitcoin": { "usd": 62000.0 }, ... }
        """
        params = {
            "ids":           ",".join(coin_ids),
            "vs_currencies": vs_currency,
        }
        return self._get("/simple/price", params) or {}

    def get_trending_coins(self) -> List[Dict[str, Any]]:
        """
        Return the top-7 trending coins on CoinGecko in the last 24 h.

        Returns:
            List of coin dicts as returned by /search/trending → coins[].item
        """
        data = self._get("/search/trending")
        if not isinstance(data, dict):
            return []
        return [c.get("item", c) for c in data.get("coins", [])]

    def get_coin_market_data(self, coin_id: str) -> Dict[str, Any]:
        """
        Fetch detailed market data for a single coin.

        Args:
            coin_id: CoinGecko coin id, e.g. "solana"

        Returns:
            Full /coins/{id} response dict (market_data, community_data, etc.)
        """
        params = {
            "localization":   "false",
            "tickers":        "false",
            "community_data": "false",
            "developer_data": "false",
            "sparkline":      "false",
        }
        return self._get(f"/coins/{coin_id}", params) or {}

    def get_global_market(self) -> Dict[str, Any]:
        """
        Return global crypto market overview (total market cap, BTC dominance, …).

        Returns:
            The 'data' sub-dict from /global
        """
        data = self._get("/global")
        if isinstance(data, dict):
            return data.get("data", data)
        return {}

    def get_top_coins(
        self,
        vs_currency: str = "usd",
        per_page: int = 20,
        page: int = 1,
    ) -> List[Dict[str, Any]]:
        """
        Return coins sorted by market cap descending.

        Args:
            vs_currency: target currency
            per_page:    results per page (max 250)
            page:        page number (1-indexed)

        Returns:
            List of coin market dicts.
        """
        params = {
            "vs_currency": vs_currency,
            "order":       "market_cap_desc",
            "per_page":    per_page,
            "page":        page,
            "sparkline":   "false",
        }
        return self._get("/coins/markets", params) or []

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(
        self,
        path: str,
        params: Dict[str, Any] | None = None,
        *,
        _attempt: int = 0,
    ) -> Any:
        """Acquire rate-limit token, then GET; retry with backoff on 429."""
        self._limiter.acquire()
        url = _BASE_URL + path
        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            if resp.status_code == 429:
                wait = min(5 * (2 ** _attempt), 60)
                _log.warning("coingecko_rate_limited attempt=%d wait=%.0fs", _attempt, wait)
                time.sleep(wait)
                return self._get(path, params, _attempt=_attempt + 1)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            _log.error("coingecko_request_failed url=%s error=%s", url, exc)
            return None
