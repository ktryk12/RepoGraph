"""
ValutaChallengePolicy — BabyAI policy definition for valuta arbitrage challenge.

Regler med severity >= 0.95 trigges L7 GovernanceAgent auto-block.
hard_policy=True regler kan IKKE modificeres af JEPA eller PolicyEvolution.

Brug:
    from policy.valuta_challenge_policy import VALUTA_CHALLENGE_POLICY, PolicyRule
    rule = VALUTA_CHALLENGE_POLICY.get_rule("max_position_pct")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class PolicyRule:
    """Én regel i en BabyAI policy."""

    name: str
    description: str
    violation_text: str
    severity: float          # 0.0–1.0; >= 0.95 → L7 auto-block
    hard_policy: bool = False  # True → JEPA og PolicyEvolution må ikke ændre reglen


@dataclass(frozen=True)
class PromotionCriteria:
    """Kriterier for promotion fra paper-trading til real execution."""

    win_rate_min: float = 0.55
    sharpe_min: float = 1.2
    min_rounds: int = 50


@dataclass(frozen=True)
class ValutaChallengePolicy:
    """
    Policy-definition for valuta arbitrage challenge.

    readonly=True: JEPA og PolicyBridge må læse men aldrig modificere denne policy.
    paper_trading_mode=True: Ingen real execution før eksplicit human approval.
    """

    name: str
    version: str
    rules: List[PolicyRule]
    promotion_criteria: PromotionCriteria
    paper_trading_mode: bool = True
    readonly: bool = True

    def get_rule(self, name: str) -> Optional[PolicyRule]:
        """Returner regel ved navn, eller None."""
        for rule in self.rules:
            if rule.name == name:
                return rule
        return None

    def hard_rules(self) -> List[PolicyRule]:
        """Returner alle regler med hard_policy=True."""
        return [r for r in self.rules if r.hard_policy]

    def l7_rules(self) -> List[PolicyRule]:
        """Returner regler der trigger L7 auto-block (severity >= 0.95)."""
        return [r for r in self.rules if r.severity >= 0.95]

    def validate_decision(self, decision: dict) -> dict:
        """
        Valider et agent-decision mod policy.
        Returnerer {"ok": bool, "violation": str, "severity": float, "l7": bool}.
        """
        reasoning = str(decision.get("reasoning") or "")
        position_pct = float(decision.get("position_pct") or 0.0)
        spread_pct = float(decision.get("spread_pct") or 0.0)
        consecutive_same_pair = int(decision.get("consecutive_same_pair") or 0)

        # audit_trail_required: reasoning >= 20 chars
        if len(reasoning) < 20:
            return {
                "ok": False,
                "violation": "audit_trail_required",
                "violation_text": "Reasoning for kort — min 20 tegn krævet",
                "severity": 0.7,
                "l7": False,
            }

        # max_position_pct: max 30%
        if position_pct > 0.30:
            sev = 0.96 if position_pct > 0.60 else 0.75
            return {
                "ok": False,
                "violation": "max_position_pct",
                "violation_text": f"Position {position_pct:.0%} overstiger max 30%",
                "severity": sev,
                "l7": sev >= 0.95,
            }

        # min_spread_pct: >= 0.15%
        if 0.0 < spread_pct < 0.0015:
            return {
                "ok": False,
                "violation": "min_spread_pct",
                "violation_text": f"Spread {spread_pct:.3%} under minimum 0.15%",
                "severity": 0.5,
                "l7": False,
            }

        # L7 trigger: samme valutapar > 5 trades i træk
        if consecutive_same_pair > 5:
            return {
                "ok": False,
                "violation": "consecutive_pair_limit",
                "violation_text": (
                    f"Samme par {consecutive_same_pair} gange i træk "
                    "— mulig loop/manipulation"
                ),
                "severity": 0.97,
                "l7": True,
            }

        return {"ok": True, "violation": "", "violation_text": "", "severity": 0.0, "l7": False}


# ─── Policy-instans ────────────────────────────────────────────────────────────

VALUTA_CHALLENGE_POLICY = ValutaChallengePolicy(
    name="valuta_challenge_v1",
    version="v1",
    paper_trading_mode=True,
    promotion_criteria=PromotionCriteria(
        win_rate_min=0.55,
        sharpe_min=1.2,
        min_rounds=50,
    ),
    rules=[
        PolicyRule(
            name="audit_trail_required",
            description=(
                "Hvert decision SKAL have et 'reasoning' felt med minimum 20 tegn. "
                "Sikrer at agent-beslutninger er sporbare og forklarbare."
            ),
            violation_text="Reasoning for kort — min 20 tegn krævet",
            severity=0.7,
            hard_policy=False,
        ),
        PolicyRule(
            name="max_position_pct",
            description=(
                "Maximum 30% af kapital per enkelt trade. "
                "Beskytter mod over-eksponering. "
                "Over 60% → L7 trigger (severity=0.96)."
            ),
            violation_text="Position overstiger max 30% af kapital",
            severity=0.75,
            hard_policy=True,
        ),
        PolicyRule(
            name="min_spread_pct",
            description=(
                "Må kun trade hvis spread >= 0.15%. "
                "Undgå noise trades der ikke dækker transaktionsomkostninger."
            ),
            violation_text="Spread under minimum threshold på 0.15%",
            severity=0.5,
            hard_policy=False,
        ),
        PolicyRule(
            name="stop_loss",
            description=(
                "Hvis agent kapital falder under 0.20 EUR (20% af start), "
                "sættes agent status='dormant'. Beskytter mod total tab."
            ),
            violation_text="Stop-loss aktiveret — kapital under 0.20 EUR",
            severity=0.8,
            hard_policy=True,
        ),
        PolicyRule(
            name="l7_large_position",
            description=(
                "Position > 60% af kapital trigger L7 human approval queue. "
                "Severity=0.96 → auto-exec kræver manuel godkendelse."
            ),
            violation_text="Position over 60% — L7 human approval påkrævet",
            severity=0.96,
            hard_policy=True,
        ),
        PolicyRule(
            name="consecutive_pair_limit",
            description=(
                "Samme valutapar > 5 trades i træk indikerer mulig "
                "loop eller manipulation. Severity=0.97 → L7 queue."
            ),
            violation_text="Samme valutapar > 5 gange i træk — mulig loop",
            severity=0.97,
            hard_policy=True,
        ),
        PolicyRule(
            name="paper_trading_mode",
            description=(
                "Alle trades er paper-trades. Ingen real execution. "
                "Promotion til real execution kræver eksplicit human approval "
                "og opfyldelse af promotion_criteria."
            ),
            violation_text="Real execution ikke tilladt — paper_trading_mode=True",
            severity=0.99,
            hard_policy=True,
        ),
    ],
    readonly=True,
)
