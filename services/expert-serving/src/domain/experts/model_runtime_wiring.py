"""
Model Runtime Wiring for Expert Serving

Implements model runtime configuration instead of direct AESA imports.
Following ADR-0015 contract-based communication patterns.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Mapping, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ModelRuntime:
    """Model runtime configuration."""

    models_path: str = "/app/models"
    available_models: List[str] = None

    def __post_init__(self):
        if self.available_models is None:
            self.available_models = ["mamba-gpt-7b-Q2_K.gguf", "llama-2-7b-chat.gguf"]

    def list_models(self) -> List[str]:
        """List available models."""
        return self.available_models.copy()

    def predict(self, model_id: str, features: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Mock prediction implementation."""
        logger.info(f"Mock prediction for model {model_id} with features: {list(features.keys())}")

        # Mock prediction response
        return {
            "prediction_class": "positive",
            "confidence": 0.85,
            "model_used": model_id,
            "features_processed": len(features),
            "metadata": {
                "runtime": "expert-serving-local",
                "version": "1.0.0"
            }
        }


@dataclass
class ExpertServingServiceRuntime:
    """Expert serving service runtime configuration."""

    model_runtime: ModelRuntime
    api_key: Optional[str] = None
    debug: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            'models_path': self.model_runtime.models_path,
            'available_models': self.model_runtime.available_models,
            'api_key': self.api_key is not None,
            'debug': self.debug
        }


def build_expert_serving_service_runtime(
    env: Optional[Mapping[str, str]] = None,
    models_path: Optional[str] = None
) -> ExpertServingServiceRuntime:
    """Build expert serving service runtime configuration."""

    source_env = env or os.environ

    runtime_models_path = models_path or source_env.get('EXPERT_SERVING_MODELS_PATH', '/app/models')
    api_key = source_env.get('EXPERT_SERVING_API_KEY')
    debug = source_env.get('EXPERT_SERVING_DEBUG', 'false').lower() in ('true', '1', 'yes')

    # Discover available models
    available_models = []
    try:
        if os.path.exists(runtime_models_path):
            for item in os.listdir(runtime_models_path):
                if item.endswith('.gguf') or item.endswith('.bin'):
                    available_models.append(item)
    except Exception as e:
        logger.warning(f"Could not discover models in {runtime_models_path}: {e}")

    # Default models if none found
    if not available_models:
        available_models = ["mamba-gpt-7b-Q2_K.gguf", "llama-2-7b-chat.gguf"]
        logger.info("Using default model list")

    model_runtime = ModelRuntime(
        models_path=runtime_models_path,
        available_models=available_models
    )

    runtime = ExpertServingServiceRuntime(
        model_runtime=model_runtime,
        api_key=api_key,
        debug=debug
    )

    logger.info(f"Built expert serving runtime: {len(available_models)} models at {runtime_models_path}")

    return runtime