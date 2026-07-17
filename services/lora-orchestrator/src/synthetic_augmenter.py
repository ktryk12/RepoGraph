"""
SyntheticAugmenter — genererer syntetiske variationer af træningseksempler.

Bruges til at booste træningssættet uden at overfitte på få scenarier.
Ingen ML-afhængighed — ren Python.
"""
from __future__ import annotations

import json
import random
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MARKET_CONDITIONS = [
    "USD_bullish",
    "USD_bearish",
    "DKK_lagging",
    "EUR_volatile",
    "low_volatility",
    "high_spread_opportunity",
]


class SyntheticAugmenter:
    """
    Tag et rigtigt eksempel og generer N variationer.

    Bevarer reasoning-struktur men justerer tal konsistent med variationen.
    Vigtigt for at LoRA-modellen generaliserer i stedet for at memorere.
    """

    def __init__(self, archetype: str) -> None:
        self.archetype = str(archetype)

    def augment(self, example: dict[str, Any], n_variations: int = 10) -> list[dict[str, Any]]:
        """
        Generer n_variations syntetsiske variationer af et eksempel.

        Varierer:
          - momentum_24h: gaussian noise ±0.2%
          - spread_pct: uniform [0.10%, 0.40%]
          - agent_capital_eur: scale [0.5x, 2.0x]
          - market_conditions: random fra MARKET_CONDITIONS
        """
        variations = []
        for _ in range(n_variations):
            var = deepcopy(example)
            var["id"] = str(uuid.uuid4())
            var["timestamp"] = datetime.now(timezone.utc).isoformat()
            var["_synthetic"] = True

            ctx = var.get("context") or {}

            # Varier momentum
            base_momentum = float(ctx.get("momentum_24h", 0.002))
            new_momentum = base_momentum + random.gauss(0, 0.002)
            new_momentum = max(-0.015, min(0.015, new_momentum))
            ctx["momentum_24h"] = round(new_momentum, 6)

            # Varier spread
            ctx["spread_pct"] = round(random.uniform(0.001, 0.004), 6)

            # Varier kapital (scale)
            base_cap = float(ctx.get("agent_capital_eur", 1.0))
            scale = random.uniform(0.5, 2.0)
            new_cap = base_cap * scale
            ctx["agent_capital_eur"] = round(new_cap, 4)

            # Varier market_conditions
            ctx["market_conditions"] = random.choice(MARKET_CONDITIONS)

            # Opdater reasoning med nye tal
            dec = var.get("decision") or {}
            base_position = float(dec.get("position_pct", 0.25))
            # Skaler position med ny kapital relativt
            new_position_pct = min(0.30, base_position * random.uniform(0.7, 1.0))
            dec["position_pct"] = round(new_position_pct, 4)
            dec["position_eur"] = round(new_cap * new_position_pct, 4)

            # Opdater confidence baseret på momentum styrke
            confidence = min(0.95, 0.50 + abs(new_momentum) * 50)
            dec["confidence"] = round(confidence, 3)

            # Generer nyt reasoning der matcher de varierede tal
            pair = ctx.get("pair", "USD/DKK")
            mom_pct = new_momentum * 100
            spread_pct = ctx["spread_pct"] * 100
            condition = ctx["market_conditions"]
            var["reasoning"] = (
                f"{pair} momentum {mom_pct:+.2f}% på 24h. "
                f"Markedsforhold: {condition}. "
                f"Spread {spread_pct:.3f}% over min threshold. "
                f"Position {new_position_pct:.0%} under max 30%."
            )

            var["context"] = ctx
            var["decision"] = dec
            variations.append(var)

        return variations

    def generate_edge_cases(self, n: int = 50) -> list[dict[str, Any]]:
        """
        Generer bevidst grænsetilfælde.

        Disse er vigtige for at LoRA lærer policy-grænser:
          - Spread præcis på threshold (0.15%)
          - Position præcis på max (30%)
          - L7-trigger scenario (61%)
          - Stop-loss scenario (0.21 EUR)
          - Krise-scenario (alle par negative)
        """
        edge_cases = []
        templates = [
            self._edge_spread_threshold,
            self._edge_max_position,
            self._edge_l7_trigger,
            self._edge_stop_loss,
            self._edge_crisis,
        ]
        for i in range(n):
            fn = templates[i % len(templates)]
            edge_cases.append(fn())
        return edge_cases

    def save_augmented(
        self,
        examples: list[dict[str, Any]],
        output_path: str,
    ) -> None:
        """Gem augmenteret dataset til {archetype}_augmented.jsonl."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            for ex in examples:
                f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # ── Edge case generators ────────────────────────────────────────────────────

    def _base_example(self, label: str) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()),
            "archetype": self.archetype,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "_synthetic": True,
            "_edge_case": label,
            "context": {},
            "reasoning": "",
            "decision": {},
            "outcome": None,
        }

    def _edge_spread_threshold(self) -> dict[str, Any]:
        ex = self._base_example("spread_at_threshold")
        ex["context"] = {
            "pair": "USD/DKK",
            "momentum_24h": 0.003,
            "spread_pct": 0.0015,  # præcis 0.15%
            "agent_capital_eur": 1.0,
            "market_conditions": "low_volatility",
        }
        ex["reasoning"] = (
            "USD/DKK momentum +0.30% på 24h. "
            "Spread præcis 0.15% på threshold. "
            "Position 25% under max 30%. "
            "Minimal margin men inden for policy."
        )
        ex["decision"] = {
            "pair": "USD/DKK",
            "action": "buy_usd_sell_dkk",
            "position_pct": 0.25,
            "position_eur": 0.25,
            "confidence": 0.55,
        }
        return ex

    def _edge_max_position(self) -> dict[str, Any]:
        ex = self._base_example("max_position")
        ex["context"] = {
            "pair": "GBP/USD",
            "momentum_24h": 0.008,
            "spread_pct": 0.003,
            "agent_capital_eur": 2.5,
            "market_conditions": "USD_bullish",
        }
        ex["reasoning"] = (
            "GBP/USD stærk bullish momentum +0.80% på 24h. "
            "Høj spread opportunity 0.30%. "
            "Position sat til max tilladt 30% af kapital. "
            "Risikostyring: stop-loss aktiveres under 0.20 EUR."
        )
        ex["decision"] = {
            "pair": "GBP/USD",
            "action": "buy_gbp_sell_usd",
            "position_pct": 0.30,  # præcis max
            "position_eur": 0.75,
            "confidence": 0.78,
        }
        return ex

    def _edge_l7_trigger(self) -> dict[str, Any]:
        ex = self._base_example("l7_trigger_61pct")
        ex["context"] = {
            "pair": "EUR/JPY",
            "momentum_24h": 0.012,
            "spread_pct": 0.004,
            "agent_capital_eur": 5.0,
            "market_conditions": "EUR_volatile",
        }
        ex["reasoning"] = (
            "EUR/JPY ekstraordinær bevægelse +1.20%. "
            "Høj overbevisning — position 61% foreslået. "
            "ADVARSEL: Over L7 threshold → human approval påkrævet. "
            "Policy violation: max position 30%."
        )
        ex["decision"] = {
            "pair": "EUR/JPY",
            "action": "sell_jpy_buy_eur",
            "position_pct": 0.61,  # L7 trigger
            "position_eur": 3.05,
            "confidence": 0.91,
            "_policy_violation": "l7_large_position",
        }
        return ex

    def _edge_stop_loss(self) -> dict[str, Any]:
        ex = self._base_example("stop_loss_near")
        ex["context"] = {
            "pair": "NOK/SEK",
            "momentum_24h": -0.005,
            "spread_pct": 0.002,
            "agent_capital_eur": 0.21,  # tæt på stop-loss grænse
            "market_conditions": "USD_bearish",
        }
        ex["reasoning"] = (
            "Kapital 0.21 EUR nær stop-loss threshold 0.20 EUR. "
            "NOK/SEK negativ momentum -0.50%. "
            "Høj forsigtighed — minimal position 10%. "
            "Risiko for stop-loss aktivering ved tab."
        )
        ex["decision"] = {
            "pair": "NOK/SEK",
            "action": "sell_nok_buy_sek",
            "position_pct": 0.10,
            "position_eur": 0.021,
            "confidence": 0.42,
        }
        return ex

    def _edge_crisis(self) -> dict[str, Any]:
        ex = self._base_example("market_crisis")
        ex["context"] = {
            "pair": "CHF/USD",
            "momentum_24h": -0.015,
            "spread_pct": 0.0008,  # under threshold
            "agent_capital_eur": 1.5,
            "market_conditions": "EUR_volatile",
        }
        ex["reasoning"] = (
            "Krise-scenario: alle valutapar negative momentum. "
            "CHF/USD spread 0.08% under min threshold 0.15%. "
            "Ingen trade — policy spread requirement ikke opfyldt. "
            "Agent holder position og venter på normalisering."
        )
        ex["decision"] = {
            "pair": "CHF/USD",
            "action": "hold",
            "position_pct": 0.0,
            "position_eur": 0.0,
            "confidence": 0.30,
            "_policy_skip": "min_spread_pct",
        }
        return ex
