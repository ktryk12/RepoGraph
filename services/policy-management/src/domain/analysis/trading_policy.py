"""
Trading Policy — hard and soft rules for the TradingAgent.
All enforcement is paper-only: PAPER_ONLY=True is a hard constraint
that cannot be overridden at runtime.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)


@dataclass
class PolicyViolation:
    rule: str
    message: str
    hard: bool  # True = reject trade; False = warning only


@dataclass
class PolicyResult:
    allowed: bool
    violations: List[PolicyViolation] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def hard_violations(self) -> List[PolicyViolation]:
        return [v for v in self.violations if v.hard]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "violations": [
                {"rule": v.rule, "message": v.message, "hard": v.hard}
                for v in self.violations
            ],
            "warnings": self.warnings,
        }


class TradingPolicy:
    """
    Hard rules (any violation → allowed=False):
      - PAPER_ONLY: never place real orders
      - MAX_POSITION_PCT: single position ≤ 5% of equity
      - MAX_TOTAL_EXPOSURE: total open exposure ≤ 30% of equity
      - MIN_CONFIDENCE: signals below 0.65 are rejected
      - MAX_DAILY_TRADES: at most 10 trades per day

    Soft rules (logged as warnings, do not block):
      - PREFER_LIMIT_ORDERS: market orders are discouraged
      - DIVERSIFICATION: avoid >3 positions in the same asset class
    """

    MAX_POSITION_PCT: float = 0.05
    MAX_TOTAL_EXPOSURE: float = 0.30
    MIN_CONFIDENCE: float = 0.65
    PAPER_ONLY: bool = True
    MAX_DAILY_TRADES: int = 10

    def validate(
        self,
        *,
        action: str,
        symbol: str,
        confidence: float,
        position_pct: float,
        total_exposure: float,
        daily_trade_count: int,
        is_paper: bool,
        order_type: str = "market",
        asset_class_count: int = 1,
    ) -> PolicyResult:
        violations: List[PolicyViolation] = []
        warnings: List[str] = []

        # ── Hard rules ─────────────────────────────────────────────────────
        if not is_paper and self.PAPER_ONLY:
            violations.append(PolicyViolation(
                rule="PAPER_ONLY",
                message="Real orders are disabled. Only paper trading is permitted.",
                hard=True,
            ))

        if action not in ("BUY", "SELL", "HOLD"):
            violations.append(PolicyViolation(
                rule="VALID_ACTION",
                message=f"Unknown action '{action}'. Must be BUY, SELL, or HOLD.",
                hard=True,
            ))

        if confidence < self.MIN_CONFIDENCE:
            violations.append(PolicyViolation(
                rule="MIN_CONFIDENCE",
                message=(
                    f"Confidence {confidence:.3f} is below minimum {self.MIN_CONFIDENCE}. "
                    "Signal too weak."
                ),
                hard=True,
            ))

        if position_pct > self.MAX_POSITION_PCT:
            violations.append(PolicyViolation(
                rule="MAX_POSITION_PCT",
                message=(
                    f"Position size {position_pct:.1%} exceeds max {self.MAX_POSITION_PCT:.1%}."
                ),
                hard=True,
            ))

        if total_exposure > self.MAX_TOTAL_EXPOSURE:
            violations.append(PolicyViolation(
                rule="MAX_TOTAL_EXPOSURE",
                message=(
                    f"Total exposure {total_exposure:.1%} exceeds max {self.MAX_TOTAL_EXPOSURE:.1%}."
                ),
                hard=True,
            ))

        if daily_trade_count >= self.MAX_DAILY_TRADES:
            violations.append(PolicyViolation(
                rule="MAX_DAILY_TRADES",
                message=(
                    f"Daily trade count {daily_trade_count} has reached limit {self.MAX_DAILY_TRADES}."
                ),
                hard=True,
            ))

        # ── Soft rules ─────────────────────────────────────────────────────
        if order_type == "market":
            warnings.append(
                f"PREFER_LIMIT_ORDERS: market order for {symbol} — consider limit order to reduce slippage."
            )

        if asset_class_count > 3:
            warnings.append(
                f"DIVERSIFICATION: {asset_class_count} positions in same asset class. "
                "Consider spreading across different assets."
            )

        allowed = len([v for v in violations if v.hard]) == 0
        if violations or warnings:
            _log.info(
                "trading_policy symbol=%s action=%s allowed=%s violations=%d warnings=%d",
                symbol, action, allowed, len(violations), len(warnings),
            )
        return PolicyResult(allowed=allowed, violations=violations, warnings=warnings)


# ── Live trading guards ───────────────────────────────────────────────────────

LIVE_TRADING_GUARDS: Dict[str, Any] = {
    "max_total_exposure_usdt": 500,     # Max $500 in the market at once
    "max_order_usdt": 50,               # Max $50 per individual order
    "max_daily_loss_pct": 3.0,          # Halt if daily loss exceeds 3%
    "max_concurrent_positions": 5,      # Max 5 open positions simultaneously
    "min_volume_24h_usdt": 10_000_000,  # Ignore illiquid symbols
    "require_paper_weeks": 2,           # Min 2 weeks paper trading before LIVE
}


def validate_live_switch(
    paper_trading_start_iso: Optional[str] = None,
    paper_win_rate: Optional[float] = None,
    paper_trade_count: int = 0,
) -> tuple[bool, str]:
    """
    Called before switching to LIVE mode.
    Checks that:
      - Paper trading has run for at least 2 weeks
      - Win rate in paper mode > 50%
      - At least 10 paper trades completed
    Returns (allowed: bool, reason: str).
    """
    import datetime as _dt

    if paper_trading_start_iso:
        try:
            start = _dt.datetime.fromisoformat(paper_trading_start_iso.replace("Z", "+00:00"))
            now = _dt.datetime.now(_dt.timezone.utc)
            weeks = (now - start).days / 7
            if weeks < LIVE_TRADING_GUARDS["require_paper_weeks"]:
                return False, (
                    f"Paper trading has only run for {weeks:.1f} weeks. "
                    f"Minimum is {LIVE_TRADING_GUARDS['require_paper_weeks']} weeks."
                )
        except Exception as exc:
            return False, f"Could not parse paper_trading_start_iso: {exc}"
    else:
        return False, "paper_trading_start_iso is required to validate live switch."

    if paper_trade_count < 10:
        return False, (
            f"Only {paper_trade_count} paper trades completed. Minimum is 10."
        )

    if paper_win_rate is not None and paper_win_rate < 0.50:
        return False, (
            f"Paper win rate {paper_win_rate:.0%} is below minimum 50%. "
            "Improve signal quality before going live."
        )

    return True, (
        f"Live switch approved: {weeks:.1f} weeks paper trading, "
        f"win_rate={paper_win_rate:.0%}, trades={paper_trade_count}"
    )


# Module-level singleton — mirrors constitution_service pattern
_policy: Optional[TradingPolicy] = None


def get_trading_policy() -> TradingPolicy:
    global _policy
    if _policy is None:
        _policy = TradingPolicy()
    return _policy


# ─── Declarative policy (crypto_analysis_policy.py pattern) ──────────────────
# Used by TradingAgent (agents/trading_agent.py) for rule-based enforcement.

@dataclass(frozen=True)
class PolicyRule:
    """One rule in a declarative BabyAI policy."""

    name: str
    description: str
    violation_text: str
    severity: float        # 0.0–1.0; >= 0.95 → L7 auto-block
    hard_policy: bool = False


@dataclass(frozen=True)
class TradingPolicySpec:
    """
    Declarative policy definition for paper-only currency arbitrage trading.

    readonly=True: JEPA and PolicyBridge may read but never modify this policy.
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
        return [r for r in self.rules if r.severity >= 0.95]


