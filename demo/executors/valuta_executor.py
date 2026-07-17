"""
ValutaExecutor — ECB-baseret valuta paper-trade executor.

Henter live kurser fra European Central Bank XML feed.
Ingen betalte API-nøgler kræves.

Kilde: https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml
"""
from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logger = logging.getLogger(__name__)

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

# Valutapar vi monitorerer (alle prises i EUR)
MONITORED_CURRENCIES = ["USD", "DKK", "GBP", "JPY", "CHF", "SEK", "NOK", "CAD", "AUD", "NZD"]

# Simuleret typisk FX fee og slippage
FX_FEE = 0.001       # 0.1%
MAX_SLIPPAGE = 0.001  # 0.1% max

# In-memory cache fallback (Redis-nøgle: "valuta:rates:{date}")
_MEMORY_CACHE: dict[str, dict] = {}


def _get_redis():
    """Forsøg at forbinde til Redis. Returner None ved fejl."""
    try:
        import redis as _redis  # type: ignore
        r = _redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=1)
        r.ping()
        return r
    except Exception:
        return None


class ValutaExecutor:
    """
    Paper-trade executor baseret på ECB daglige kurser.

    Alle trades er simulerede — ingen rigtige penge-transaktioner.
    """

    def __init__(self) -> None:
        self._redis = _get_redis()

    def fetch_rates(self) -> dict[str, float]:
        """
        Hent ECB daglige kurser.

        Returnerer fx {"USD": 1.0823, "DKK": 7.4601, "GBP": 0.8534, ...}
        Alle kurser er EUR-baserede (1 EUR = X valuta).

        Cache i Redis/memory med TTL 3600 sekunder.
        Fallback: returnerer realistiske statiske kurser hvis ECB er utilgængeligt.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cache_key = f"valuta:rates:{today}"

        # Tjek cache — returner rene kurser til caching, tilføj noise nedenfor
        cached = self._cache_get(cache_key)
        if cached:
            return self._apply_intraday_noise(cached)

        # Hent fra ECB
        try:
            req = urllib.request.Request(
                ECB_URL,
                headers={"User-Agent": "BabyAI-Research/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read().decode("utf-8")
            rates = self._parse_ecb_xml(xml_data)
            if rates:
                self._cache_set(cache_key, rates, ttl=3600)  # gem rene kurser
                return self._apply_intraday_noise(rates)
        except (urllib.error.URLError, ET.ParseError, Exception) as exc:
            logger.warning("ECB fetch fejlede: %s — bruger fallback kurser", exc)

        fallback = self._fallback_rates()
        self._cache_set(cache_key, fallback, ttl=300)
        return self._apply_intraday_noise(fallback)

    def _apply_intraday_noise(self, rates: dict[str, float]) -> dict[str, float]:
        """
        Tilføj realistisk intradag FX-variation (~0.08% per runde).

        Cachen gemmer rene ECB kurser; noise appliceres ved hvert kald
        så momentum-signalerne varierer mellem runder og agenten ikke
        låser fast på ét valutapar.
        """
        noisy: dict[str, float] = {}
        for currency, rate in rates.items():
            if currency.startswith("_"):
                noisy[currency] = rate
            else:
                noisy[currency] = rate * (1 + random.gauss(0, 0.0008))
        return noisy

    def _parse_ecb_xml(self, xml_data: str) -> dict[str, float]:
        """Parser ECB XML og returnerer kurs-dict."""
        root = ET.fromstring(xml_data)
        rates: dict[str, float] = {}
        # ECB XML namespace
        ns = {"gesmes": "http://www.gesmes.org/xml/2002-08-01",
              "ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
        # Find alle Cube elementer med currency og rate attributter
        for cube in root.iter():
            currency = cube.get("currency")
            rate = cube.get("rate")
            if currency and rate:
                try:
                    rates[currency] = float(rate)
                except ValueError:
                    continue
        return rates

    def _fallback_rates(self) -> dict[str, float]:
        """Realistiske ECB-priser (marts 2026 niveau) som fallback."""
        logger.warning("Bruger statiske fallback valutakurser")
        return {
            "USD": 1.0823,
            "DKK": 7.4601,
            "GBP": 0.8534,
            "JPY": 161.42,
            "CHF": 0.9312,
            "SEK": 11.234,
            "NOK": 11.752,
            "CAD": 1.4821,
            "AUD": 1.6534,
            "NZD": 1.8123,
            "_fallback": 1.0,
        }

    def calculate_spread(
        self,
        base: str,
        quote: str,
        rates: dict[str, float],
    ) -> float:
        """
        Beregn effektivt spread ved at route via EUR.

        Spread = forskel mellem direkte kurs og EUR-routet kurs, i procent.
        Returnerer 0.0 hvis et par ikke kan prises.
        """
        base_rate = rates.get(base)
        quote_rate = rates.get(quote)
        if not base_rate or not quote_rate or base_rate <= 0:
            return 0.0
        # Via EUR: 1 base → (1/base_rate) EUR → (1/base_rate)*quote_rate quote
        synthetic_rate = quote_rate / base_rate
        # Markedsestimeret spread (typisk 0.05–0.3% for major pairs)
        # Vi bruger volatilitet som proxy
        spread_proxy = abs(rates.get("_momentum_" + base, 0.002))
        spread_pct = max(0.0005, min(0.005, spread_proxy + 0.0015))
        return spread_pct

    def simulate_trade(
        self,
        agent_id: str,
        base: str,
        quote: str,
        amount_eur: float,
        action: str,
        rates: dict[str, float],
    ) -> dict:
        """
        Simuler en paper-trade.

        action: "buy_{base}_sell_{quote}" eller "sell_{base}_buy_{quote}"
        amount_eur: position-størrelse i EUR

        Returnerer komplet trade-result dict.
        """
        base_rate = rates.get(base, 1.0)
        quote_rate = rates.get(quote, 1.0)

        if base_rate <= 0 or quote_rate <= 0:
            return {"executed": False, "reason": "invalid_rates"}

        # Simuleret slippage (0–max_slippage, normalfordelt)
        slippage = abs(random.gauss(0, MAX_SLIPPAGE / 3))
        slippage = min(slippage, MAX_SLIPPAGE)

        # Kurs fra EUR til base
        rate_eur_to_base = base_rate
        # Kurs fra base til quote
        rate_base_to_quote = quote_rate / base_rate

        # Beregn beløb
        amount_base = amount_eur * rate_eur_to_base
        amount_quote = amount_base * rate_base_to_quote

        # Momentum signal fra rates (injiceret af challenge_engine)
        momentum_key = f"_momentum_{base}"
        momentum = rates.get(momentum_key, 0.0)

        # P&L: positiv momentum = gevinst ved buy, tab ved sell
        direction = 1.0 if "buy" in action.split("_")[0] else -1.0
        gross_pnl_pct = momentum * direction
        fee_pct = FX_FEE + slippage
        net_pnl_pct = gross_pnl_pct - fee_pct
        net_pnl_eur = amount_eur * net_pnl_pct

        return {
            "executed": True,
            "trade_id": f"{agent_id}_{int(time.time()*1000)}",
            "base": base,
            "quote": quote,
            "action": action,
            "rate_used": round(rate_base_to_quote, 6),
            "amount_eur": round(amount_eur, 4),
            "amount_base": round(amount_base, 4),
            "amount_quote": round(amount_quote, 4),
            "slippage_simulated": round(slippage, 6),
            "fee_simulated": FX_FEE,
            "gross_pnl_pct": round(gross_pnl_pct, 6),
            "net_pnl_pct": round(net_pnl_pct, 6),
            "net_pnl_eur": round(net_pnl_eur, 6),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def calculate_pnl(self, trade_result: dict, current_rates: dict[str, float]) -> float:
        """
        Beregn realiseret P&L i EUR baseret på nuværende kurser.

        Returner P&L i EUR (positiv = gevinst, negativ = tab).
        """
        if not trade_result.get("executed"):
            return 0.0
        return float(trade_result.get("net_pnl_eur", 0.0))

    # ── Redis/memory cache helpers ──────────────────────────────────────────────

    def _cache_get(self, key: str) -> Optional[dict]:
        if self._redis is not None:
            try:
                raw = self._redis.get(key)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
        return _MEMORY_CACHE.get(key)

    def _cache_set(self, key: str, value: dict, ttl: int = 3600) -> None:
        _MEMORY_CACHE[key] = value
        if self._redis is not None:
            try:
                self._redis.setex(key, ttl, json.dumps(value))
            except Exception:
                pass
