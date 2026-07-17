"""Shared retrieval infrastructure — consumer-agnostic context preparation."""

from .analysis import build_analysis_plan, request_for_analysis_step, select_analysis_step, should_break_down_for_analysis
from .adapters import format_for_consumer
from .compressor import CompressedContext, compress
from .gateway import prepare_task_context
from .models import SharedRetrievalRequest, SharedRetrievalResponse
from .profiles import get_profile, profile_for_context, resolve_profile
from .prompt_packer import pack

__all__ = [
    "prepare_task_context",
    "build_analysis_plan",
    "select_analysis_step",
    "request_for_analysis_step",
    "should_break_down_for_analysis",
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
