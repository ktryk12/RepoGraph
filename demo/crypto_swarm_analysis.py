#!/usr/bin/env python
"""
BabyAI Crypto Investment Swarm Analysis
========================================
Henter live data fra CoinGecko og kører tre uafhængige swarms:
  - Swarm A: 40 daytrading-eksperter
  - Swarm B: 40 hold-investorer
  - Swarm C: 20 risiko-analytikere

Kør:
  python demo/crypto_swarm_analysis.py
  python demo/crypto_swarm_analysis.py --coins bitcoin,ethereum,solana
  python demo/crypto_swarm_analysis.py --top 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

# Ensure project root is on path regardless of where the script is launched from
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


# ─── Data hentning ─────────────────────────────────────────────────────────────


def fetch_json(url: str, timeout: int = 15, _retries: int = 0) -> dict:
    """Simpel HTTP GET — returnerer parsed JSON. Håndtér rate-limit (429)."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "BabyAI-Research/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 429 and _retries < 3:
            print("      Rate limit — venter 10s...")
            time.sleep(10)
            return fetch_json(url, timeout, _retries + 1)
        print(f"      HTTP fejl {e.code}: {url}")
        return {}
    except Exception as exc:
        print(f"      Fetch fejl: {exc}")
        return {}


def fetch_top_coins(n: int = 15) -> list[dict]:
    """Hent top N coins fra CoinGecko markets endpoint."""
    print(f"      Henter top {n} coins fra CoinGecko...")
    url = (
        f"{COINGECKO_BASE}/coins/markets"
        f"?vs_currency=usd"
        f"&order=market_cap_desc"
        f"&per_page={n}"
        f"&page=1"
        f"&sparkline=false"
        f"&price_change_percentage=1h,24h,7d,30d"
    )
    data = fetch_json(url)
    if not data or not isinstance(data, list):
        print("      Bruger fallback data...")
        return _fallback_coins()
    return data


def fetch_global_market() -> dict:
    """Hent global crypto market data."""
    url = f"{COINGECKO_BASE}/global"
    return fetch_json(url).get("data", {})


def fetch_trending() -> list[str]:
    """Returner trending coin IDs (op til 5)."""
    url = f"{COINGECKO_BASE}/search/trending"
    data = fetch_json(url)
    coins = data.get("coins", []) if isinstance(data, dict) else []
    return [c["item"]["id"] for c in coins[:5] if isinstance(c, dict) and "item" in c]


def _fallback_coins() -> list[dict]:
    """Statisk fallback hvis CoinGecko er nede."""
    print("      ADVARSEL: Bruger statisk fallback data (ingen live connection)")
    return [
        {
            "id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
            "current_price": 65000, "market_cap": 1280000000000,
            "market_cap_rank": 1, "total_volume": 28000000000,
            "price_change_percentage_1h_in_currency": 0.3,
            "price_change_percentage_24h": 1.2,
            "price_change_percentage_7d_in_currency": 4.5,
            "price_change_percentage_30d_in_currency": 12.0,
            "high_24h": 66200, "low_24h": 64100, "_fallback": True,
        },
        {
            "id": "ethereum", "symbol": "eth", "name": "Ethereum",
            "current_price": 3400, "market_cap": 408000000000,
            "market_cap_rank": 2, "total_volume": 15000000000,
            "price_change_percentage_1h_in_currency": -0.1,
            "price_change_percentage_24h": 0.8,
            "price_change_percentage_7d_in_currency": 3.2,
            "price_change_percentage_30d_in_currency": 8.5,
            "high_24h": 3450, "low_24h": 3350, "_fallback": True,
        },
        {
            "id": "solana", "symbol": "sol", "name": "Solana",
            "current_price": 145, "market_cap": 65000000000,
            "market_cap_rank": 5, "total_volume": 3200000000,
            "price_change_percentage_1h_in_currency": 0.7,
            "price_change_percentage_24h": 2.8,
            "price_change_percentage_7d_in_currency": 8.1,
            "price_change_percentage_30d_in_currency": 22.0,
            "high_24h": 148, "low_24h": 141, "_fallback": True,
        },
    ]


# ─── Signal beregning ──────────────────────────────────────────────────────────


