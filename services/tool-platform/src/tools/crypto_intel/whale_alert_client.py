"""
WhaleAlertClient — Whale Alert API v1, free tier.

Requires WHALE_ALERT_API_KEY in environment or .env file.
Falls back gracefully (logs a warning, returns empty list) if no key is set.

Free tier limits: ~100 calls/day.  No rate limiter needed at this volume.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

_log = logging.getLogger(__name__)

_BASE_URL  = "https://api.whale-alert.io/v1"
_TIMEOUT   = 15
_BLOCKCHAINS = ["bitcoin", "ethereum", "tron", "solana"]


class WhaleAlertClient:
    """
    Wrapper for the Whale Alert REST API.

    API key is read from the WHALE_ALERT_API_KEY environment variable.
    If the key is absent, all methods return empty results and log a warning.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        """
        Args:
            api_key: override the env var (useful in tests).
        """
        self._api_key = api_key or os.getenv("WHALE_ALERT_API_KEY", "")
        if not self._api_key:
            _log.warning(
                "whale_alert_no_key: WHALE_ALERT_API_KEY not set — "
                "all methods will return empty results"
            )
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    @property
    def available(self) -> bool:
        """True if an API key is configured."""
        return bool(self._api_key)

    # ── Public methods ────────────────────────────────────────────────────────

    def get_recent_transactions(
        self,
        min_value: int = 1_000_000,
        limit: int = 100,
        start: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent large on-chain transactions.

        Args:
            min_value: minimum USD value to include (default $1 M)
            limit:     max transactions to return (free tier cap: 100)
            start:     Unix timestamp to start from (default: now - 3600)

        Returns:
            List of transaction dicts; empty list if no key or API error.
        """
        if not self._api_key:
            return []

        params: Dict[str, Any] = {
            "api_key":       self._api_key,
            "min_value":     min_value,
            "limit":         limit,
            "currency":      "usd",
            "blockchain":    ",".join(_BLOCKCHAINS),
        }
        if start is not None:
            params["start"] = start
        else:
            params["start"] = int(time.time()) - 3600  # last hour

        data = self._get("/transactions", params)
        if not isinstance(data, dict):
            return []
        return data.get("transactions", [])

    def get_transaction(self, tx_hash: str) -> Dict[str, Any]:
        """
        Fetch details for a single transaction by hash.

        Args:
            tx_hash: on-chain transaction hash

        Returns:
            Transaction dict, or {} on error / missing key.
        """
        if not self._api_key:
            return {}
        params = {"api_key": self._api_key}
        return self._get(f"/transaction/{tx_hash}", params) or {}

    def get_status(self) -> Dict[str, Any]:
        """
        Return API status and remaining call quota.

        Returns:
            { "status": "ok", "blockchains": […], … } or {}
        """
        if not self._api_key:
            return {}
        params = {"api_key": self._api_key}
        return self._get("/status", params) or {}

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        _attempt: int = 0,
    ) -> Any:
        """GET with exponential backoff on 429."""
        url = _BASE_URL + path
        try:
            resp = self._session.get(url, params=params, timeout=_TIMEOUT)
            if resp.status_code == 429:
                wait = min(10 * (2 ** _attempt), 120)
                _log.warning(
                    "whale_alert_rate_limited attempt=%d wait=%ds", _attempt, wait
                )
                time.sleep(wait)
                return self._get(path, params, _attempt=_attempt + 1)
            if resp.status_code == 401:
                _log.error("whale_alert_unauthorized — check WHALE_ALERT_API_KEY")
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            _log.error("whale_alert_request_failed url=%s error=%s", url, exc)
            return None
