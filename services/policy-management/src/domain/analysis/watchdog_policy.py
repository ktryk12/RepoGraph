"""
policy/watchdog_policy.py — Policy for WatchdogAgent investigative content.

All rules are hard_policy=True — JEPA og PolicyEvolution must NOT modify them.
Rules with severity >= 0.95 trigger L7 GovernanceAgent auto-block.

Brug:
    from policy.watchdog_policy import WATCHDOG_POLICY
    rule = WATCHDOG_POLICY.get_rule("source_required")
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class PolicyRule:
    """One rule in a BabyAI policy."""

    name: str
    description: str
    violation_text: str
    severity: float          # 0.0–1.0; >= 0.95 → L7 auto-block
    hard_policy: bool = False


@dataclass(frozen=True)
class WatchdogPolicySpec:
    """
    Declarative policy for WatchdogAgent investigative content.

    readonly=True: JEPA og PolicyBridge may read but never modify.
    All rules are hard_policy=True.
    """

    name: str
    description: str
    version: str
    rules: List[PolicyRule]
    readonly: bool = True

    def get_rule(self, name: str) -> Optional[PolicyRule]:
        for rule in self.rules:
            if rule.name == name:
                return rule
        return None

    def hard_rules(self) -> List[PolicyRule]:
        return [r for r in self.rules if r.hard_policy]

    def l7_rules(self) -> List[PolicyRule]:
        """Rules that trigger L7 auto-block (severity >= 0.95)."""
        return [r for r in self.rules if r.severity >= 0.95]

    def check(self, **kwargs) -> list[str]:
        """
        Convenience check — returns list of violated rule names.

        Supported kwargs:
            has_sources (bool)          — False → source_required violated
            confidence (float)          — < 0.85 → min_confidence violated
            names_private_person (bool) — True → no_unnamed_private_persons violated
            active_litigation (bool)    — True → no_active_litigation violated
            human_approved (bool)       — False → human_approval_required violated
            content_tag (str)           — "NSFW" + nsfw_approved=False → nsfw_explicit_gate violated
            nsfw_approved (bool)        — must be True when content_tag="NSFW"
        """
        violated: list[str] = []
        if not kwargs.get("has_sources", True):
            violated.append("source_required")
        if kwargs.get("confidence", 1.0) < 0.85:
            violated.append("min_confidence")
        if kwargs.get("names_private_person", False):
            violated.append("no_unnamed_private_persons")
        if kwargs.get("active_litigation", False):
            violated.append("no_active_litigation")
        if not kwargs.get("human_approved", True):
            violated.append("human_approval_required")
        if (
            str(kwargs.get("content_tag", "GENERAL")).upper() == "NSFW"
            and not kwargs.get("nsfw_approved", False)
        ):
            violated.append("nsfw_explicit_gate")
        return violated


# ─── Policy instance ───────────────────────────────────────────────────────────

WATCHDOG_POLICY = WatchdogPolicySpec(
    name="watchdog_policy",
    description="Investigative content policy — verified facts, documented scandals only",
    version="v1",
    rules=[
        PolicyRule(
            name="source_required",
            description=(
                "Every published claim must have at least 2 independent, named sources. "
                "Unsubstantiated assertions are prohibited regardless of plausibility."
            ),
            violation_text="Claim published without minimum 2 independent sources",
            severity=1.0,
            hard_policy=True,
        ),
        PolicyRule(
            name="min_confidence",
            description=(
                "confidence_score() must return >= 0.85 before any claim is included. "
                "Claims scoring below 0.85 are silently dropped from output."
            ),
            violation_text="Claim confidence below 0.85 threshold — blocked",
            severity=0.95,
            hard_policy=True,
        ),
        PolicyRule(
            name="no_unnamed_private_persons",
            description=(
                "Private individuals may only be named if they have been convicted by a court "
                "or hold an unambiguous public role directly relevant to the scandal. "
                "No naming of relatives, employees below executive level, or unnamed sources."
            ),
            violation_text="Private individual named without conviction or public role",
            severity=1.0,
            hard_policy=True,
        ),
        PolicyRule(
            name="no_active_litigation",
            description=(
                "Topics involving active legal proceedings where no final judgment has been "
                "handed down are blocked entirely. Wait for verdict before publishing."
            ),
            violation_text="Topic involves active litigation — blocked until verdict",
            severity=0.98,
            hard_policy=True,
        ),
        PolicyRule(
            name="human_approval_required",
            description=(
                "ALL output from WatchdogAgent requires explicit human approval before "
                "scheduling or publishing. Auto-approve is unconditionally forbidden."
            ),
            violation_text="Watchdog output routed without human approval gate",
            severity=1.0,
            hard_policy=True,
        ),
        PolicyRule(
            name="nsfw_explicit_gate",
            description=(
                "Content tagged NSFW must carry an explicit content_tag='NSFW' marker in "
                "all output payloads and requires a separate, dedicated human approval step "
                "beyond the standard watchdog gate. NSFW content may never be auto-approved "
                "or bundled silently with GENERAL content approvals."
            ),
            violation_text="NSFW-tagged content missing explicit gate or content_tag marker",
            severity=1.0,
            hard_policy=True,
        ),
    ],
    readonly=True,
)
