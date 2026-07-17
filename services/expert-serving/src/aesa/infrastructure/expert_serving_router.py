"""
Mock AESA expert_serving_router module for expert-serving

Re-exports the actual implementation from domain.experts to maintain
backward compatibility while transitioning to event-driven architecture.
"""

# Import actual implementations from the domain package
from domain.experts.expert_serving_router import (
    ModelNotAvailableError,
    resolve_model_url
)

# Re-export for backward compatibility
__all__ = [
    'ModelNotAvailableError',
    'resolve_model_url'
]