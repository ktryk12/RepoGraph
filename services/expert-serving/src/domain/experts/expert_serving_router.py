"""
Expert Serving Router for Expert Serving Service

Implements model routing and selection instead of direct AESA imports.
Following ADR-0015 contract-based communication patterns.
"""

from __future__ import annotations

import logging
from typing import Mapping, Optional

logger = logging.getLogger(__name__)


class ModelNotAvailableError(Exception):
    """Exception raised when requested model profile is not available."""
    pass


def resolve_model_url(model_profile: str, env: Optional[Mapping[str, str]] = None) -> str:
    """Resolve model URL based on profile."""
    source_env = env or {}

    # Normalize profile
    profile = str(model_profile or "").strip().lower() or "general"

    # Profile-specific URL mappings
    url_mappings = {
        "general": source_env.get("MODEL_RUNNER_BASE_URL", "http://model-manager:8112"),
        "fast": source_env.get("MODEL_RUNNER_FAST_URL", "http://model-manager-fast:8112"),
        "precise": source_env.get("MODEL_RUNNER_PRECISE_URL", "http://model-manager-precise:8112"),
        "creative": source_env.get("MODEL_RUNNER_CREATIVE_URL", "http://model-manager-creative:8112"),
        "analytical": source_env.get("MODEL_RUNNER_ANALYTICAL_URL", "http://model-manager-analytical:8112"),
    }

    # Get URL for profile or fallback to general
    url = url_mappings.get(profile) or url_mappings["general"]

    logger.debug(f"Resolved model profile '{profile}' to URL: {url}")

    return url


def select_expert_model(
    purpose: str = "default",
    context_id: str = "dev",
    model_profile: Optional[str] = None
) -> str:
    """Select appropriate expert model based on purpose and context."""

    profile = str(model_profile or "").strip().lower() or "general"

    # Model selection logic based on profile and purpose
    if profile == "fast":
        return "mamba-gpt-3b-Q4_K.gguf"
    elif profile == "precise":
        return "llama-2-13b-chat.gguf"
    elif profile == "creative":
        return "mistral-7b-instruct.gguf"
    elif profile == "analytical":
        return "codellama-7b-instruct.gguf"
    else:
        # Default general model
        return "mamba-gpt-7b-Q2_K.gguf"


def validate_model_availability(
    model_ref: str,
    base_url: str,
    timeout_seconds: float = 5.0
) -> bool:
    """Validate that a model is available at the given URL."""
    try:
        # Mock validation - in production this would check model availability
        logger.debug(f"Validating model '{model_ref}' at {base_url} (timeout: {timeout_seconds}s)")

        # For now, assume models are available if base URL looks valid
        if not base_url or not base_url.startswith('http'):
            return False

        # Mock successful validation
        return True

    except Exception as e:
        logger.warning(f"Model validation failed for '{model_ref}' at {base_url}: {e}")
        return False