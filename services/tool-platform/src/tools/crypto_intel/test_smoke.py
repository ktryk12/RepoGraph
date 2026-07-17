"""
Smoke tests for tools.crypto_intel.

Run with:
    .venv/Scripts/python -m pytest tools/crypto_intel/test_smoke.py -v

These tests hit live APIs — they require internet access.
The whale-alert test is automatically skipped when WHALE_ALERT_API_KEY is not set.
"""
from __future__ import annotations

import os
import pytest

from tools.crypto_intel.coingecko_client import CoinGeckoClient
from tools.crypto_intel.binance_public_client import BinancePublicClient
from tools.crypto_intel.whale_alert_client import WhaleAlertClient
from tools.crypto_intel.aggregator import CryptoIntelAggregator


# ── Test 1: CoinGecko BTC price ───────────────────────────────────────────────

def test_coingecko_btc_price():
    """CoinGecko get_price('bitcoin') returns a float greater than 0."""
    client = CoinGeckoClient()
    result = client.get_price(["bitcoin"])

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "bitcoin" in result, f"'bitcoin' key missing from {result}"

    btc = result["bitcoin"]
    assert "usd" in btc, f"'usd' key missing from bitcoin entry: {btc}"

    price = float(btc["usd"])
    assert price > 0, f"BTC price should be > 0, got {price}"


# ── Test 2: Binance BTCUSDT ticker ───────────────────────────────────────────

def test_binance_btcusdt_ticker():
    """Binance get_price('BTCUSDT') returns a dict with a 'price' key."""
    client = BinancePublicClient()
    result = client.get_price("BTCUSDT")

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "price" in result, f"'price' key missing from {result}"

    price = float(result["price"])
    assert price > 0, f"BTCUSDT price should be > 0, got {price}"


# ── Test 3: Aggregator market snapshot ───────────────────────────────────────

def test_aggregator_market_snapshot():
    """Aggregator.get_market_snapshot() returns a dict with a 'coins' key."""
    agg = CryptoIntelAggregator()
    snapshot = agg.get_market_snapshot()

    assert isinstance(snapshot, dict), f"Expected dict, got {type(snapshot)}"
    assert "coins" in snapshot, f"'coins' key missing from snapshot: {list(snapshot.keys())}"
    assert isinstance(snapshot["coins"], list), "'coins' should be a list"
    assert "spot_prices" in snapshot, "'spot_prices' key missing"
    assert "whale_txns" in snapshot, "'whale_txns' key missing"


# ── Test 4: Whale Alert (skip if no key) ─────────────────────────────────────

@pytest.mark.skipif(
    not os.getenv("WHALE_ALERT_API_KEY"),
    reason="WHALE_ALERT_API_KEY not set — skipping whale-alert live test",
)
def test_whale_alert_returns_list():
    """WhaleAlertClient.get_recent_transactions() returns a list (may be empty)."""
    client = WhaleAlertClient()
    result = client.get_recent_transactions(min_value=1_000_000, limit=10)

    assert isinstance(result, list), f"Expected list, got {type(result)}"


def test_whale_alert_no_key_returns_empty():
    """WhaleAlertClient with no key gracefully returns an empty list."""
    client = WhaleAlertClient(api_key="")
    result = client.get_recent_transactions()
    assert result == [], f"Expected [], got {result}"
