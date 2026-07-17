"""
Market data via CoinGecko REST API — no API key required.
Returns pandas DataFrames with columns: timestamp, open, high, low, close, volume.

Primary endpoint: /coins/{id}/market_chart?interval=daily
  — gives daily close + volume, up to 365 days, more reliable than OHLC on free tier.
  OHLC is approximated: open=prev_close, high/low ± 0.1% of close (paper trading only).

Rate-limit handling: 429 → exponential backoff, max 2 retries.
Never crashes — returns empty DataFrame on any unrecoverable error.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
import urllib.parse
from typing import Any, Optional

import pandas as pd

_log = logging.getLogger(__name__)

_COINGECKO_BASE = "https://api.coingecko.com/api/v3"
_TIMEOUT = 15

_SYMBOL_MAP: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "ADA": "cardano",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "DOT": "polkadot",
    "AVAX": "avalanche-2",
    "MATIC": "matic-network",
}

_EMPTY_DF = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])


def _coingecko_id(symbol: str) -> str:
    return _SYMBOL_MAP.get(symbol.upper(), symbol.lower())


def _get_json(url: str, *, max_retries: int = 2) -> Any:
    """Fetch JSON with exponential backoff on 429. Returns None on failure."""
    headers = {"User-Agent": "BabyAI/TradingAgent 1.0"}
    delay = 5.0
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
            if exc.code == 429 and attempt < max_retries:
                _log.warning("rate_limit_hit url=%s attempt=%d sleeping=%.0fs", url, attempt, delay)
                time.sleep(delay)
                delay *= 2
                continue
            _log.warning("http_error url=%s status=%s", url, exc.code)
            return None
        except Exception as exc:
            _log.warning("fetch_failed url=%s error=%s", url, exc)
            return None
    return None


def _build_df_from_chart(prices: list, volumes: list) -> pd.DataFrame:
    """
    Convert market_chart price/volume arrays to DataFrame.
    OHLC is approximated from consecutive closes (paper trading only).
    """
    if not prices:
        return _EMPTY_DF.copy()

    rows = []
    prev_close: float | None = None
    for i, entry in enumerate(prices):
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        ts_ms = int(entry[0])
        close = float(entry[1])
        open_ = prev_close if prev_close is not None else close
        # Approximate high/low from open–close range + small spread
        high = max(open_, close) * 1.001
        low = min(open_, close) * 0.999
        vol = 0.0
        if i < len(volumes) and isinstance(volumes[i], list) and len(volumes[i]) >= 2:
            try:
                vol = float(volumes[i][1])
            except Exception:
                pass
        rows.append({
            "timestamp": pd.Timestamp(ts_ms, unit="ms", tz="UTC"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
        })
        prev_close = close

    if not rows:
        return _EMPTY_DF.copy()

    df = pd.DataFrame(rows)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def get_ohlcv(
    symbol: str,
    interval: str = "1d",
    limit: int = 90,
    vs_currency: str = "usd",
) -> pd.DataFrame:
    """
    Fetch OHLCV data for `symbol`.
    Returns DataFrame with columns: timestamp, open, high, low, close, volume.
    Fetches 365 days of daily data from CoinGecko market_chart, then slices to `limit` rows.
    Returns empty DataFrame on any unrecoverable error — never crashes.

    Args:
        symbol:      Crypto symbol (BTC, ETH, SOL, ...)
        interval:    Ignored (always daily via CoinGecko free tier)
        limit:       Max rows to return (most recent N rows)
        vs_currency: Quote currency (default: usd)
    """
    coin_id = _coingecko_id(symbol)
    params = urllib.parse.urlencode({
        "vs_currency": vs_currency,
        "days": "365",
        "interval": "daily",
    })
    url = f"{_COINGECKO_BASE}/coins/{coin_id}/market_chart?{params}"
    data = _get_json(url)

    if not isinstance(data, dict):
        _log.warning("get_ohlcv_no_data symbol=%s", symbol)
        return _EMPTY_DF.copy()

    prices = data.get("prices", [])
    volumes = data.get("total_volumes", [])

    df = _build_df_from_chart(prices, volumes)

    if len(df) < 30:
        _log.warning("get_ohlcv_low_rows symbol=%s rows=%d", symbol, len(df))

    # Return the most recent `limit` rows
    if limit and len(df) > limit:
        df = df.tail(limit).reset_index(drop=True)

    return df


def get_historical_ohlcv(
    symbol: str,
    days: int = 90,
    vs_currency: str = "usd",
) -> pd.DataFrame:
    """
    Fetch exactly `days` rows of daily OHLCV, sorted ascending by timestamp.
    Intended for backtesting — same data as get_ohlcv(limit=days).
    """
    return get_ohlcv(symbol, interval="1d", limit=days, vs_currency=vs_currency)


def get_current_price(symbol: str, vs_currency: str = "usd") -> Optional[float]:
    """
    Fetch current price for a crypto symbol.
    Returns None on failure.
    """
    coin_id = _coingecko_id(symbol)
    params = urllib.parse.urlencode({"ids": coin_id, "vs_currencies": vs_currency})
    url = f"{_COINGECKO_BASE}/simple/price?{params}"
    data = _get_json(url)
    if not isinstance(data, dict):
        return None
    try:
        return float(data[coin_id][vs_currency])
    except Exception:
        return None


def get_market_summary(symbol: str) -> dict[str, Any]:
    """Get a quick market summary: price, 24h change, market cap."""
    coin_id = _coingecko_id(symbol)
    params = urllib.parse.urlencode({
        "ids": coin_id,
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_market_cap": "true",
    })
    url = f"{_COINGECKO_BASE}/simple/price?{params}"
    data = _get_json(url)
    if not isinstance(data, dict):
        return {}
    return dict(data.get(coin_id, {}))
