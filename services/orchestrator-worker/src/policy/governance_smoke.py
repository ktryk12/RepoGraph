"""
Mock policy governance_smoke module for orchestrator-worker

Provides governance smoke testing functionality.
"""

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def build_governance_artifact_payload(event: Any, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Build governance artifact payload."""
    return {
        "governance_check": "passed",
        "decision_id": getattr(event, "decision_id", "unknown"),
        "metadata": metadata
    }


def evaluate_governance_hello_world_artifact(artifact_data: Any) -> Dict[str, Any]:
    """Evaluate governance hello world artifact."""
    return {
        "passed": True,
        "score": 1.0,
        "reason": "governance_smoke_test_passed"
    }


def expected_payload() -> Dict[str, Any]:
    """Expected governance payload."""
    return {
        "governance_check": "expected"
    }


def extract_model_json_payload(response_data: Any) -> Dict[str, Any]:
    """Extract model JSON payload."""
    if isinstance(response_data, dict):
        return response_data
    return {}


def is_governance_hello_world_task(task: Dict[str, Any]) -> bool:
    """Check if task is governance hello world task."""
    task_ref = task.get("task_ref", "")
    return "hello" in str(task_ref).lower() or "governance" in str(task_ref).lower()


# Set expected_payload as both function and constant for compatibility
governance_expected_payload = expected_payload()