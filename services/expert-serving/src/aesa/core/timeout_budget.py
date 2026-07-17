"""
Mock AESA timeout_budget module for expert-serving

Re-exports the actual implementation from domain.experts to maintain
backward compatibility while transitioning to event-driven architecture.
"""

# Import actual implementation from the domain package
from domain.experts.timeout_budget import TimeoutBudget

# Re-export for backward compatibility
__all__ = ['TimeoutBudget']