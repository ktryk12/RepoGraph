"""
Scenario: crypto_intel_flow

Exercises the intelligence pipeline from a new project signal to a brief.

Seed:    signal.analysis.complete
Chain:
  signal.analysis.complete
    → TrendScoutAgent   → content.opportunity.detected
    → CreativeBriefAgent → content.brief.ready
"""
from shared.exercise.models import Scenario, ScenarioStep

crypto_intel_flow = Scenario(
    name        = "crypto_intel_flow",
    seed_topic  = "signal.analysis.complete",
    seed_payload = {
        "signal_type":          "analysis_complete",
        "analysis_id":          "exercise-ana-001",
        "symbol":               "SOL",
        "original_confidence":  0.82,
        "verdict":              "strong",
        "score":                0.82,
        "analysis_score":       0.82,
        "thesis":               "Exercise mode — Solana test signal.",
        "requires_action":      False,
        "requires_human_review": True,
    },
    steps = [
        ScenarioStep(
            topic         = "content.opportunity.detected",
            required_keys = ["opportunity_id", "symbol", "opportunity_score",
                             "requires_action", "requires_human_review"],
        ),
        ScenarioStep(
            topic         = "content.brief.ready",
            required_keys = ["brief_id", "symbol", "recommended_format",
                             "requires_action", "requires_human_review", "status"],
        ),
    ],
    timeout_s = 20.0,
)
