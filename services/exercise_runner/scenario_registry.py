"""
services/exercise_runner/scenario_registry.py — Named scenario lookup.

Add new scenarios here after creating their module in scenarios/.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from shared.exercise.models import Scenario
from scenarios.content_workflow import content_workflow
from scenarios.crypto_intel_flow import crypto_intel_flow
from scenarios.intake_to_policy import intake_to_policy

_REGISTRY: Dict[str, Scenario] = {
    "content_workflow":  content_workflow,
    "crypto_intel_flow": crypto_intel_flow,
    "intake_to_policy":  intake_to_policy,
}


def get_scenario(name: str) -> Optional[Scenario]:
    return _REGISTRY.get(name)


def list_scenarios() -> List[str]:
    return sorted(_REGISTRY.keys())
