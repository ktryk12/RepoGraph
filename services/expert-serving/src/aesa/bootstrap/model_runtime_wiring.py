"""
Mock AESA model_runtime_wiring module for expert-serving

Re-exports the actual implementation from domain.experts to maintain
backward compatibility while transitioning to event-driven architecture.
"""

# Import actual implementations from the domain package
from domain.experts.model_runtime_wiring import (
    ExpertServingServiceRuntime,
    build_expert_serving_service_runtime
)

# Re-export for backward compatibility
__all__ = [
    'ExpertServingServiceRuntime',
    'build_expert_serving_service_runtime'
]