"""
Mock AESA approval module for orchestrator-worker

Re-exports the actual implementation from domain.approval to maintain
backward compatibility while transitioning to event-driven architecture.
"""

# Import actual implementations from the domain package
from domain.approval.execution_permit import (
    ExecutionPermit,
    require_execution_permit_from_mapping
)

# Re-export for backward compatibility
__all__ = [
    'ExecutionPermit',
    'require_execution_permit_from_mapping'
]