TRADING_POLICY = TradingPolicySpec(
    name="trading_policy",
    description="Paper-only currency arbitrage trading policy",
    version="v1",
    rules=[
        PolicyRule(
            name="paper_only",
            description="All trades must be paper trades. Live trading is prohibited.",
            violation_text="Live trading is prohibited — paper mode only",
            severity=1.0,
            hard_policy=True,
        ),
        PolicyRule(
            name="max_notional",
            description="Single trade notional must not exceed 10,000 EUR.",
            violation_text="Trade notional exceeds 10,000 EUR maximum",
            severity=0.95,
            hard_policy=True,
        ),
        PolicyRule(
            name="min_net_return",
            description="Only execute trades with net_return >= 1.001 (0.1% minimum).",
            violation_text="Trade net_return below minimum threshold of 1.001",
            severity=0.80,
            hard_policy=True,
        ),
        PolicyRule(
            name="max_daily_trades",
            description="Maximum 50 paper trades per 24-hour window.",
            violation_text="Daily trade limit of 50 exceeded",
            severity=0.85,
            hard_policy=True,
        ),
        PolicyRule(
            name="stop_loss",
            description="Halt trading if cumulative PnL drops below -500 EUR.",
            violation_text="Stop-loss triggered: cumulative PnL below -500 EUR",
            severity=1.0,
            hard_policy=True,
        ),
        PolicyRule(
            name="dataset_write_required",
            description="Every trade decision (execute or reject) must be written to dataset.",
            violation_text="Trade decision recorded without dataset write",
            severity=0.70,
            hard_policy=True,
        ),
    ],
    readonly=True,
)
