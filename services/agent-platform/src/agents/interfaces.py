"""
Protocol definitions for agent system contracts.

Naming convention:
- Protocols end in "Like" to avoid collision with concrete classes
- Import storage contracts from their canonical location
"""
from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable

from babyai_shared.storage.context_store import ContextStore


@runtime_checkable
class HybridGenerator(Protocol):
    """
    Contract for architecture decision generators.

    Input: EVAL task format
    Output: ArchitectureDecision dict (schema-compatible)
    """

    def __call__(self, eval_task: Dict[str, Any]) -> Dict[str, Any]:
        ...


@runtime_checkable
class DecisionEvaluator(Protocol):
    """
    Contract for decision validation.
    """

    def validate(self, decision: Dict[str, Any], eval_task: Dict[str, Any]) -> Dict[str, Any]:
        ...


@runtime_checkable
class AgentLike(Protocol):
    """
    Base contract for all agents.
    """

    agent_id: str
    role: str

    def process(self, message: Any, context: Any) -> list[Any]:
        ...

    def can_handle(self, message_type: Any) -> bool:
        ...


__all__ = [
    "HybridGenerator",
    "DecisionEvaluator",
    "AgentLike",
    "ContextStore",
]
