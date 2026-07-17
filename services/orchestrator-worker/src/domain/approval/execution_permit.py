"""
Execution Permit for Orchestrator Worker

Implements approval tokens instead of direct AESA imports.
Following ADR-0015 contract-based communication patterns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExecutionPermit:
    """Execution permit for approved decisions."""

    decision_id: str
    policy_fingerprint: str
    approved_by: str
    approved_at: str
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "decision_id": self.decision_id,
            "policy_fingerprint": self.policy_fingerprint,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "reason": self.reason
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ExecutionPermit':
        """Create from dictionary."""
        return cls(
            decision_id=data["decision_id"],
            policy_fingerprint=data["policy_fingerprint"],
            approved_by=data["approved_by"],
            approved_at=data["approved_at"],
            reason=data.get("reason")
        )


def require_execution_permit_from_mapping(
    permit_data: Dict[str, Any],
    decision_id: str,
    policy_fingerprint: Optional[str] = None
) -> Optional[ExecutionPermit]:
    """Create execution permit from mapping data."""
    try:
        permit = ExecutionPermit.from_dict(permit_data)

        # Validate decision_id matches
        if permit.decision_id != decision_id:
            logger.warning(f"Permit decision_id mismatch: expected {decision_id}, got {permit.decision_id}")
            return None

        # Validate policy fingerprint if provided
        if policy_fingerprint and permit.policy_fingerprint != policy_fingerprint:
            logger.warning(f"Permit policy fingerprint mismatch: expected {policy_fingerprint}, got {permit.policy_fingerprint}")
            return None

        return permit

    except Exception as e:
        logger.warning(f"Failed to create execution permit: {e}")
        return None