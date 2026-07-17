"""
tools.crypto_intel — market intelligence data layer for BabyAI agents.

Clients:
  CoinGeckoClient      free, no key
  BinancePublicClient  free, no key
  WhaleAlertClient     free tier, needs WHALE_ALERT_API_KEY in env
  CryptoIntelAggregator  combines all sources into unified snapshots
"""
from tools.crypto_intel.coingecko_client import CoinGeckoClient
from tools.crypto_intel.binance_public_client import BinancePublicClient
from tools.crypto_intel.whale_alert_client import WhaleAlertClient
from tools.crypto_intel.aggregator import CryptoIntelAggregator

__all__ = [
    "CoinGeckoClient",
    "BinancePublicClient",
    "WhaleAlertClient",
    "CryptoIntelAggregator",
]
