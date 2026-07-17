#!/usr/bin/env python
"""
BabyAI Valuta Arbitrage Challenge Engine
==========================================
Kører paper-trading valuta-agenter med live ECB-kurser.
Indsamler træningsdata til LoRA pipeline.

Kør:
  python demo/challenge_engine.py --archetype valuta --rounds 3
  python demo/challenge_engine.py --archetype valuta --rounds unlimited
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Sørg for at project root er på sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demo.executors.valuta_executor import MONITORED_CURRENCIES, ValutaExecutor
from policy.valuta_challenge_policy import VALUTA_CHALLENGE_POLICY

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

AUDIT_LOG = Path("logs/challenge_audit.jsonl")
STATE_FILE = Path("logs/challenge_state.json")
ROUND_SLEEP_SEC = 30

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class PerformanceMetrics:
    rounds: int = 0
    wins: int = 0
    losses: int = 0
    total_return_pct: float = 0.0
    max_drawdown: float = 0.0
    _returns: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.0

    @property
    def sharpe(self) -> float:
        if len(self._returns) < 2:
            return 0.0
        mean = sum(self._returns) / len(self._returns)
        variance = sum((r - mean) ** 2 for r in self._returns) / len(self._returns)
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0:
            return 1.0 if mean > 0 else 0.0
        return round(mean / std, 3)

    def update(self, pnl_pct: float, capital_eur: float, peak_capital: float) -> None:
        self.rounds += 1
        self._returns.append(pnl_pct)
        self.total_return_pct = round(sum(self._returns), 4)
        if pnl_pct > 0:
            self.wins += 1
        else:
            self.losses += 1
        drawdown = (peak_capital - capital_eur) / peak_capital if peak_capital > 0 else 0.0
        self.max_drawdown = max(self.max_drawdown, drawdown)


@dataclass
class ChallengeAgent:
    id: str
    archetype: str
    capital_eur: float = 1.0
    status: str = "active"          # active | dormant | promoted
    trade_history: list = field(default_factory=list)
    performance: PerformanceMetrics = field(default_factory=PerformanceMetrics)
    _peak_capital: float = 1.0
    _consecutive_same_pair: int = 0
    _last_pair: str = ""
    _recent_l7_violation: bool = False
    _last_trade_id: Optional[str] = None


# ── Redis / state helpers ─────────────────────────────────────────────────────


def _get_redis():
    try:
        import redis as _redis  # type: ignore
        r = _redis.Redis(host="localhost", port=6379, db=0, socket_connect_timeout=1)
        r.ping()
        return r
    except Exception:
        return None


_redis_client = None
_redis_checked = False


def get_redis():
    global _redis_client, _redis_checked
    if not _redis_checked:
        _redis_client = _get_redis()
        _redis_checked = True
    return _redis_client


def save_state(state: dict) -> None:
    """Gem state til Redis + JSON fil fallback."""
    payload = json.dumps(state, ensure_ascii=False)
    r = get_redis()
    if r is not None:
        try:
            r.set("challenge:valuta:state", payload)
        except Exception:
            pass
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(payload, encoding="utf-8")


def audit(agent_id: str, level: str, msg: str, data: dict | None = None) -> None:
    """Skriv til audit log JSONL."""
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        "agent": agent_id,
        "level": level,
        "msg": msg,
    }
    if data:
        entry["data"] = data
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── Agent reasoning ───────────────────────────────────────────────────────────


def analyze_rates(
    rates: dict[str, float],
    yesterday_rates: dict[str, float],
) -> list[dict]:
    """
    Beregn signals for alle valutapar.

    Returns liste af pair-analyser sorteret efter opportunity score.
    """
    signals = []
    for currency in MONITORED_CURRENCIES:
        rate = rates.get(currency)
        yesterday = yesterday_rates.get(currency)
        if rate is None or yesterday is None or yesterday <= 0:
            continue

        momentum_24h = (rate - yesterday) / yesterday
        # Spread proxy baseret på momentum styrke
        spread_pct = max(0.0008, min(0.005, abs(momentum_24h) * 0.8 + 0.001))

        signals.append({
            "currency": currency,
            "pair": f"{currency}/EUR",
            "rate": rate,
            "yesterday": yesterday,
            "momentum_24h": round(momentum_24h, 6),
            "spread_pct": round(spread_pct, 6),
            "opportunity": abs(momentum_24h) * (1.0 if spread_pct >= 0.0015 else 0.1),
        })

    return sorted(signals, key=lambda x: x["opportunity"], reverse=True)


def build_decision(
    agent: ChallengeAgent,
    signals: list[dict],
    round_num: int,
) -> tuple[dict, dict]:
    """
    Byg agent decision baseret på signals.

    Returns: (decision_dict, signal_used)
    """
    # FIX 3: Diversification bonus — boost par der ikke er handlet i 10 runder
    recently_traded = {
        h["pair"].split("/")[0]
        for h in agent.trade_history[-10:]
        if h.get("action", "hold") != "hold"
    }
    scored_signals = []
    for sig in signals:
        bonus = 0.0 if sig["currency"] in recently_traded else 0.15
        scored_signals.append({**sig, "opportunity": sig["opportunity"] + bonus})
    scored_signals.sort(key=lambda x: x["opportunity"], reverse=True)

    # Vælg bedste opportunity der opfylder min_spread
    best_signal = None
    for sig in scored_signals:
        if sig["spread_pct"] >= 0.0015:  # min_spread_pct
            best_signal = sig
            break

    if best_signal is None:
        # Ingen god trade — hold
        return {
            "agent_id": agent.id,
            "round": round_num,
            "pair": "HOLD",
            "action": "hold",
            "position_eur": 0.0,
            "position_pct": 0.0,
            "spread_pct": 0.0,
            "signal": "no_opportunity",
            "reasoning": "Ingen valutapar opfylder minimum spread threshold på 0.15%.",
            "confidence": 0.1,
            "consecutive_same_pair": 0,
        }, {}

    currency = best_signal["currency"]
    momentum = best_signal["momentum_24h"]
    spread_pct = best_signal["spread_pct"]

    # Retning
    if momentum > 0:
        action = f"buy_{currency.lower()}_sell_eur"
        direction_word = "styrkelse"
    else:
        action = f"sell_{currency.lower()}_buy_eur"
        direction_word = "svækkelse"

    # Position: 25% base, boost ved høj momentum
    position_pct = min(0.30, 0.20 + abs(momentum) * 5)
    position_eur = round(agent.capital_eur * position_pct, 4)

    # Konsekutiv tæller
    if currency == agent._last_pair:
        consec = agent._consecutive_same_pair + 1
    else:
        consec = 1

    # Confidence
    confidence = min(0.92, 0.45 + abs(momentum) * 40 + spread_pct * 20)

    # DKK/EUR korrelation note
    dkk_note = ""
    if currency == "DKK":
        dkk_note = " DKK følger EUR tæt (korrelation 0.998) men kan lagge."

    reasoning = (
        f"{currency} {direction_word} {momentum*100:+.2f}% mod EUR på 24h. "
        f"Spread opportunity: {spread_pct*100:.3f}% over min threshold.{dkk_note} "
        f"Position {position_pct:.0%} under max 30%. "
        f"Kapital: {agent.capital_eur:.4f} EUR."
    )

    decision = {
        "agent_id": agent.id,
        "round": round_num,
        "pair": f"{currency}/EUR",
        "action": action,
        "position_eur": position_eur,
        "position_pct": round(position_pct, 4),
        "spread_pct": round(spread_pct, 6),
        "signal": f"momentum_{'bullish' if momentum > 0 else 'bearish'}",
        "reasoning": reasoning,
        "confidence": round(confidence, 3),
        "consecutive_same_pair": consec,
    }
    return decision, best_signal


def run_round(
    agents: list[ChallengeAgent],
    executor: ValutaExecutor,
    yesterday_rates: dict[str, float],
    round_num: int,
    collector=None,
    prev_trade_ids: dict[str, str] | None = None,
) -> tuple[dict[str, float], dict]:
    """
    Kør én runde for alle aktive agenter.

    Returns: (current_rates, round_summary)
    """
    rates = executor.fetch_rates()
    signals = analyze_rates(rates, yesterday_rates)

    # Injicér momentum signals i rates til brug af executor
    for sig in signals:
        rates[f"_momentum_{sig['currency']}"] = sig["momentum_24h"]

    round_summary = {
        "round": round_num,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agents": {},
        "rates": {k: v for k, v in rates.items() if not k.startswith("_")},
    }

    for agent in agents:
        if agent.status != "active":
            continue

        # DECIDE
        decision, signal_used = build_decision(agent, signals, round_num)

        # VALIDATE
        validation = VALUTA_CHALLENGE_POLICY.validate_decision(decision)
        if not validation["ok"]:
            level = "L7" if validation.get("l7") else "WARN"
            audit(
                agent.id, level,
                f"Policy violation: {validation['violation_text']}",
                {"decision": decision, "validation": validation},
            )
            if validation.get("l7"):
                agent._recent_l7_violation = True

            # FIX 2: consecutive_pair L7 — reset counter og vælg næstbedste par
            if validation.get("violation") == "consecutive_pair_limit":
                agent._consecutive_same_pair = 0
                blocked_currency = decision["pair"].split("/")[0]
                alt_signals = [s for s in signals if s["currency"] != blocked_currency]
                if alt_signals:
                    decision, signal_used = build_decision(agent, alt_signals, round_num)
                else:
                    decision["action"] = "hold"
                    decision["position_eur"] = 0.0
                    decision["position_pct"] = 0.0
            elif decision["action"] != "hold":
                decision["action"] = "hold"
                decision["position_eur"] = 0.0
                decision["position_pct"] = 0.0

        # EXECUTE
        trade_result = {"executed": False, "net_pnl_eur": 0.0, "net_pnl_pct": 0.0}
        if decision["action"] != "hold" and decision["position_eur"] > 0:
            currency = decision["pair"].split("/")[0]
            trade_result = executor.simulate_trade(
                agent_id=agent.id,
                base=currency,
                quote="EUR",
                amount_eur=decision["position_eur"],
                action=decision["action"],
                rates=rates,
            )

        # UPDATE
        pnl_eur = float(trade_result.get("net_pnl_eur", 0.0))
        pnl_pct = float(trade_result.get("net_pnl_pct", 0.0))
        capital_before = agent.capital_eur
        agent.capital_eur = round(agent.capital_eur + pnl_eur, 6)
        agent._peak_capital = max(agent._peak_capital, agent.capital_eur)

        # Update consecutive pair tracker
        currency_traded = decision["pair"].split("/")[0]
        if currency_traded == agent._last_pair and decision["action"] != "hold":
            agent._consecutive_same_pair += 1
        elif decision["action"] != "hold":
            agent._consecutive_same_pair = 1
            agent._last_pair = currency_traded

        # Performance
        agent.performance.update(pnl_pct, agent.capital_eur, agent._peak_capital)

        # Stop-loss check
        if agent.capital_eur < 0.20:
            agent.status = "dormant"
            audit(agent.id, "WARN", f"Stop-loss aktiveret — kapital {agent.capital_eur:.4f} EUR")

        # Trade history
        history_entry = {
            "round": round_num,
            "capital_eur": agent.capital_eur,
            "pair": decision["pair"],
            "action": decision["action"],
            "pnl_eur": round(pnl_eur, 6),
            "pnl_pct": round(pnl_pct, 6),
        }
        agent.trade_history.append(history_entry)

        # TRAINING DATA — record
        trade_id = None
        if collector is not None and decision["action"] != "hold":
            ctx = {
                "rates": {k: v for k, v in rates.items() if not k.startswith("_")},
                "pair": decision["pair"],
                "momentum_24h": signal_used.get("momentum_24h", 0.0),
                "spread_pct": decision["spread_pct"],
                "agent_capital_eur": capital_before,
                "market_conditions": _infer_market_conditions(signals),
            }
            trade_id = collector.record(
                context=ctx,
                reasoning=decision["reasoning"],
                decision={
                    "action": decision["action"],
                    "position_pct": decision["position_pct"],
                    "confidence": decision["confidence"],
                },
            )

        # TRAINING DATA — update previous round outcome
        if collector is not None and prev_trade_ids and agent.id in prev_trade_ids:
            prev_id = prev_trade_ids[agent.id]
            if prev_id:
                collector.update_outcome(
                    prev_id,
                    pnl_pct=pnl_pct,
                    capital_after=agent.capital_eur,
                    success=pnl_eur > 0,
                )

        agent._last_trade_id = trade_id

        # AUDIT
        change_pct = (agent.capital_eur - 1.0) / 1.0 * 100
        audit(
            agent.id, "AUDIT",
            f"{decision['pair']} {decision['action']} · "
            f"{pnl_pct*100:+.3f}% · "
            f"kapital {capital_before:.4f}→{agent.capital_eur:.4f} EUR",
            {"round": round_num, "decision": decision},
        )

        round_summary["agents"][agent.id] = {
            "capital_eur": agent.capital_eur,
            "status": agent.status,
            "pair": decision["pair"],
            "action": decision["action"],
            "pnl_pct": round(pnl_pct, 6),
            "win_rate": round(agent.performance.win_rate, 3),
            "sharpe": round(agent.performance.sharpe, 3),
            "wins": agent.performance.wins,
            "losses": agent.performance.losses,
            "trade_id": trade_id,
            "lastReason": decision["reasoning"][:80],
        }

    return rates, round_summary


def _infer_market_conditions(signals: list[dict]) -> str:
    """Udled markedsforhold fra signals."""
    if not signals:
        return "unknown"
    top = signals[0] if signals else {}
    momentum = top.get("momentum_24h", 0)
    currency = top.get("currency", "")
    if abs(momentum) > 0.008:
        return f"EUR_volatile"
    if momentum > 0.003:
        return f"{currency}_bullish"
    if momentum < -0.003:
        return f"{currency}_bearish"
    return "low_volatility"


# ── Print helpers ─────────────────────────────────────────────────────────────


def print_round_summary(round_num: int, agents: list[ChallengeAgent], round_data: dict) -> None:
    """Print runde-summary i formateret tabel."""
    ts = datetime.now().strftime("%H:%M:%S")
    print()
    print("======================================================")
    print(f"  RUNDE {round_num:04d} · {ts}")
    print("======================================================")

    for agent in agents:
        ag_data = round_data.get("agents", {}).get(agent.id, {})
        change_pct = (agent.capital_eur - 1.0) / 1.0 * 100
        arrow = "^" if change_pct >= 0 else "v"
        pair = ag_data.get("pair", "HOLD")
        pnl = ag_data.get("pnl_pct", 0) * 100
        w = agent.performance.wins
        l = agent.performance.losses
        sharpe = agent.performance.sharpe
        status_tag = f" [{agent.status.upper()}]" if agent.status != "active" else ""
        print(
            f"  {agent.id:<10} | {agent.capital_eur:>7.4f} EUR | "
            f"{arrow} {change_pct:>+6.2f}% | W:{w} L:{l} | "
            f"Sharpe: {sharpe:.2f}{status_tag}"
        )

    # Bedste trade denne runde
    best_agent = None
    best_pnl = -999
    for aid, ag_data in round_data.get("agents", {}).items():
        if ag_data.get("pnl_pct", 0) > best_pnl:
            best_pnl = ag_data["pnl_pct"]
            best_agent = (aid, ag_data)

    if best_agent and best_pnl != 0:
        aid, ag_data = best_agent
        print(
            f"  Bedste trade: {ag_data.get('pair','?')} "
            f"{best_pnl*100:+.3f}% · "
            f"{ag_data.get('action','?')}"
        )
    print("======================================================")


# ── Yesterday rates ───────────────────────────────────────────────────────────


def get_yesterday_rates(executor: ValutaExecutor) -> dict[str, float]:
    """
    Hent gårsdagens kurser fra Redis/memory cache.

    Fallback: tilføj ±0.3% random variation til dagens kurser
    (realistisk simulering af daglig bevægelse).
    """
    r = get_redis()
    if r is not None:
        try:
            raw = r.get("valuta:yesterday_rates")
            if raw:
                return json.loads(raw)
        except Exception:
            pass

    # Ingen gårsdagens kurser — simuler via variation
    today = executor.fetch_rates()
    yesterday: dict[str, float] = {}
    for currency, rate in today.items():
        if currency.startswith("_"):
            continue
        # Typisk ±0.5% daglig bevægelse
        drift = random.gauss(0, 0.003)
        yesterday[currency] = round(rate / (1 + drift), 6)
    return yesterday


def store_today_as_yesterday(rates: dict[str, float]) -> None:
    """Gem dagens kurser som gårsdagens kurser til næste session."""
    clean = {k: v for k, v in rates.items() if not k.startswith("_")}
    r = get_redis()
    if r is not None:
        try:
            r.setex("valuta:yesterday_rates", 86400 * 2, json.dumps(clean))
        except Exception:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────


async def main(archetype: str, max_rounds: Optional[int]) -> None:
    from babyai.lora.training_data_collector import TrainingDataCollector
    from babyai.lora.promotion_manager import PromotionManager

    print()
    print("======================================================")
    print(f"  BabyAI Valuta Arbitrage Challenge")
    print(f"  Arketype: {archetype}  |  Rounds: {max_rounds or 'unlimited'}")
    print(f"  Policy: {VALUTA_CHALLENGE_POLICY.name} v{VALUTA_CHALLENGE_POLICY.version}")
    print(f"  Mode: {'PAPER TRADING' if VALUTA_CHALLENGE_POLICY.paper_trading_mode else 'REAL'}")
    print("======================================================")

    executor = ValutaExecutor()
    collector = TrainingDataCollector(archetype=archetype)
    promoter = PromotionManager()

    # Opret én agent
    agents = [
        ChallengeAgent(id="agent_01", archetype=archetype),
    ]

    print(f"\n  {len(agents)} agent(er) oprettet med 1.00 EUR kapital")
    print(f"  Henter ECB kurser og gårsdagens baseline...\n")

    yesterday_rates = get_yesterday_rates(executor)
    prev_trade_ids: dict[str, str] = {}

    round_num = 0
    while True:
        round_num += 1
        active_agents = [a for a in agents if a.status == "active"]
        if not active_agents:
            print("\n  Alle agenter er dormant eller promoted. Stopper.")
            break

        current_rates, round_data = run_round(
            agents=active_agents,
            executor=executor,
            yesterday_rates=yesterday_rates,
            round_num=round_num,
            collector=collector,
            prev_trade_ids=prev_trade_ids,
        )

        # Gem nye trade IDs til næste runde
        for aid, ag_data in round_data.get("agents", {}).items():
            prev_trade_ids[aid] = ag_data.get("trade_id") or ""

        print_round_summary(round_num, agents, round_data)

        # Gem state
        state = {
            "round": round_num,
            "archetype": archetype,
            "agents": {
                a.id: {
                    "capital_eur": a.capital_eur,
                    "capital_dkk": round(a.capital_eur * current_rates.get("DKK", 7.46), 4),
                    "status": a.status,
                    "win_rate": round(a.performance.win_rate, 4),
                    "sharpe": round(a.performance.sharpe, 4),
                    "history": a.trade_history[-100:],  # max 100 entries
                    "lastReason": round_data["agents"].get(a.id, {}).get("lastReason", ""),
                }
                for a in agents
            },
            "current_rates": {k: v for k, v in current_rates.items() if not k.startswith("_")},
        }
        save_state(state)
        store_today_as_yesterday(current_rates)

        # Promotion check (hver 10. runde)
        if round_num % 10 == 0:
            for agent in active_agents:
                result = promoter.check_promotion_eligibility(agent, VALUTA_CHALLENGE_POLICY)
                if result.eligible:
                    agent.status = "promoted"
                    perf_summary = {
                        "capital_eur": agent.capital_eur,
                        "win_rate": agent.performance.win_rate,
                        "sharpe": agent.performance.sharpe,
                        "rounds": agent.performance.rounds,
                    }
                    promoter.request_promotion_approval(agent.id, result, perf_summary)

        # Training data stats
        if round_num % 5 == 0:
            stats = collector.get_stats()
            print(
                f"  Training data: {stats['total_examples']} eksempler, "
                f"{stats['with_outcomes']} med outcomes"
            )

        if max_rounds is not None and round_num >= max_rounds:
            print(f"\n  Nåede max {max_rounds} runder. Stopper.")
            break

        if max_rounds is None or max_rounds > 3:
            time.sleep(ROUND_SLEEP_SEC)

    # Slut-rapport
    print()
    print("======================================================")
    print("  FINAL RAPPORT")
    print("======================================================")
    for agent in agents:
        change_pct = (agent.capital_eur - 1.0) * 100
        print(
            f"  {agent.id}: {agent.capital_eur:.6f} EUR "
            f"({change_pct:+.4f}%) "
            f"W:{agent.performance.wins} L:{agent.performance.losses} "
            f"Sharpe:{agent.performance.sharpe:.3f} "
            f"[{agent.status.upper()}]"
        )

    stats = collector.get_stats()
    print(f"\n  Training data gemt: {stats['total_examples']} eksempler")
    print(f"  Audit log: {AUDIT_LOG}")
    print(f"  State: {STATE_FILE}")
    print()


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description="BabyAI Valuta Arbitrage Challenge")
    parser.add_argument(
        "--archetype",
        type=str,
        default="valuta",
        help="Agent arketype (default: valuta)",
    )
    parser.add_argument(
        "--rounds",
        type=str,
        default="unlimited",
        help="Antal runder (f.eks. 3, 60, unlimited)",
    )
    args = parser.parse_args()

    max_rounds = None
    if args.rounds.lower() != "unlimited":
        try:
            max_rounds = int(args.rounds)
        except ValueError:
            print(f"Ugyldigt --rounds argument: {args.rounds}")
            sys.exit(1)

    asyncio.run(main(archetype=args.archetype, max_rounds=max_rounds))
