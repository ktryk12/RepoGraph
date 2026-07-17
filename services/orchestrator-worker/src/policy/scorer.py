"""
Mock policy scorer module for orchestrator-worker

Provides architecture scoring and rules loading functionality.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def load_rules(rules_path: Optional[str] = None) -> Dict[str, Any]:
    """Load scoring rules."""
    return {
        "version": "1.0",
        "rules": [
            {"name": "basic_validation", "weight": 1.0, "enabled": True},
            {"name": "security_check", "weight": 1.5, "enabled": True},
            {"name": "performance_check", "weight": 1.2, "enabled": True}
        ],
        "thresholds": {
            "pass": 0.7,
            "warning": 0.5,
            "fail": 0.3
        }
    }


def score_architecture(
    architecture_data: Dict[str, Any],
    rules: Optional[Dict[str, Any]] = None,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Score architecture based on rules."""

    # Use provided rules or load defaults
    scoring_rules = rules or load_rules()

    # Mock scoring logic
    base_score = 0.8
    components_score = 0.75
    gates_passed = True

    return {
        "passed": gates_passed,
        "score": base_score,
        "components": {
            "architecture_quality": components_score,
            "security_validation": 0.9,
            "performance_validation": 0.8
        },
        "gate_results": {
            "architecture_gates": True,
            "security_gates": True,
            "performance_gates": True
        },
        "penalties": [],
        "failure_reasons": [],
        "metadata": {
            "rules_version": scoring_rules.get("version", "1.0"),
            "context": context or {}
        }
    }