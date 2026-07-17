"""
babyai/trading/ecb_client.py — ECB exchange rate client.

Fetches EUR-based FX rates from the ECB Data Portal.
All rates are expressed as units of foreign currency per 1 EUR.

Usage:
    client = ECBClient()
    rates = await client.get_rates()   # {"USD": 1.08, "GBP": 0.86, ...}
    rate  = await client.get_rate("USD", "GBP")  # cross via EUR
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional

import httpx

_log = logging.getLogger(__name__)

_ECB_URL = (
    "https://data-api.ecb.europa.eu/service/data/EXR/"
    "D.{currency}.EUR.SP00.A?format=jsondata&lastNObservations=1"
)

SUPPORTED_CURRENCIES: tuple[str, ...] = (
    "USD", "GBP", "JPY", "CHF", "DKK", "SEK", "NOK", "CAD", "AUD"
)

_TIMEOUT_S = 8.0


class ECBClient:
    """Async ECB FX rate client with in-process TTL cache."""

    def __init__(self, cache_ttl_seconds: int = 60) -> None:
        self._cache_ttl = cache_ttl_seconds
        self._cache: Optional[Dict[str, float]] = None
        self._cache_ts: float = 0.0
        self._lock = asyncio.Lock()

    async def get_rates(self) -> Dict[str, float]:
        """
        Return EUR-based rates for all supported currencies.

        Returns cached result if within TTL. On HTTP error or timeout,
        returns last cached value if available; otherwise raises.
        """
        async with self._lock:
            now = time.monotonic()
            if self._cache is not None and (now - self._cache_ts) < self._cache_ttl:
                _log.debug("ecb_client_cache_hit age_s=%.1f", now - self._cache_ts)
                return dict(self._cache)

            try:
                fresh = await self._fetch_all()
                self._cache = fresh
                self._cache_ts = time.monotonic()
                return dict(fresh)
            except Exception as exc:
                if self._cache is not None:
                    _log.warning(
                        "ecb_client_fetch_failed error=%s — returning stale cache", exc
                    )
                    return dict(self._cache)
                raise

    async def get_rate(self, from_ccy: str, to_ccy: str) -> float:
        """
        Return cross rate from_ccy → to_ccy computed via EUR base.

        Both currencies must be in SUPPORTED_CURRENCIES or be "EUR".
        Formula:  rate = (1 / EUR/from_ccy) * EUR/to_ccy
                       = to_ccy per from_ccy
        """
        from_ccy = from_ccy.upper()
        to_ccy   = to_ccy.upper()

        for ccy in (from_ccy, to_ccy):
            if ccy != "EUR" and ccy not in SUPPORTED_CURRENCIES:
                raise ValueError(f"Unsupported currency: {ccy!r}")

        rates = await self.get_rates()

        eur_from = 1.0 if from_ccy == "EUR" else rates[from_ccy]
        eur_to   = 1.0 if to_ccy   == "EUR" else rates[to_ccy]

        return eur_to / eur_from

    # ── Internal ────────────────────────────────────────────────────────────────

    async def _fetch_all(self) -> Dict[str, float]:
        """Fetch all supported currencies concurrently."""
        timeout = httpx.Timeout(timeout=_TIMEOUT_S)
        async with httpx.AsyncClient(timeout=timeout) as client:
            tasks = [self._fetch_one(client, ccy) for ccy in SUPPORTED_CURRENCIES]
            results = await asyncio.gather(*tasks)

        rates: Dict[str, float] = {}
        for ccy, rate in zip(SUPPORTED_CURRENCIES, results):
            if rate is not None:
                rates[ccy] = rate

        if not rates:
            raise RuntimeError("ECB returned no usable rates")

        return rates

    async def _fetch_one(
        self, client: httpx.AsyncClient, currency: str
    ) -> Optional[float]:
        url = _ECB_URL.format(currency=currency)
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            # ECB SDMX-JSON: dataSets[0].series["0:0:0:0:0"].observations
            datasets = data.get("dataSets", [])
            if not datasets:
                return None
            series = datasets[0].get("series", {})
            if not series:
                return None
            obs = next(iter(series.values())).get("observations", {})
            if not obs:
                return None
            # observations is {"0": [rate, ...], ...} — take last value
            last_obs = obs[max(obs.keys(), key=int)]
            return float(last_obs[0])
        except Exception as exc:
            _log.warning("ecb_fetch_failed currency=%s error=%s", currency, exc)
            return None
