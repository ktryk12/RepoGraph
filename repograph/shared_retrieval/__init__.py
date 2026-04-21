"""Shared retrieval infrastructure — consumer-agnostic context preparation."""

from .adapters import format_for_consumer
from .compressor import CompressedContext, compress
from .gateway import prepare_task_context
from .models import SharedRetrievalRequest, SharedRetrievalResponse
from .profiles import get_profile, profile_for_context, resolve_profile
from .prompt_packer import pack

__all__ = [
    "prepare_task_context",
    "format_for_consumer",
    "compress",
    "CompressedContext",
    "SharedRetrievalRequest",
    "SharedRetrievalResponse",
    "get_profile",
    "resolve_profile",
    "profile_for_context",
    "pack",
]
