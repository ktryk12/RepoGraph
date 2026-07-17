"""
babyai/trading/arbitrage.py — Triangular arbitrage detector.

Detects 3-leg EUR→A→B→EUR arbitrage opportunities from an ECB rates snapshot.
All rates are EUR-based (units of foreign currency per 1 EUR).

Usage:
    detector = ArbitrageDetector()
    opps = detector.detect(rates)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class ArbitrageOpportunity:
    path: List[str]               # e.g. ["EUR", "USD", "GBP", "EUR"]
    gross_return: float           # e.g. 1.0031 means 0.31% profit before costs
    net_return: float             # after transaction_cost_bps per leg
    detected_at: datetime
    rates_snapshot: Dict[str, float]


_DEFAULT_CURRENCIES = ["EUR", "USD", "GBP", "JPY", "CHF", "DKK"]


class ArbitrageDetector:
    """
    Detect triangular EUR-rooted arbitrage opportunities.

    Parameters
    ----------
    transaction_cost_bps:
        Cost per leg in basis points (1 bp = 0.01%). Default 2 bps.
    min_net_return:
        Minimum net return to include in results. Default 1.001 (0.1%).
    currencies:
        Currency universe. Defaults to EUR + 5 majors.
    """

    def __init__(
        self,
        transaction_cost_bps: float = 2.0,
        min_net_return: float = 1.001,
        currencies: Optional[List[str]] = None,
    ) -> None:
        self._cost_bps  = transaction_cost_bps
        self._min_net   = min_net_return
        self._currencies = currencies if currencies is not None else _DEFAULT_CURRENCIES

    # ── Public ─────────────────────────────────────────────────────────────────

    def detect(self, rates: Dict[str, float]) -> List[ArbitrageOpportunity]:
        """
        Detect triangular arbitrage opportunities.

        Only considers paths: EUR → A → B → EUR (A ≠ B, neither is EUR).
        Rates dict must have entries for non-EUR currencies (EUR/XXX).

        gross_return = rate(EUR→A) * rate(A→B) * rate(B→EUR)
        net_return   = gross_return * (1 - cost_bps/10_000)^3

        Returns opportunities where net_return >= min_net_return,
        sorted by net_return descending.
        """
        now = datetime.utcnow()
        non_eur = [c for c in self._currencies if c != "EUR" and c in rates]
        per_leg_factor = 1.0 - self._cost_bps / 10_000.0

        results: List[ArbitrageOpportunity] = []

        for a in non_eur:
            for b in non_eur:
                if a == b:
                    continue

                try:
                    eur_to_a  = self._cross_rate("EUR", a, rates)   # EUR → A
                    a_to_b    = self._cross_rate(a, b, rates)        # A   → B
                    b_to_eur  = self._cross_rate(b, "EUR", rates)    # B   → EUR
                except (KeyError, ZeroDivisionError):
                    continue

                gross = eur_to_a * a_to_b * b_to_eur
                net   = gross * (per_leg_factor ** 3)

                if net >= self._min_net:
                    results.append(
                        ArbitrageOpportunity(
                            path=["EUR", a, b, "EUR"],
                            gross_return=gross,
                            net_return=net,
                            detected_at=now,
                            rates_snapshot=dict(rates),
                        )
                    )

        results.sort(key=lambda o: o.net_return, reverse=True)
        return results

    def _cross_rate(
        self, from_ccy: str, to_ccy: str, rates: Dict[str, float]
    ) -> float:
        """
        Cross rate via EUR base.

        All rates in `rates` are EUR/XXX (units of XXX per 1 EUR).
            EUR → XXX : rates[XXX]
            XXX → EUR : 1 / rates[XXX]
            XXX → YYY : rates[YYY] / rates[XXX]
        """
        if from_ccy == to_ccy:
            return 1.0
        if from_ccy == "EUR":
            return rates[to_ccy]
        if to_ccy == "EUR":
            return 1.0 / rates[from_ccy]
        return rates[to_ccy] / rates[from_ccy]
