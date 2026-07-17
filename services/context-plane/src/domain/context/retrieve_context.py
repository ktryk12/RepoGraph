"""
Context Retrieval Use Cases

Implements the context retrieval domain logic that was previously in aesa.application.use_cases.
This provides context extraction, indexing, and retrieval capabilities for the context-plane service.
"""

from __future__ import annotations

import logging
import json
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class AgentContextRequest:
    """Request for agent context retrieval."""

    task_description: str
    task_type: str = "general"
    focus_doc_id: Optional[str] = None
    max_tokens: int = 4096
    consumer: str = "codestral"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'task_description': self.task_description,
            'task_type': self.task_type,
            'focus_doc_id': self.focus_doc_id,
            'max_tokens': self.max_tokens,
            'consumer': self.consumer
        }

    @property
    def query(self) -> str:
        """Backward compatibility - map task_description to query."""
        return self.task_description

    @property
    def agent_id(self) -> Optional[str]:
        """Backward compatibility - map focus_doc_id to agent_id."""
        return self.focus_doc_id

    @property
    def max_results(self) -> int:
        """Backward compatibility - derive max_results from max_tokens."""
        return min(20, max(1, self.max_tokens // 200))  # Rough estimation


@dataclass
class ContextResult:
    """Result from context retrieval."""

    content: str
    source: str
    relevance_score: float
    metadata: Dict[str, Any]
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        return {
            'content': self.content,
            'source': self.source,
            'relevance_score': self.relevance_score,
            'metadata': self.metadata,
            'timestamp': self.timestamp.isoformat()
        }


@dataclass
class AgentContextResponse:
    """Response from agent context retrieval."""

    task_description: str
    task_type: str
    results: List[ContextResult]
    draft_quality: str = "success"
    focus_doc_id: Optional[str] = None
    consumer: str = "codestral"
    timestamp: Optional[str] = None
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'task_description': self.task_description,
            'task_type': self.task_type,
            'results': [result.to_dict() for result in self.results],
            'draft_quality': self.draft_quality,
            'focus_doc_id': self.focus_doc_id,
            'consumer': self.consumer,
            'timestamp': self.timestamp or datetime.now(timezone.utc).isoformat(),
            'success': self.success,
            'error': self.error,
            'total_results': len(self.results)
        }


@dataclass
class RetrieveContextRequest:
    """Generic context retrieval request."""

    query: str
    repository_path: Optional[str] = None
    max_results: int = 20
    min_relevance: float = 0.1
    include_code: bool = True
    include_docs: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            'query': self.query,
            'repository_path': self.repository_path,
            'max_results': self.max_results,
            'min_relevance': self.min_relevance,
            'include_code': self.include_code,
            'include_docs': self.include_docs
        }


