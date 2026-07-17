"""
Scenario: content_workflow

Exercises the full content pipeline from an approved brief to publish.

Seed:    content.brief.approved
Chain:
  content.brief.approved
    → ContentOrchestratorAgent → content.video.request
    → ClaudeVideoService       → content.video.complete
    → ContentOrchestratorAgent → content.publish.request
    → PublishingService        → content.published

Required keys per step are the minimal event contract (not exhaustive schema).
exercise_id + correlation_id are always checked implicitly by Verifier.
"""
from shared.exercise.models import Scenario, ScenarioStep

content_workflow = Scenario(
    name        = "content_workflow",
    seed_topic  = "content.brief.approved",
    seed_payload = {
        "brief_id":             "exercise-brief-001",
        "symbol":               "ETH",
        "recommended_format":   "short_video",
        "recommended_channel":  "youtube",
        "title_options":        ["Exercise: ETH signal breakdown"],
        "hook":                 "Exercise mode — not real content.",
        "key_points":           ["Exercise point 1", "Exercise point 2"],
        "tone":                 "analytical",
        "target_length_s":      60,
        "opportunity_score":    0.75,
        "status":               "approved",
    },
    steps = [
        ScenarioStep(
            topic         = "content.video.request",
            required_keys = ["brief_id", "job_id", "symbol", "requires_action"],
        ),
        ScenarioStep(
            topic         = "content.video.complete",
            required_keys = ["brief_id", "job_id", "artifact_ref"],
        ),
        ScenarioStep(
            topic         = "content.publish.request",
            required_keys = ["brief_id", "publish_id", "channel", "requires_action"],
        ),
        ScenarioStep(
            topic         = "content.published",
            required_keys = ["brief_id", "publish_id", "channel", "platform_ref"],
        ),
    ],
    timeout_s = 30.0,
)
