"""
eToro REST API client for BabyAI agents.
Direct HTTP — no MCP, no Claude Desktop in the data path.
All data stays local.

Credentials from environment:
  ETORO_API_KEY     — from eToro Settings → Trading → Create New Key
  ETORO_USER_KEY    — demo or real user key
  ETORO_MODE        — "demo" | "real" (default: "demo")

Base URLs:
  demo: https://public-api.etoro.com/api/v1  (demo uses same base)
  real: https://public-api.etoro.com/api/v1
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List

import requests

logger = logging.getLogger(__name__)

ETORO_BASE_URLS = {
    "demo": "https://public-api.etoro.com/api/v1",
    "real": "https://public-api.etoro.com/api/v1",
}


class EToroClient:
    """
    Internal eToro REST client for BabyAI agents.

    Never instantiate with real keys in demo/test code.
    Always check self.mode before any write operation.
    """

    def __init__(self) -> None:
        self._api_key  = os.getenv("ETORO_API_KEY", "")
        self._user_key = os.getenv("ETORO_USER_KEY", "")
        self.mode      = os.getenv("ETORO_MODE", "demo")
        self._base_url = ETORO_BASE_URLS.get(self.mode, ETORO_BASE_URLS["demo"])
        self._session  = requests.Session()

        if not self._api_key:
            logger.warning(
                "ETORO_API_KEY not set — "
                "EToroClient running in read-only stub mode"
            )

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key":    self._api_key,
            "x-user-key":   self._user_key,
            "x-request-id": str(uuid.uuid4()),
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: Dict[str, Any] | None = None) -> Dict[str, Any] | List[Any]:
        """Internal GET — returns empty dict on any error."""
        if not self._api_key:
            return {}
        try:
            url  = f"{self._base_url}{path}"
            resp = self._session.get(
                url, headers=self._headers(),
                params=params or {}, timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("eToro GET %s failed: %s", path, exc)
            return {}

    # ── Read endpoints (safe in both modes) ──────────────────────────────────

    def get_portfolio(self) -> Dict[str, Any]:
        """Current portfolio positions and P&L."""
        result = self._get("/portfolio")
        return result if isinstance(result, dict) else {}

    def get_watchlists(self) -> List[Dict[str, Any]]:
        """All watchlists for the account."""
        result = self._get("/watchlists")
        return result if isinstance(result, list) else []

    def search_instruments(
        self,
        query: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search tradeable instruments by name/symbol."""
        result = self._get(
            "/market-data/search",
            params={"internalSymbolFull": query, "limit": limit},
        )
        return result if isinstance(result, list) else []

    def get_instrument_price(self, instrument_id: int) -> Dict[str, Any]:
        """Current price for an instrument ID."""
        result = self._get(f"/market-data/instruments/{instrument_id}")
        return result if isinstance(result, dict) else {}

    # ── Write endpoints (guarded by mode check) ───────────────────────────────

    def place_order(
        self,
        instrument_id: int,
        amount: float,
        is_buy: bool,
        requires_confirm: bool = True,
    ) -> Dict[str, Any]:
        """
        Place a market order.

        ALWAYS checks self.mode.
        In demo mode: uses demo execution endpoint.
        In real mode: requires requires_confirm=True
                      (caller must explicitly pass False to execute).

        BabyAI agents should NEVER pass requires_confirm=False
        without explicit human approval via CLI.
        """
        if not self._api_key:
            logger.warning("place_order called without API key — no-op")
            return {}

        if self.mode == "real" and requires_confirm:
            logger.error(
                "place_order blocked: mode=real requires "
                "explicit human approval. "
                "Use CLI: python -m babyai.cli approve-trade"
            )
            return {"error": "requires_human_approval", "blocked": True}

        endpoint = (
            "/trading/execution/demo/market-open-orders/by-amount"
            if self.mode == "demo"
            else "/trading/execution/real/market-open-orders/by-amount"
        )

        payload = {
            "instrumentID": instrument_id,
            "amount":       amount,
            "isBuy":        is_buy,
        }

        try:
            resp = self._session.post(
                f"{self._base_url}{endpoint}",
                headers=self._headers(),
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(
                "eToro order placed: mode=%s instrument=%s amount=%s buy=%s",
                self.mode, instrument_id, amount, is_buy,
            )
            return resp.json()
        except Exception as exc:
            logger.error("eToro place_order failed: %s", exc)
            return {"error": str(exc)}

    def close_position(self, position_id: str) -> Dict[str, Any]:
        """
        Close an open position.
        Same human-approval guard as place_order.
        """
        if self.mode == "real":
            logger.error(
                "close_position blocked in real mode — "
                "use CLI: python -m babyai.cli approve-trade"
            )
            return {"error": "requires_human_approval", "blocked": True}

        try:
            resp = self._session.delete(
                f"{self._base_url}/trading/execution/demo/positions/{position_id}",
                headers=self._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.error("eToro close_position failed: %s", exc)
            return {"error": str(exc)}