class ExpertServingStrategyEngine:
    """Strategy engine for expert serving context retrieval."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 timeout_seconds: float = 60.0, context_store: Any = None):
        self.base_url = base_url or "http://expert-serving:8094"
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.context_store = context_store
        logger.info(f"ExpertServingStrategyEngine initialized with base_url: {self.base_url}")

    async def get_expert_context(self, request: AgentContextRequest) -> List[ContextResult]:
        """Get context for expert serving."""
        try:
            logger.info(f"Getting expert context for query: {request.query}")

            # Mock implementation - replace with actual context retrieval logic
            results = []

            # Simulate context retrieval based on query
            if "code" in request.query.lower():
                results.append(ContextResult(
                    content="// Example code context\nfunction example() { return 'context'; }",
                    source="expert_code_example.js",
                    relevance_score=0.8,
                    metadata={"type": "code", "language": "javascript"},
                    timestamp=datetime.now(timezone.utc)
                ))

            if "documentation" in request.query.lower() or "docs" in request.query.lower():
                results.append(ContextResult(
                    content="This is example documentation for the expert context system.",
                    source="expert_docs.md",
                    relevance_score=0.7,
                    metadata={"type": "documentation"},
                    timestamp=datetime.now(timezone.utc)
                ))

            logger.info(f"Retrieved {len(results)} expert context results")
            return results[:request.max_results]

        except Exception as e:
            logger.error(f"Error getting expert context: {e}")
            return []

    def estimate_context_size(self, query: str) -> int:
        """Estimate the size of context for a query."""
        return len(query) * 10  # Simple estimation


class AgentRetrieveContextUseCase:
    """Use case for agent context retrieval."""

    def __init__(self, context_store: Any = None, retrieve_context_use_case: Optional['RetrieveContextUseCase'] = None,
                 strategy_engine: Optional[ExpertServingStrategyEngine] = None):
        self.context_store = context_store
        self.retrieve_context_use_case = retrieve_context_use_case
        self.strategy_engine = strategy_engine or ExpertServingStrategyEngine()
        logger.info("AgentRetrieveContextUseCase initialized")

    def execute(self, request: AgentContextRequest) -> AgentContextResponse:
        """Execute agent context retrieval."""
        try:
            logger.info(f"Executing agent context retrieval for focus_doc: {request.focus_doc_id}")

            # Mock implementation - get context results
            results = []

            # Simulate context retrieval based on task_description
            if "code" in request.task_description.lower():
                results.append(ContextResult(
                    content="// Example code context\nfunction example() { return 'context'; }",
                    source="expert_code_example.js",
                    relevance_score=0.8,
                    metadata={"type": "code", "language": "javascript"},
                    timestamp=datetime.now(timezone.utc)
                ))

            if "documentation" in request.task_description.lower() or "docs" in request.task_description.lower():
                results.append(ContextResult(
                    content="This is example documentation for the expert context system.",
                    source="expert_docs.md",
                    relevance_score=0.7,
                    metadata={"type": "documentation"},
                    timestamp=datetime.now(timezone.utc)
                ))

            # Limit results based on max_tokens estimation
            max_results = min(20, max(1, request.max_tokens // 200))
            final_results = results[:max_results]

            response = AgentContextResponse(
                task_description=request.task_description,
                task_type=request.task_type,
                results=final_results,
                draft_quality="success" if final_results else "fallback",
                focus_doc_id=request.focus_doc_id,
                consumer=request.consumer,
                timestamp=datetime.now(timezone.utc).isoformat(),
                success=True
            )

            logger.info(f"Agent context retrieval completed: {len(final_results)} results")
            return response

        except Exception as e:
            logger.error(f"Error in agent context retrieval: {e}")
            return AgentContextResponse(
                task_description=request.task_description,
                task_type=request.task_type,
                results=[],
                draft_quality="fallback",
                focus_doc_id=request.focus_doc_id,
                consumer=request.consumer,
                timestamp=datetime.now(timezone.utc).isoformat(),
                success=False,
                error=str(e)
            )


class RetrieveContextUseCase:
    """Generic context retrieval use case."""

    def __init__(self, retriever: Any = None, store: Any = None, publisher: Any = None,
                 failure_mode: str = "fallback_local", context_store: Any = None):
        self.retriever = retriever
        self.store = store
        self.publisher = publisher
        self.failure_mode = failure_mode
        self.context_store = context_store
        logger.info(f"RetrieveContextUseCase initialized with failure_mode: {failure_mode}")

    async def execute(self, request: RetrieveContextRequest) -> Dict[str, Any]:
        """Execute context retrieval."""
        try:
            logger.info(f"Executing context retrieval for query: {request.query}")

            results = []

            # Mock context retrieval - replace with actual implementation
            if request.include_code and "function" in request.query.lower():
                results.append(ContextResult(
                    content=f"function relevantFunction() {{\n  // Related to: {request.query}\n  return 'result';\n}}",
                    source="example.js",
                    relevance_score=0.9,
                    metadata={"type": "code", "language": "javascript", "lines": 3},
                    timestamp=datetime.now(timezone.utc)
                ))

            if request.include_docs:
                results.append(ContextResult(
                    content=f"Documentation related to: {request.query}",
                    source="README.md",
                    relevance_score=0.6,
                    metadata={"type": "documentation", "section": "usage"},
                    timestamp=datetime.now(timezone.utc)
                ))

            # Filter by relevance
            filtered_results = [r for r in results if r.relevance_score >= request.min_relevance]
            final_results = filtered_results[:request.max_results]

            response = {
                'query': request.query,
                'repository_path': request.repository_path,
                'results': [result.to_dict() for result in final_results],
                'total_results': len(final_results),
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'success': True
            }

            logger.info(f"Context retrieval completed: {len(final_results)} results")
            return response

        except Exception as e:
            logger.error(f"Error in context retrieval: {e}")
            return {
                'query': request.query,
                'repository_path': request.repository_path,
                'results': [],
                'total_results': 0,
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'success': False,
                'error': str(e)
            }


# Factory functions for backward compatibility
def create_agent_retrieve_context_use_case() -> AgentRetrieveContextUseCase:
    """Create an agent context retrieval use case."""
    return AgentRetrieveContextUseCase()


def create_retrieve_context_use_case() -> RetrieveContextUseCase:
    """Create a context retrieval use case."""
    return RetrieveContextUseCase()