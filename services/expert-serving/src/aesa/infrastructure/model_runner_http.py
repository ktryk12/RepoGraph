"""
Mock AESA model_runner_http module for expert-serving

Re-exports the actual implementation from domain.experts to maintain
backward compatibility while transitioning to event-driven architecture.
"""

# Import actual implementations from the domain package
from domain.experts.model_runner_http import (
    LlamaCppRunnerGateway,
    ModelRunnerHttpError,
    ModelRunnerTimeoutError
)

# Re-export for backward compatibility
__all__ = [
    'LlamaCppRunnerGateway',
    'ModelRunnerHttpError',
    'ModelRunnerTimeoutError'
]