def compute_daytrading_score(coin: dict) -> float:
    """Beregn daytrading-egnethed som score -1.0 til 1.0."""
    price = coin.get("current_price", 0) or 0
    if price <= 0:
        return 0.0

    market_cap = coin.get("market_cap", 1) or 1
    volume = coin.get("total_volume", 0) or 0
    change_24h = abs(coin.get("price_change_percentage_24h", 0) or 0)
    high = coin.get("high_24h", price) or price
    low = coin.get("low_24h", price) or price

    # Likviditet (volumen/marketcap)
    vol_ratio = min(volume / market_cap, 0.5)
    vol_score = min(0.4, (vol_ratio / 0.1) * 0.4)

    # Bevægelses-score (5-10% er optimalt)
    if 3 <= change_24h <= 10:
        move_score = 0.3
    elif 1 <= change_24h < 3:
        move_score = 0.15
    elif change_24h > 15:
        move_score = -0.2
    else:
        move_score = 0.0

    # Intradag spread
    if price > 0:
        spread_pct = (high - low) / price * 100
        spread_score = 0.2 if 2 <= spread_pct <= 8 else (-0.1 if spread_pct > 15 else 0.1)
    else:
        spread_score = 0.0

    return max(-1.0, min(1.0, (vol_score + move_score + spread_score) * 1.5))


def compute_hold_score(coin: dict) -> float:
    """Beregn hold-investeringsegnethed som score -1.0 til 1.0."""
    market_cap = coin.get("market_cap", 0) or 0
    change_7d = coin.get("price_change_percentage_7d_in_currency", 0) or 0
    change_30d = coin.get("price_change_percentage_30d_in_currency", 0) or 0
    change_24h = abs(coin.get("price_change_percentage_24h", 0) or 0)

    if market_cap > 100_000_000_000:
        cap_score = 0.3
    elif market_cap > 10_000_000_000:
        cap_score = 0.2
    elif market_cap > 1_000_000_000:
        cap_score = 0.1
    else:
        cap_score = -0.1

    if change_30d > 20:
        trend_score = 0.35
    elif change_30d > 5:
        trend_score = 0.2
    elif change_30d > 0:
        trend_score = 0.1
    elif change_30d > -10:
        trend_score = -0.1
    else:
        trend_score = -0.3

    week_score = 0.15 if change_7d > 5 else (0.05 if change_7d > 0 else -0.1)
    vol_penalty = -0.1 if change_24h > 10 else 0.0

    return max(-1.0, min(1.0, (cap_score + trend_score + week_score + vol_penalty) * 1.3))


def compute_risk_score(coin: dict) -> float:
    """Beregn risiko som score 1-10 (10 = højest risiko)."""
    market_cap = coin.get("market_cap", 0) or 0
    change_24h = abs(coin.get("price_change_percentage_24h", 0) or 0)
    change_7d = abs(coin.get("price_change_percentage_7d_in_currency", 0) or 0)
    rank = coin.get("market_cap_rank", 100) or 100

    risk = 5.0
    if market_cap > 100_000_000_000:
        risk -= 2.0
    elif market_cap > 10_000_000_000:
        risk -= 1.0

    if change_24h > 10:
        risk += 2.0
    elif change_24h > 5:
        risk += 1.0

    if change_7d > 20:
        risk += 1.5
    elif change_7d > 10:
        risk += 0.5

    if rank > 50:
        risk += 1.5
    elif rank > 20:
        risk += 0.5

    return max(1.0, min(10.0, risk))


# ─── Swarm kørsel ──────────────────────────────────────────────────────────────


def _build_crypto_configs(
    n_agents: int,
    base_opinion: float,
    archetype: str,
) -> list:
    """Byg NanoAgentConfig liste med variation omkring base_opinion."""
    from services.swarm.nano_agent import NanoAgentConfig

    if archetype == "daytrading":
        drift_weights = [0.15, 0.08, 0.20, 0.05]
    elif archetype == "hold":
        drift_weights = [0.07, 0.10, 0.06, 0.09]
    else:  # risk
        drift_weights = [0.05, 0.08]

    configs = []
    for i in range(n_agents):
        weight = drift_weights[i % len(drift_weights)]
        variation = random.uniform(-0.15, 0.15)
        start_op = max(-1.0, min(1.0, base_opinion + variation))
        cfg = NanoAgentConfig(
            agent_id=f"{archetype}_{i}_{uuid.uuid4().hex[:6]}",
            rules=[
                {"type": "opinion_drift", "weight": weight},
                {"type": "energy_decay", "rate": 0.003},
                {"type": "group_join", "threshold": 0.2},
            ],
            initial_state={"opinion": start_op},
        )
        configs.append(cfg)
    return configs


