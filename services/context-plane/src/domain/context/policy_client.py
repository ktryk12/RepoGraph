"""
Kafka-based Policy Client for Context-Plane

Implements event-driven policy decisions instead of direct service imports.
Following ADR-0015 contract-based communication patterns.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional, Union
from datetime import datetime, timezone
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PolicyRequest:
    """Policy evaluation request."""

    service: str
    operation: str
    resource: str
    request_id: str
    metadata: Dict[str, Any]
    timestamp: str

    def to_event(self) -> Dict[str, Any]:
        """Convert to Kafka event format."""
        return {
            "envelope": {
                "version": "v1",
                "event_type": "policy.approval.request.v1",
                "event_id": str(uuid.uuid4()),
                "correlation_id": self.request_id,
                "timestamp": self.timestamp,
                "source_service": "context-plane"
            },
            "payload": {
                "service": self.service,
                "operation": self.operation,
                "resource": self.resource,
                "request_id": self.request_id,
                "metadata": self.metadata
            }
        }


@dataclass
class PolicyResponse:
    """Policy evaluation response."""

    request_id: str
    approved: bool
    verdict: str
    reason: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @classmethod
    def from_event(cls, event: Dict[str, Any]) -> 'PolicyResponse':
        """Create from Kafka event."""
        payload = event.get("payload", {})
        return cls(
            request_id=payload.get("request_id", ""),
            approved=payload.get("approved", False),
            verdict=payload.get("verdict", "DENY"),
            reason=payload.get("reason"),
            metadata=payload.get("metadata", {})
        )


class PerfBudgetViolation(Exception):
    """Exception for performance budget violations."""
    pass


class KafkaPolicyClient:
    """Kafka-based policy client for event-driven policy decisions."""

    def __init__(self, kafka_producer=None, timeout_seconds: float = 30.0):
        self.kafka_producer = kafka_producer
        self.timeout_seconds = timeout_seconds
        self.pending_requests: Dict[str, asyncio.Future] = {}
        logger.info("KafkaPolicyClient initialized")

    async def request_approval(self, operation: str, resource: str,
                             metadata: Optional[Dict[str, Any]] = None) -> PolicyResponse:
        """Request policy approval via Kafka events."""
        try:
            request_id = str(uuid.uuid4())

            # Create policy request
            policy_request = PolicyRequest(
                service="context-plane",
                operation=operation,
                resource=resource,
                request_id=request_id,
                metadata=metadata or {},
                timestamp=datetime.now(timezone.utc).isoformat()
            )

            # Send policy request event
            if self.kafka_producer:
                try:
                    event = policy_request.to_event()
                    await self.kafka_producer.send('policy.approval.request.v1', event)
                    logger.info(f"Sent policy request for {operation} on {resource}")
                except Exception as e:
                    logger.warning(f"Failed to send policy event, using fallback approval: {e}")
                    return PolicyResponse(
                        request_id=request_id,
                        approved=True,
                        verdict="ALLOW",
                        reason="Fallback approval - policy service unavailable"
                    )

            # Wait for response (with timeout)
            try:
                # Create future for response
                future = asyncio.Future()
                self.pending_requests[request_id] = future

                # Wait for response or timeout
                response = await asyncio.wait_for(future, timeout=self.timeout_seconds)
                return response

            except asyncio.TimeoutError:
                logger.warning(f"Policy request timeout for {operation} on {resource}")
                return PolicyResponse(
                    request_id=request_id,
                    approved=True,  # Fail-open for availability
                    verdict="ALLOW",
                    reason="Timeout - allowing operation to proceed"
                )
            finally:
                # Clean up pending request
                self.pending_requests.pop(request_id, None)

        except Exception as e:
            logger.error(f"Error in policy request: {e}")
            return PolicyResponse(
                request_id=str(uuid.uuid4()),
                approved=True,  # Fail-open
                verdict="ALLOW",
                reason=f"Error in policy evaluation: {e}"
            )

    async def handle_policy_response(self, event: Dict[str, Any]) -> None:
        """Handle incoming policy response event."""
        try:
            response = PolicyResponse.from_event(event)
            request_id = response.request_id

            # Find and complete pending request
            if request_id in self.pending_requests:
                future = self.pending_requests[request_id]
                if not future.done():
                    future.set_result(response)
                    logger.info(f"Completed policy request {request_id}: {response.verdict}")

        except Exception as e:
            logger.error(f"Error handling policy response: {e}")

    def extract_candidates(self, request: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract discovery candidates from request - mock implementation."""
        # This would normally extract repo URLs, file paths, etc. for policy evaluation
        candidates = []

        # Check for common patterns that need policy evaluation
        if "repository_path" in request:
            candidates.append({
                "candidate_id": str(uuid.uuid4()),
                "source_ref": f"repo://{request['repository_path']}",
                "rights_label": "internal"
            })

        if "doc_id" in request:
            candidates.append({
                "candidate_id": str(uuid.uuid4()),
                "source_ref": f"document://{request['doc_id']}",
                "rights_label": "internal"
            })

        return candidates

    async def budgeted_call(self, operation_name: str, func, metadata: Optional[Dict[str, Any]] = None):
        """Execute operation with performance budget checking."""
        try:
            # For now, just execute the function
            # In production, this would check performance budgets via events
            logger.debug(f"Executing budgeted operation: {operation_name}")
            return func()

        except Exception as e:
            logger.error(f"Budgeted call failed for {operation_name}: {e}")
            # Could send performance violation event here
            raise PerfBudgetViolation(f"Performance budget exceeded for {operation_name}: {e}")

    def require_write(self, operation: str, scope: str) -> None:
        """Check if writes are allowed - mock implementation."""
        # In production, this would send killswitch check event
        logger.debug(f"Checking write permission for {operation} in scope {scope}")
        # For now, always allow writes
        pass


# Global policy client instance
_policy_client = None


def get_policy_client(kafka_producer=None) -> KafkaPolicyClient:
    """Get global policy client instance."""
    global _policy_client
    if _policy_client is None:
        _policy_client = KafkaPolicyClient(kafka_producer=kafka_producer)
    return _policy_client


# Backward compatibility functions
async def budgeted_call(operation_name: str, func, metadata: Optional[Dict[str, Any]] = None):
    """Backward compatibility for budgeted_call."""
    client = get_policy_client()
    return await client.budgeted_call(operation_name, func, metadata)


def require_ingest_write_or_503(operation: str) -> None:
    """Backward compatibility for write permission checks."""
    try:
        client = get_policy_client()
        client.require_write(operation, "INGEST_WRITE")
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail={"error": "writes_disabled", "reason": str(e)})