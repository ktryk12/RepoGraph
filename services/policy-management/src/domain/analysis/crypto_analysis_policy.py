"""
CryptoAnalysisPolicy — BabyAI policy definition for crypto research demos.

Regler med severity >= 0.95 trigges L7 GovernanceAgent auto-block.
hard_policy=True regler kan IKKE modificeres af JEPA eller PolicyEvolution.

Brug:
    from policy.crypto_analysis_policy import CRYPTO_ANALYSIS_POLICY, PolicyRule
    rule = CRYPTO_ANALYSIS_POLICY.get_rule("prompt_injection_guard")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class PolicyRule:
    """Én regel i en BabyAI policy."""

    name: str
    description: str
    violation_text: str
    severity: float          # 0.0–1.0; ≥ 0.95 → L7 auto-block
    hard_policy: bool = False  # True → JEPA og PolicyEvolution må ikke ændre reglen


@dataclass(frozen=True)
class CryptoAnalysisPolicy:
    """
    Policy-definition for crypto swarm analyse.

    readonly=True: JEPA og PolicyBridge må læse men aldrig modificere denne policy.
    """

    name: str
    version: str
    rules: List[PolicyRule]
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


# ─── Policy-instans ────────────────────────────────────────────────────────────

CRYPTO_ANALYSIS_POLICY = CryptoAnalysisPolicy(
    name="crypto_analysis_policy",
    version="v1",
    rules=[
        PolicyRule(
            name="no_financial_advice",
            description=(
                "Output må ikke indeholde specifikke køb/salg anbefalinger med beløb. "
                "Analysen er udelukkende til demonstration af BabyAI swarm-teknologi."
            ),
            violation_text="Finansiel rådgivning ikke tilladt",
            severity=0.7,
            hard_policy=False,
        ),
        PolicyRule(
            name="prompt_injection_guard",
            description=(
                "Input må ikke indeholde prompt injection mønstre: "
                "system overrides, jailbreaks, template injection, "
                "shell operators, script tags, SQL injection, prompt leaking."
            ),
            violation_text="Prompt injection detekteret",
            severity=0.98,   # > 0.95 → L7 auto-block
            hard_policy=True,
        ),
        PolicyRule(
            name="data_exfiltration_guard",
            description=(
                "Input må ikke forsøge at ekstrahere systemdata: "
                "env-filer, API-nøgler, passwords, redis/kafka dumps, "
                "interne konfigurationer."
            ),
            violation_text="Data exfiltration forsøg",
            severity=0.97,   # > 0.95 → L7 auto-block
            hard_policy=True,
        ),
        PolicyRule(
            name="input_length_limit",
            description="Input-strings max 500 tegn for at undgå resource exhaustion.",
            violation_text="Input for langt",
            severity=0.6,
            hard_policy=False,
        ),
        PolicyRule(
            name="malformed_input_guard",
            description=(
                "Bloker binært indhold, control characters (U+0000–U+001F) "
                "og null bytes der kan bruges til bypass-angreb."
            ),
            violation_text="Ugyldigt input format",
            severity=0.85,
            hard_policy=True,
        ),
        PolicyRule(
            name="data_freshness",
            description=(
                "Advar brugeren hvis markedsdata er ældre end 24 timer. "
                "Ikke en hard blok — kun en advarsel."
            ),
            violation_text="Advarsel: data kan være forældet",
            severity=0.3,
            hard_policy=False,
        ),
    ],
    readonly=True,
)