async def _run_swarm(
    n_agents: int,
    base_opinion: float,
    world_state: dict,
    archetype: str,
    ticks: int,
) -> float:
    """Kør én swarm og returner final gennemsnitlig opinion."""
    from services.swarm.swarm_runtime import SwarmRuntime

    configs = _build_crypto_configs(n_agents, base_opinion, archetype)
    runtime = SwarmRuntime(
        configs,
        observer_consumer=None,
        emergence_interval=ticks + 1,
    )
    runtime.inject_world_state(world_state)
    await runtime.run(max_ticks=ticks)

    snap = runtime.get_state_snapshot()
    opinions = [v["opinion"] for v in snap.values()]
    return sum(opinions) / len(opinions) if opinions else 0.0


async def run_coin_analysis(coin: dict, trending_ids: list[str]) -> dict:
    """Kør tre swarms for én coin og returner komplet analyse-dict."""
    coin_id = coin.get("id", "unknown")
    symbol = (coin.get("symbol") or "?").upper()
    is_trending = coin_id in trending_ids

    day_base = compute_daytrading_score(coin)
    hold_base = compute_hold_score(coin)
    risk_base = compute_risk_score(coin)

    world_state: dict[str, Any] = {
        "coin_id": coin_id,
        "symbol": symbol,
        "price": coin.get("current_price", 0),
        "market_cap": coin.get("market_cap", 0),
        "rank": coin.get("market_cap_rank", 0),
        "change_1h": coin.get("price_change_percentage_1h_in_currency", 0),
        "change_24h": coin.get("price_change_percentage_24h", 0),
        "change_7d": coin.get("price_change_percentage_7d_in_currency", 0),
        "change_30d": coin.get("price_change_percentage_30d_in_currency", 0),
        "volume": coin.get("total_volume", 0),
        "is_trending": is_trending,
        "day_signal": day_base,
        "hold_signal": hold_base,
    }

    day_score = await _run_swarm(40, day_base, world_state, "daytrading", 40)
    hold_score = await _run_swarm(40, hold_base, world_state, "hold", 40)
    risk_raw = await _run_swarm(20, (5.0 - risk_base) / 5.0, world_state, "risk", 30)
    risk_final = round(max(1.0, min(10.0, (1 - risk_raw) * 5 + 5)), 1)

    return {
        "coin": coin,
        "symbol": symbol,
        "is_trending": is_trending,
        "daytrading": round(day_score, 3),
        "hold": round(hold_score, 3),
        "risk": risk_final,
        "day_rating": _score_to_stars(day_score),
        "hold_rating": _score_to_stars(hold_score),
        "risk_label": _risk_label(risk_final),
    }


# ─── Hjælpefunktioner ──────────────────────────────────────────────────────────


def _score_to_stars(score: float) -> str:
    """Konvertér -1.0 til 1.0 til ASCII-stjernerating (cp1252-safe)."""
    stars = round((score + 1.0) / 2.0 * 5)
    stars = max(0, min(5, stars))
    return "*" * stars + "." * (5 - stars)


def _risk_label(risk: float) -> str:
    if risk <= 3:
        return "LAV"
    if risk <= 6:
        return "MEDIUM"
    if risk <= 8:
        return "HOJ"
    return "MEGET HOJ"


# ─── Memory + rapport ──────────────────────────────────────────────────────────


