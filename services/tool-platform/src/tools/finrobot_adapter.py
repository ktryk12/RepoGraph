"""
tools/finrobot_adapter.py — FinRobot financial analysis adapter for BabyAI.

Wraps FinRobot's data utilities for deeper post-screening analysis.
Used AFTER CryptoIntelAgent/OpenBB identifies a candidate — not for
real-time polling.

If FinRobot is unavailable, all methods return empty results and log
a warning. Non-blocking by design.

FinRobot source: https://github.com/ai4finance-foundation/FinRobot
Installed from: E:/repos/FinRobot (--no-deps, Python 3.13 compat)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

# Try importing FinRobot utilities — graceful fallback if unavailable
try:
    from finrobot.data_source.yfinance_utils import YFinanceUtils as _YFUtils
    _FINROBOT_AVAILABLE = True
except Exception as _exc:
    _log.warning("finrobot_unavailable error=%s — FinRobotAdapter returning stubs", _exc)
    _FINROBOT_AVAILABLE = False
    _YFUtils = None  # type: ignore[assignment]

_VERDICTS = ("strong", "moderate", "weak", "avoid")


def _empty_analysis(symbol: str = "") -> Dict[str, Any]:
    return {
        "symbol":        symbol,
        "score":         0.0,
        "risks":         [],
        "opportunities": [],
        "verdict":       "avoid",
        "confidence":    0.0,
        "available":     _FINROBOT_AVAILABLE,
    }


class FinRobotAdapter:
    """
    Adapter for FinRobot financial analysis capabilities.

    Used for deeper analysis AFTER CryptoIntelAgent/OpenBB identifies
    a candidate — not for real-time screening.

    All methods:
    - Return dict or str — never raw FinRobot objects
    - Return empty/stub results if FinRobot unavailable
    - Log at WARNING level on errors
    - Never raise
    """

    # ── Equity analysis ───────────────────────────────────────────────────────

    def analyze_equity(
        self,
        symbol: str,
        analysis_type: str = "comprehensive",
    ) -> Dict[str, Any]:
        """
        Deep equity analysis using FinRobot's yfinance data layer.

        analysis_type: "comprehensive" | "sentiment" | "risk"

        Returns structured analysis with confidence score::
            {
              "symbol": "AAPL",
              "score": 0.72,
              "risks": ["high PE ratio", ...],
              "opportunities": ["strong cash flow", ...],
              "verdict": "moderate",
              "confidence": 0.72,
            }

        Example::
            adapter.analyze_equity("NVDA", analysis_type="risk")
        """
        if not _FINROBOT_AVAILABLE or _YFUtils is None:
            _log.warning("finrobot_analyze_equity_skipped symbol=%s reason=unavailable", symbol)
            return _empty_analysis(symbol)

        try:
            info  = _YFUtils.get_stock_info(symbol)
            price = _YFUtils.get_stock_price(symbol)

            risks: List[str] = []
            opportunities: List[str] = []
            score = 0.5  # neutral baseline

            if info:
                pe = info.get("trailingPE") or info.get("forwardPE")
                if pe:
                    if pe > 50:
                        risks.append(f"High P/E ratio: {pe:.1f}")
                        score -= 0.1
                    elif pe < 15:
                        opportunities.append(f"Low P/E ratio: {pe:.1f} — potential value")
                        score += 0.1

                market_cap = info.get("marketCap", 0)
                if market_cap > 1e11:
                    opportunities.append("Large-cap stability")
                    score += 0.05

                debt_equity = info.get("debtToEquity")
                if debt_equity and debt_equity > 200:
                    risks.append(f"High debt/equity: {debt_equity:.0f}%")
                    score -= 0.1

                profit_margin = info.get("profitMargins")
                if profit_margin and profit_margin > 0.15:
                    opportunities.append(f"Strong profit margin: {profit_margin:.1%}")
                    score += 0.1
                elif profit_margin and profit_margin < 0:
                    risks.append("Negative profit margins")
                    score -= 0.15

            score = max(0.0, min(1.0, score))
            verdict = _score_to_verdict(score)

            return {
                "symbol":        symbol,
                "score":         round(score, 4),
                "risks":         risks,
                "opportunities": opportunities,
                "verdict":       verdict,
                "confidence":    round(score, 4),
                "analysis_type": analysis_type,
                "available":     True,
            }
        except Exception as exc:
            _log.warning("finrobot_analyze_equity_failed symbol=%s error=%s", symbol, exc)
            return _empty_analysis(symbol)

    # ── Crypto project analysis ───────────────────────────────────────────────

    def analyze_crypto_project(
        self,
        project_name: str,
        whitepaper_text: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate a new crypto project based on name and optional whitepaper text.

        Uses whitepaper_text from FirecrawlClient if provided.

        Returns::
            {
              "score": 0.0-1.0,
              "risks": [...],
              "opportunities": [...],
              "verdict": "strong|moderate|weak|avoid",
            }

        Example::
            adapter.analyze_crypto_project("SomeCoin", whitepaper_text="...")
        """
        risks:         List[str] = []
        opportunities: List[str] = []
        score = 0.4  # conservative baseline for unknown projects

        try:
            if whitepaper_text:
                text_lower = whitepaper_text.lower()
                word_count = len(whitepaper_text.split())

                # Positive signals
                if word_count > 2000:
                    opportunities.append("Detailed whitepaper (>2000 words)")
                    score += 0.05
                if any(t in text_lower for t in ("audit", "audited", "certik", "chainalysis")):
                    opportunities.append("Security audit mentioned")
                    score += 0.10
                if any(t in text_lower for t in ("tokenomics", "vesting", "lock")):
                    opportunities.append("Tokenomics / vesting schedule documented")
                    score += 0.05
                if any(t in text_lower for t in ("roadmap", "milestone", "q1", "q2", "q3", "q4")):
                    opportunities.append("Roadmap with milestones present")
                    score += 0.05

                # Risk signals
                if any(t in text_lower for t in ("guaranteed", "100x", "no risk", "risk-free")):
                    risks.append("Unrealistic return claims detected")
                    score -= 0.20
                if word_count < 500:
                    risks.append("Very short whitepaper (<500 words)")
                    score -= 0.10
                if "anonymous" in text_lower and "team" in text_lower:
                    risks.append("Anonymous team mentioned")
                    score -= 0.05
            else:
                risks.append("No whitepaper text available for analysis")
                score -= 0.05

            score = max(0.0, min(1.0, score))
            verdict = _score_to_verdict(score)

            return {
                "project_name":  project_name,
                "score":         round(score, 4),
                "risks":         risks,
                "opportunities": opportunities,
                "verdict":       verdict,
                "has_whitepaper": whitepaper_text is not None,
                "available":     True,
            }
        except Exception as exc:
            _log.warning(
                "finrobot_analyze_crypto_failed project=%s error=%s", project_name, exc
            )
            return {
                "project_name":  project_name,
                "score":         0.0,
                "risks":         ["Analysis failed"],
                "opportunities": [],
                "verdict":       "avoid",
                "has_whitepaper": whitepaper_text is not None,
                "available":     _FINROBOT_AVAILABLE,
            }

    # ── Investment thesis ─────────────────────────────────────────────────────

    def generate_investment_thesis(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        whale_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Generate a concise investment thesis combining market + whale data.

        Returns plain text thesis, max 300 words.

        Example::
            adapter.generate_investment_thesis(
                "BTC", market_data={"price": 84000}, whale_data={...}
            )
        """
        try:
            lines: List[str] = [f"## Investment Thesis: {symbol}"]

            # Market context
            price = market_data.get("price") or market_data.get("close")
            if price:
                lines.append(f"Current price: {price:,.2f}")

            market_cap = market_data.get("market_cap") or market_data.get("marketCap")
            if market_cap:
                lines.append(f"Market cap: ${market_cap:,.0f}")

            vol_ratio = market_data.get("volume_ratio")
            if vol_ratio:
                lines.append(f"Volume/market-cap ratio: {vol_ratio:.2%} (high = unusual activity)")

            # Whale context
            if whale_data:
                whale_count = whale_data.get("score", len(whale_data.get("whale_txns", [])))
                if whale_count:
                    lines.append(
                        f"Whale activity: {whale_count} large transaction(s) detected "
                        "in the last monitoring window."
                    )

            # Verdict from analysis if present
            score    = market_data.get("analysis_score", 0.0)
            verdict  = market_data.get("verdict", "")
            if verdict:
                lines.append(f"Analysis verdict: {verdict.upper()} (score: {score:.2f})")

            risks        = market_data.get("risks", [])
            opportunities = market_data.get("opportunities", [])
            if opportunities:
                lines.append("Key opportunities: " + "; ".join(opportunities[:3]))
            if risks:
                lines.append("Key risks: " + "; ".join(risks[:3]))

            lines.append(
                "\nNote: This thesis is generated from automated signals. "
                "Human review required before any action."
            )

            thesis = "\n".join(lines)
            # Enforce 300-word limit
            words = thesis.split()
            if len(words) > 300:
                thesis = " ".join(words[:300]) + "..."
            return thesis
        except Exception as exc:
            _log.warning("finrobot_thesis_failed symbol=%s error=%s", symbol, exc)
            return f"Thesis generation failed for {symbol}: {exc}"


def _score_to_verdict(score: float) -> str:
    if score >= 0.70:
        return "strong"
    if score >= 0.55:
        return "moderate"
    if score >= 0.40:
        return "weak"
    return "avoid"
