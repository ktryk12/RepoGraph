"""
tools/opportunity_scorer.py — Skill: score a content opportunity.

Pure function skill — no side effects, no state, no Kafka.
Called by TrendScoutAgent to decide whether to emit an opportunity signal.

Scoring factors:
  - analysis_score    : DeepAnalysisAgent verdict (0.0–1.0)
  - sentiment_score   : ReviewMiner sentiment (-1.0–1.0), normalized
  - signal_confidence : upstream CryptoIntelAgent confidence (0.0–1.0)
  - novelty_bonus     : +0.10 if topic not seen recently
  - verdict_weight    : strong=1.0, moderate=0.75, weak=0.40, avoid=0.0

Final score in [0.0, 1.0].  Threshold for emitting: ≥ 0.60.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

_VERDICT_WEIGHTS: Dict[str, float] = {
    "strong":   1.00,
    "moderate": 0.75,
    "weak":     0.40,
    "avoid":    0.00,
}

_MIN_SCORE = 0.60   # below this → opportunity not emitted


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_opportunity(
    analysis_result:    Dict[str, Any],
    sentiment_result:   Optional[Dict[str, Any]] = None,
    signal_confidence:  float = 0.0,
    seen_topics:        Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Compute a composite opportunity score.

    Args:
        analysis_result  : output of DeepAnalysisAgent / FinRobotAdapter
        sentiment_result : output of review_miner.mine_reviews (optional)
        signal_confidence: upstream confidence from CryptoIntelAgent
        seen_topics      : list of topic ids already seen this session

    Returns:
        {
            "score":        float,          # 0.0–1.0
            "above_threshold": bool,        # score >= 0.60
            "factors":      Dict[str,float],
            "verdict":      str,
            "recommendation": str,
        }
    """
    verdict = analysis_result.get("verdict", "avoid")
    verdict_w = _VERDICT_WEIGHTS.get(verdict, 0.0)

    analysis_score = float(analysis_result.get("score", 0.0))
    sentiment_raw  = float((sentiment_result or {}).get("sentiment_score", 0.0))
    sentiment_norm = (sentiment_raw + 1.0) / 2.0   # map [-1,1] → [0,1]

    topic_id = analysis_result.get("symbol", analysis_result.get("topic", ""))
    novelty  = 0.10 if (seen_topics is not None and topic_id not in seen_topics) else 0.0

    # Weighted composite
    score = (
        analysis_score   * 0.35
        + verdict_w      * 0.25
        + signal_confidence * 0.25
        + sentiment_norm * 0.15
        + novelty
    )
    score = min(1.0, round(score, 4))

    factors = {
        "analysis_score":    analysis_score,
        "verdict_weight":    verdict_w,
        "signal_confidence": signal_confidence,
        "sentiment_norm":    round(sentiment_norm, 4),
        "novelty_bonus":     novelty,
    }

    above = score >= _MIN_SCORE
    recommendation = _recommend(score, verdict)

    _log.debug(
        "opportunity_scorer score=%.3f above_threshold=%s verdict=%s topic=%s",
        score, above, verdict, topic_id,
    )

    return {
        "score":            score,
        "above_threshold":  above,
        "factors":          factors,
        "verdict":          verdict,
        "recommendation":   recommendation,
    }


def filter_opportunities(
    scored: List[Dict[str, Any]],
    min_score: float = _MIN_SCORE,
) -> List[Dict[str, Any]]:
    """Return only opportunities above threshold, sorted by score descending."""
    return sorted(
        [o for o in scored if o.get("score", 0.0) >= min_score],
        key=lambda o: o["score"],
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _recommend(score: float, verdict: str) -> str:
    if verdict == "avoid" or score < 0.40:
        return "skip"
    if score >= 0.80:
        return "high_priority"
    if score >= 0.60:
        return "create_brief"
    return "monitor"
