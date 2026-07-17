"""
Mock policy approval_gate module for orchestrator-worker

Re-exports the actual implementation from domain.approval.policy_client to maintain
backward compatibility while transitioning to event-driven architecture.
"""

# Import actual implementations from the domain package
from domain.approval.policy_client import (
    approval_required,
    compute_policy_fingerprint
)

# Re-export for backward compatibility
__all__ = [
    'approval_required',
    'compute_policy_fingerprint'
]