def save_results_to_memory(results: list[dict]) -> None:
    """Gem top picks til memory-plane (port 8101)."""
    try:
        urllib.request.urlopen("http://localhost:8101/health", timeout=3)
    except Exception:
        print("      memory-plane offline — springer over")
        return

    day_top = max(results, key=lambda r: r["daytrading"])
    hold_top = max(results, key=lambda r: r["hold"])

    for label, coin_result in [("daytrading", day_top), ("hold", hold_top)]:
        content = (
            f"Crypto {label} top pick: "
            f"{coin_result['symbol']} "
            f"score={coin_result[label]:.3f} "
            f"risk={coin_result['risk']} "
            f"({coin_result['risk_label']}). "
            f"Pris: ${coin_result['coin'].get('current_price', 0):,.2f}"
        )
        data = json.dumps(
            {
                "content": content,
                "source": "crypto_swarm_analysis",
                "entity_type": "outcome",
                "entity_id": f"crypto_{label}",
                "importance": 0.85,
            },
            ensure_ascii=True,
        ).encode()
        try:
            req = urllib.request.Request(
                "http://localhost:8101/memory/ingest",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
            print(f"      Gemt {label} top pick: {coin_result['symbol']}")
        except Exception as exc:
            print(f"      Gem fejlede: {exc}")


def _daytrading_rationale(r: dict) -> str:
    c = r["coin"]
    vol = c.get("total_volume", 0) or 0
    ch24 = c.get("price_change_percentage_24h", 0) or 0
    liq = "Meget hoj likviditet" if vol > 5e9 else ("God likviditet" if vol > 1e9 else "Moderat likviditet")
    volatility = "god" if abs(ch24) < 12 else "hoj"
    return f"{liq}, {abs(ch24):.1f}% daglig bevoegelse -> {volatility} volatilitet"


def _hold_rationale(r: dict) -> str:
    c = r["coin"]
    rank = c.get("market_cap_rank", 0) or 0
    ch30 = c.get("price_change_percentage_30d_in_currency", 0) or 0
    trend = " TRENDING" if r["is_trending"] else ""
    return f"Rank #{rank}, {ch30:+.1f}% pa 30 dage{trend}"


def print_full_report(results: list[dict], global_data: dict, trending: list[str]) -> None:
    """Print komplet professionel rapport."""
    print()
    print("=" * 65)
    print("BABYAI CRYPTO INTELLIGENCE RAPPORT")
    print("=" * 65)

    if global_data:
        total_mcap = global_data.get("total_market_cap", {}).get("usd", 0) or 0
        btc_dom = global_data.get("market_cap_percentage", {}).get("btc", 0) or 0
        change = global_data.get("market_cap_change_percentage_24h_usd", 0) or 0
        print(
            f"Global market cap: ${total_mcap / 1e12:.2f}T  "
            f"BTC dominans: {btc_dom:.1f}%  "
            f"24h aendring: {change:+.1f}%"
        )
        print()

    day_sorted = sorted(results, key=lambda r: r["daytrading"], reverse=True)
    hold_sorted = sorted(results, key=lambda r: r["hold"], reverse=True)

    print("DAYTRADING ANALYSE")
    print(f"{'Coin':<10} {'Pris':>12} {'24h':>7} {'Score':>7} {'Rating':<8} {'Risiko':<10}")
    print("-" * 65)
    for r in day_sorted:
        c = r["coin"]
        price = c.get("current_price", 0) or 0
        ch24 = c.get("price_change_percentage_24h", 0) or 0
        trend = " TREND" if r["is_trending"] else ""
        print(
            f"{r['symbol']:<10}"
            f" ${price:>11,.2f}"
            f" {ch24:>+6.1f}%"
            f" {r['daytrading']:>+7.3f}"
            f" {r['day_rating']:<8}"
            f" {r['risk_label']:<8}"
            f"{trend}"
        )

    print()
    print("HOLD-INVESTERING ANALYSE")
    print(f"{'Coin':<10} {'Pris':>12} {'30d':>7} {'Score':>7} {'Rating':<8} {'Risiko':<10}")
    print("-" * 65)
    for r in hold_sorted:
        c = r["coin"]
        price = c.get("current_price", 0) or 0
        ch30 = c.get("price_change_percentage_30d_in_currency", 0) or 0
        trend = " TREND" if r["is_trending"] else ""
        print(
            f"{r['symbol']:<10}"
            f" ${price:>11,.2f}"
            f" {ch30:>+6.1f}%"
            f" {r['hold']:>+7.3f}"
            f" {r['hold_rating']:<8}"
            f" {r['risk_label']:<8}"
            f"{trend}"
        )

    print()
    print("=" * 65)
    print("INTELLIGENCE COUNCIL — ANBEFALINGER")
    print("=" * 65)

    print()
    print("DAYTRADING (kort sigt — dage/timer):")
    for i, r in enumerate(day_sorted[:3], 1):
        c = r["coin"]
        ch24 = c.get("price_change_percentage_24h", 0) or 0
        vol = c.get("total_volume", 0) or 0
        print(
            f"  {i}. {r['symbol']} {r['day_rating']}"
            f"  Risiko: {r['risk_label']}"
            f"  ({ch24:+.1f}% 24h, vol ${vol / 1e6:.0f}M)"
        )
        print(f"     {_daytrading_rationale(r)}")

    print()
    print("HOLD-INVESTERING (lang sigt — maaneder/ar):")
    for i, r in enumerate(hold_sorted[:3], 1):
        c = r["coin"]
        ch30 = c.get("price_change_percentage_30d_in_currency", 0) or 0
        mcap = c.get("market_cap", 0) or 0
        print(
            f"  {i}. {r['symbol']} {r['hold_rating']}"
            f"  Risiko: {r['risk_label']}"
            f"  ({ch30:+.1f}% 30d, mcap ${mcap / 1e9:.0f}B)"
        )
        print(f"     {_hold_rationale(r)}")

    print()
    print("ADVARSEL: Dette er AI-genereret analyse")
    print("til demonstration af BabyAI swarm-teknologi.")
    print("Invester aldrig baseret paa AI-analyse alene.")
    print("=" * 65)


# ─── Main ──────────────────────────────────────────────────────────────────────


async def main(top_n: int, specific_coins: list[str]) -> None:
    print()
    print("=" * 65)
    print("BabyAI Crypto Swarm Analysis")
    print("=" * 65)

    # Trin 1: Hent markedsdata
    print()
    print("[1/4] Henter live crypto data...")
    coins = fetch_top_coins(top_n)
    global_data = fetch_global_market()
    trending = fetch_trending()
    if trending:
        print(f"      Trending: {', '.join(trending[:5])}")

    # Filtrer til specifikke coins hvis angivet
    if specific_coins:
        coins = [
            c for c in coins
            if c.get("id") in specific_coins
            or (c.get("symbol") or "").lower() in specific_coins
        ]
        print(f"      Filtreret til: {[c.get('symbol') for c in coins]}")

    if not coins:
        print("Ingen coins fundet — tjek connection og coin-navne")
        return

    print(f"      {len(coins)} coins hentet")

    # Trin 2: Tjek hukommelse
    print()
    print("[2/4] Tjekker langtidshukommelse...")
    try:
        from planner.memory_context import PlannerMemoryContext
        ctx = PlannerMemoryContext()
        memory = ctx.retrieve_for_episode(
            scenario="crypto_analysis",
            agent_context={"coins": len(coins)},
        )
        if memory["total_retrieved"] > 0:
            print(f"      Fandt {memory['total_retrieved']} tidligere analyser i hukommelsen")
        else:
            print("      Forste korsel — ingen historik endnu")
    except Exception:
        print("      Hukommelse ikke tilgaengelig (memory-plane offline?)")

    # Trin 3: Swarm analyse
    print()
    print(f"[3/4] Korer swarm analyse ({len(coins)} coins x 3 swarms)...")
    print(f"      Dette tager ca. {len(coins) * 3}–{len(coins) * 5} sekunder...")

    results = []
    for i, coin in enumerate(coins, 1):
        symbol = (coin.get("symbol") or "?").upper()
        sys.stdout.write(f"\r      [{i}/{len(coins)}] Analyserer {symbol}...    ")
        sys.stdout.flush()
        result = await run_coin_analysis(coin, trending)
        results.append(result)

    print(f"\r      Alle {len(coins)} coins analyseret                    ")

    # Trin 4: Gem og rapport
    print()
    print("[4/4] Gemmer til langtidshukommelse...")
    save_results_to_memory(results)

    print_full_report(results, global_data, trending)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BabyAI Crypto Swarm Analysis")
    parser.add_argument(
        "--top", type=int, default=15,
        help="Antal top coins at analysere (default 15)",
    )
    parser.add_argument(
        "--coins", type=str, default="",
        help="Specifikke coins: bitcoin,ethereum,solana",
    )
    args = parser.parse_args()

    # ── Security: valider coin IDs inden kørsel ──────────────────────────────
    from babyai.security.crypto_input_sanitizer import CryptoInputSanitizer

    specific: list[str] = (
        [c.strip().lower() for c in args.coins.split(",") if c.strip()]
        if args.coins else []
    )

    if specific:
        coin_check = CryptoInputSanitizer.sanitize_coin_ids(specific)
        if not coin_check["ok"]:
            CryptoInputSanitizer.log_violation(coin_check, f"coins={specific}")
            if coin_check["severity"] >= 0.95:
                print(f"SIKKERHED: Input afvist — {coin_check['violation']}")
                print("Korsel stoppet.")
                sys.exit(1)
            else:
                print(f"ADVARSEL: {coin_check['violation']}")
        specific = coin_check.get("clean", specific)
    # ────────────────────────────────────────────────────────────────────────────

    asyncio.run(main(args.top, specific))
