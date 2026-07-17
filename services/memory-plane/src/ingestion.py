from __future__ import annotations

from typing import Any, Dict, Tuple


class IngestionFilter:
    """Decides which Kafka events are worth persisting to long-term memory.

    Importance rules (higher = more important):
      1.0  — policy_approved / policy_rejected events
      0.9  — eval.results with score < 0.5 (failure scenarios)
      0.8  — eval.results with score > 0.9 (high-success patterns)
      0.7  — decision.lifecycle with status=failed
      0.6  — decision.lifecycle with peer_consultation=true
      0.4  — all other eval.results
      0.2  — routine decision.lifecycle
      skip — anything < 0.3 (too noisy)
    """

    def should_ingest(self, event: Dict[str, Any], source: str) -> Tuple[bool, float]:
        """Return (should_ingest, importance_score)."""
        importance = self._score(event, source)
        return importance >= 0.3, importance

    def _score(self, event: Dict[str, Any], source: str) -> float:
        event_type = str(event.get("event_type") or "").lower()
        if event_type in ("policy_approved", "policy_rejected"):
            return 1.0

        if source == "eval.results":
            score = _to_float(event.get("score") or event.get("final_score"), default=0.5)
            if score < 0.5:
                return 0.9
            if score > 0.9:
                return 0.8
            return 0.4

        if source == "decision.lifecycle":
            status = str(event.get("status") or "").lower()
            if status == "failed":
                return 0.7
            if event.get("peer_consultation"):
                return 0.6
            return 0.2

        return 0.2

    def extract_content(self, event: Dict[str, Any], source: str) -> str:
        """Build a human-readable text string from the event."""
        if source == "eval.results":
            score = event.get("score") or event.get("final_score") or ""
            agent = event.get("agent") or event.get("runner_used") or "unknown"
            scenario = event.get("scenario") or event.get("decision_id") or "unknown"
            details = event.get("details") or event.get("error_codes") or ""
            return f"Outcome: {score} for {agent} on {scenario}. {details}"

        if source == "decision.lifecycle":
            status = event.get("status") or "unknown"
            agent = event.get("agent") or event.get("component") or "unknown"
            policy_id = event.get("policy_id") or event.get("policy_ref") or ""
            summary = event.get("summary") or event.get("decision_id") or ""
            return f"Decision: {status} by {agent}. Policy: {policy_id}. {summary}"

        return str(event)

    def extract_entity(self, event: Dict[str, Any]) -> Tuple[str, str]:
        """Return (entity_type, entity_id)."""
        if event.get("agent") or event.get("runner_used"):
            return "agent", str(event.get("agent") or event.get("runner_used") or "unknown")
        if event.get("policy_id") or event.get("policy_ref"):
            return "policy", str(event.get("policy_id") or event.get("policy_ref") or "unknown")
        if event.get("score") is not None or event.get("final_score") is not None:
            return "outcome", str(event.get("decision_id") or "unknown")
        return "pattern", str(event.get("decision_id") or event.get("context_id") or "unknown")


def _to_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default
