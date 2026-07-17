"""
Timeout Budget for Expert Serving

Implements timeout management instead of direct AESA imports.
Following ADR-0015 contract-based communication patterns.
"""

from __future__ import annotations

import os
import logging
from typing import Mapping

logger = logging.getLogger(__name__)


class TimeoutBudget:
    """Timeout budget configuration for expert serving operations."""

    def __init__(self, inner_seconds: float = 30.0):
        self.inner_seconds = inner_seconds

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> 'TimeoutBudget':
        """Create timeout budget from environment variables."""
        try:
            timeout_str = env.get('MODEL_RUNNER_TIMEOUT_SECONDS', '30.0')
            inner_seconds = float(timeout_str)
            return cls(inner_seconds=inner_seconds)
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid timeout configuration, using default 30s: {e}")
            return cls(inner_seconds=30.0)

    def __repr__(self) -> str:
        return f"TimeoutBudget(inner_seconds={self.inner_seconds})"