"""
CryptoIntelAggregator — combines CoinGecko, Binance, and Whale Alert
into unified market intelligence snapshots for BabyAI agents.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from tools.crypto_intel.coingecko_client import CoinGeckoClient
from tools.crypto_intel.binance_public_client import BinancePublicClient
from tools.crypto_intel.whale_alert_client import WhaleAlertClient

_log = logging.getLogger(__name__)

# Binance symbols to always include in the snapshot
_SPOT_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]


class CryptoIntelAggregator:
    """
    High-level market intelligence interface.

    Uses lazy-initialised client instances by default; pass your own
    for testing or custom rate-limit configs.
    """

    def __init__(
        self,
        coingecko:  CoinGeckoClient    | None = None,
        binance:    BinancePublicClient | None = None,
        whale:      WhaleAlertClient   | None = None,
    ) -> None:
        self._cg    = coingecko or CoinGeckoClient()
        self._bn    = binance   or BinancePublicClient()
        self._whale = whale     or WhaleAlertClient()

    # ── Public methods ────────────────────────────────────────────────────────

    def get_market_snapshot(self) -> Dict[str, Any]:
        """
        Unified market snapshot combining three data sources.

        Returns a dict with:
          coins         — top 20 coins by market cap (CoinGecko)
          spot_prices   — real-time BTC/ETH/BNB/SOL prices (Binance)
          whale_txns    — whale transactions in the last hour (Whale Alert)
          fetched_at    — Unix timestamp of the snapshot
        """
        snapshot: Dict[str, Any] = {"fetched_at": int(time.time())}

        # CoinGecko: top 20 by market cap
        try:
            snapshot["coins"] = self._cg.get_top_coins(per_page=20)
        except Exception as exc:
            _log.error("snapshot_coingecko_failed error=%s", exc)
            snapshot["coins"] = []

        # Binance: live prices for key pairs
        spot: Dict[str, Any] = {}
        for sym in _SPOT_SYMBOLS:
            try:
                raw = self._bn.get_price(sym)
                if raw and "price" in raw:
                    spot[sym] = {"price": float(raw["price"]), "symbol": sym}
            except Exception as exc:
                _log.warning("snapshot_binance_failed sym=%s error=%s", sym, exc)
        snapshot["spot_prices"] = spot

        # Whale Alert: last hour, ≥ $1 M
        try:
            snapshot["whale_txns"] = self._whale.get_recent_transactions(
                min_value=1_000_000, limit=50
            )
        except Exception as exc:
            _log.warning("snapshot_whale_failed error=%s", exc)
            snapshot["whale_txns"] = []

        return snapshot

    def get_trending_with_whale_overlap(self) -> List[Dict[str, Any]]:
        """
        High-conviction signal: tokens trending on CoinGecko that also
        have recent whale activity in the last hour.

        Returns a list of dicts:
          coin       — CoinGecko trending item
          whale_txns — matching whale transactions for that token
          score      — overlap count (higher = more activity)
        """
        trending = self._cg.get_trending_coins()
        if not trending:
            return []

        # Build a set of trending symbols (lower-cased) for fast lookup
        trending_symbols = {
            t.get("symbol", "").lower()
            for t in trending
        }

        whale_txns = self._whale.get_recent_transactions(
            min_value=500_000, limit=100
        )

        # Index whale txns by symbol
        whale_by_symbol: Dict[str, List[Dict[str, Any]]] = {}
        for txn in whale_txns:
            sym = (txn.get("symbol") or txn.get("currency", "")).lower()
            if sym:
                whale_by_symbol.setdefault(sym, []).append(txn)

        results = []
        for coin in trending:
            sym = coin.get("symbol", "").lower()
            if sym in whale_by_symbol:
                results.append({
                    "coin":       coin,
                    "whale_txns": whale_by_symbol[sym],
                    "score":      len(whale_by_symbol[sym]),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def get_new_token_candidates(self) -> List[Dict[str, Any]]:
        """
        Identify trending small-cap coins with unusually high relative volume.

        Criteria:
          - market cap < $100 M USD
          - volume_24h / market_cap > 0.10  (high relative volume)

        Each candidate is scored 0–1 based on:
          - volume_ratio  (50 % weight)
          - trend_rank    (30 % weight; rank 1 = best)
          - whale_activity (20 % weight; ≥1 whale txn = full credit)

        Returns candidates sorted by score descending.
        """
        trending = self._cg.get_trending_coins()
        whale_txns = self._whale.get_recent_transactions(
            min_value=100_000, limit=100
        )
        whale_symbols = {
            (t.get("symbol") or t.get("currency", "")).lower()
            for t in whale_txns
        }

        candidates = []
        total = len(trending)

        for rank, coin in enumerate(trending, start=1):
            # Trending payload varies — normalise keys
            market_cap   = coin.get("market_cap")          or \
                           coin.get("data", {}).get("market_cap")
            volume_24h   = coin.get("total_volume")        or \
                           coin.get("data", {}).get("total_volume")

            # Skip if we can't compute the ratio
            if not market_cap or not volume_24h:
                continue
            try:
                market_cap = float(str(market_cap).replace(",", "").split()[0])
                volume_24h = float(str(volume_24h).replace(",", "").split()[0])
            except (ValueError, TypeError):
                continue

            if market_cap <= 0 or market_cap >= 100_000_000:
                continue
            volume_ratio = volume_24h / market_cap
            if volume_ratio < 0.10:
                continue

            sym = coin.get("symbol", "").lower()
            has_whale = sym in whale_symbols

            # Score components (0–1 each)
            vol_score   = min(volume_ratio / 1.0, 1.0)          # cap at ratio=1.0
            rank_score  = 1.0 - (rank - 1) / max(total, 1)       # rank 1 → 1.0
            whale_score = 1.0 if has_whale else 0.0

            score = 0.50 * vol_score + 0.30 * rank_score + 0.20 * whale_score

            candidates.append({
                "coin":         coin,
                "market_cap":   market_cap,
                "volume_24h":   volume_24h,
                "volume_ratio": round(volume_ratio, 4),
                "has_whale":    has_whale,
                "trend_rank":   rank,
                "score":        round(score, 4),
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates
