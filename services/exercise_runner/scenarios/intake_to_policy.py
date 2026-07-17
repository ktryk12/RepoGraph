"""
Scenario: intake_to_policy

Exercises the core policy lifecycle from intent to approval.

Seed:    decision.intent
Chain:
  decision.intent
    → request-gate    → decision.requested
    → planner         → decision.truthpack.ready
    → orchestrator    → decision.lifecycle
"""
from shared.exercise.models import Scenario, ScenarioStep

intake_to_policy = Scenario(
    name        = "intake_to_policy",
    seed_topic  = "decision.intent",
    seed_payload = {
        "decision_id":  "exercise-dec-001",
        "intent":       "exercise mode — no action required",
        "user_id":      "exercise-user",
        "context":      {"exercise": True, "dry_run": True},
    },
    steps = [
        ScenarioStep(
            topic         = "decision.requested",
            required_keys = ["decision_id"],
        ),
        ScenarioStep(
            topic         = "decision.truthpack.ready",
            required_keys = ["decision_id"],
        ),
        ScenarioStep(
            topic         = "decision.lifecycle",
            required_keys = ["decision_id"],
        ),
    ],
    timeout_s = 45.0,
)
