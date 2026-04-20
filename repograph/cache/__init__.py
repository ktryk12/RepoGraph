"""Redis hot cache layer — optional, degrades gracefully."""

from . import redis_layer as redis
from .keys import (
    query_hash,
    session_snapshot,
    summary_file,
    summary_l0,
    summary_service,
    summary_symbol,
    task_state,
    verify_last,
    working_set,
)

__all__ = [
    "redis",
    "summary_l0", "summary_service", "summary_file", "summary_symbol",
    "working_set", "task_state", "verify_last", "session_snapshot", "query_hash",
]
