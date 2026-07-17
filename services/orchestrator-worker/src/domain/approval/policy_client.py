"""
Kafka-based Policy Client for Orchestrator Worker

Implements event-driven approval decisions instead of direct service imports.
Following ADR-0015 contract-based communication patterns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import hashlib
from typing import Any, Dict, Optional, Set
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class KafkaApprovalPolicyClient:
    """Kafka-based policy client for event-driven approval decisions."""

    def __init__(self, kafka_producer=None, timeout_seconds: float = 30.0):
        self.kafka_producer = kafka_producer
        self.timeout_seconds = timeout_seconds
        logger.info("KafkaApprovalPolicyClient initialized")

    def approval_required(
        self,
        effective_policy: Optional[Dict[str, Any]] = None,
        policy_constraints: Optional[Dict[str, Any]] = None,
        policy_preset: str = "",
        required_policy_ids: Optional[Set[str]] = None,
        required_safety_profiles: Optional[Set[str]] = None
    ) -> bool:
        """Determine if approval is required based on policy."""
        try:
            # Check effective policy first
            if effective_policy:
                approval_values = effective_policy.get("approval_required")
                if isinstance(approval_values, bool):
                    return approval_values
                elif isinstance(approval_values, list):
                    return len(approval_values) > 0
                elif isinstance(approval_values, str):
                    return bool(approval_values.strip())

                # Check constraints within effective policy
                constraints = effective_policy.get("constraints", {})
                if isinstance(constraints, dict) and constraints.get("approval_required"):
                    return True

            # Check policy constraints
            if policy_constraints and isinstance(policy_constraints, dict):
                if policy_constraints.get("approval_required"):
                    return True

            # Check if policy matches required IDs
            if required_policy_ids and policy_preset:
                if policy_preset.lower() in required_policy_ids:
                    return True

            # Check safety profile requirements
            if required_safety_profiles and effective_policy:
                safety_profile = effective_policy.get("safety_profile", "")
                if isinstance(safety_profile, str) and safety_profile.lower() in required_safety_profiles:
                    return True

            return False

        except Exception as e:
            logger.warning(f"Error checking approval requirement: {e}")
            # Default to requiring approval on error for safety
            return True

    def compute_policy_fingerprint(self, effective_policy: Dict[str, Any]) -> str:
        """Compute a fingerprint for the effective policy."""
        try:
            # Normalize policy for consistent hashing
            normalized = self._normalize_policy_for_fingerprint(effective_policy)
            policy_json = json.dumps(normalized, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
            return hashlib.sha256(policy_json.encode('utf-8')).hexdigest()
        except Exception as e:
            logger.warning(f"Error computing policy fingerprint: {e}")
            return ""

    def _normalize_policy_for_fingerprint(self, policy: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize policy dict for consistent fingerprinting."""
        normalized = {}

        # Include key policy fields that affect approval
        for field in ['policy_id', 'domain_name', 'safety_profile', 'model_profile',
                     'approval_required', 'constraints', 'write_scope']:
            if field in policy:
                value = policy[field]
                if isinstance(value, dict):
                    # Recursively normalize nested dicts
                    normalized[field] = self._normalize_policy_for_fingerprint(value)
                elif isinstance(value, list):
                    # Sort lists for consistency
                    normalized[field] = sorted([str(item) for item in value])
                else:
                    normalized[field] = value

        return normalized

    async def request_approval(
        self,
        decision_id: str,
        policy_fingerprint: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Request approval via Kafka events - simplified mock implementation."""
        try:
            # For now, return approval based on simple rules
            # In production, this would send Kafka events and wait for responses
            logger.info(f"Approval request for decision {decision_id} with fingerprint {policy_fingerprint}")

            # Mock approval logic - approve non-restricted operations
            request_metadata = metadata or {}
            if request_metadata.get("policy_preset", "").lower() != "restricted":
                logger.info(f"Auto-approving non-restricted decision {decision_id}")
                return True

            logger.info(f"Manual approval required for decision {decision_id}")
            return False

        except Exception as e:
            logger.error(f"Error in approval request: {e}")
            # Fail-closed for approval errors
            return False


# Global policy client instance
_approval_policy_client = None


def get_approval_policy_client(kafka_producer=None) -> KafkaApprovalPolicyClient:
    """Get global approval policy client instance."""
    global _approval_policy_client
    if _approval_policy_client is None:
        _approval_policy_client = KafkaApprovalPolicyClient(kafka_producer=kafka_producer)
    return _approval_policy_client


# Backward compatibility functions
def approval_required(
    effective_policy: Optional[Dict[str, Any]] = None,
    policy_constraints: Optional[Dict[str, Any]] = None,
    policy_preset: str = "",
    required_policy_ids: Optional[Set[str]] = None,
    required_safety_profiles: Optional[Set[str]] = None
) -> bool:
    """Backward compatibility for approval_required."""
    client = get_approval_policy_client()
    return client.approval_required(
        effective_policy=effective_policy,
        policy_constraints=policy_constraints,
        policy_preset=policy_preset,
        required_policy_ids=required_policy_ids,
        required_safety_profiles=required_safety_profiles
    )


def compute_policy_fingerprint(effective_policy: Dict[str, Any]) -> str:
    """Backward compatibility for compute_policy_fingerprint."""
    client = get_approval_policy_client()
    return client.compute_policy_fingerprint(effective_policy)