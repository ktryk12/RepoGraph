"""
Context Plane Contracts and Validation

Implements the contract validation that was previously in aesa.contracts.
This provides request/response validation and contract definitions for the context-plane service.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Union
from dataclasses import dataclass
import json

logger = logging.getLogger(__name__)


# Contract validation errors
class ContextPlaneContractValidationError(Exception):
    """Error in context plane contract validation."""
    pass


class IngestContractValidationError(Exception):
    """Error in ingest contract validation."""
    pass


# Response templates
HEALTH_RESPONSE = {
    "status": "healthy",
    "service": "context-plane",
    "version": "0.1.0",
    "timestamp": None  # Will be filled in by service
}

INGEST_REQUEST = {
    "type": "object",
    "required": ["repository_path"],
    "properties": {
        "repository_path": {"type": "string"},
        "force_reindex": {"type": "boolean", "default": False},
        "include_patterns": {"type": "array", "items": {"type": "string"}},
        "exclude_patterns": {"type": "array", "items": {"type": "string"}},
        "metadata": {"type": "object"}
    }
}

INGEST_RESPONSE = {
    "type": "object",
    "required": ["success", "repository_path", "timestamp"],
    "properties": {
        "success": {"type": "boolean"},
        "repository_path": {"type": "string"},
        "files_indexed": {"type": "integer"},
        "errors": {"type": "array", "items": {"type": "string"}},
        "timestamp": {"type": "string"},
        "duration_ms": {"type": "integer"},
        "metadata": {"type": "object"}
    }
}

RETRIEVE_REQUEST = {
    "type": "object",
    "required": ["query"],
    "properties": {
        "query": {"type": "string"},
        "repository_path": {"type": "string"},
        "max_results": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
        "min_relevance": {"type": "number", "default": 0.1, "minimum": 0.0, "maximum": 1.0},
        "include_code": {"type": "boolean", "default": True},
        "include_docs": {"type": "boolean", "default": True},
        "context_types": {"type": "array", "items": {"type": "string"}}
    }
}

RETRIEVE_RESPONSE = {
    "type": "object",
    "required": ["success", "query", "results", "timestamp"],
    "properties": {
        "success": {"type": "boolean"},
        "query": {"type": "string"},
        "repository_path": {"type": "string"},
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["content", "source", "relevance_score"],
                "properties": {
                    "content": {"type": "string"},
                    "source": {"type": "string"},
                    "relevance_score": {"type": "number"},
                    "metadata": {"type": "object"},
                    "timestamp": {"type": "string"}
                }
            }
        },
        "total_results": {"type": "integer"},
        "timestamp": {"type": "string"},
        "duration_ms": {"type": "integer"},
        "error": {"type": "string"}
    }
}


def validate_context_plane_contract(contract_type: str, data: Dict[str, Any]) -> bool:
    """Validate context plane contract."""
    try:
        if contract_type == "ingest_request":
            return _validate_against_schema(data, INGEST_REQUEST)
        elif contract_type == "ingest_response":
            return _validate_against_schema(data, INGEST_RESPONSE)
        elif contract_type == "retrieve_request":
            return _validate_against_schema(data, RETRIEVE_REQUEST)
        elif contract_type == "retrieve_response":
            return _validate_against_schema(data, RETRIEVE_RESPONSE)
        elif contract_type == "health_response":
            return _validate_health_response(data)
        else:
            raise ContextPlaneContractValidationError(f"Unknown contract type: {contract_type}")

    except Exception as e:
        logger.error(f"Contract validation failed for {contract_type}: {e}")
        raise ContextPlaneContractValidationError(f"Validation failed: {e}")


def _validate_against_schema(data: Dict[str, Any], schema: Dict[str, Any]) -> bool:
    """Validate data against a simple schema."""
    try:
        # Check required fields
        required_fields = schema.get("required", [])
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Missing required field: {field}")

        # Check properties
        properties = schema.get("properties", {})
        for field, value in data.items():
            if field in properties:
                field_schema = properties[field]
                if not _validate_field_type(value, field_schema):
                    raise ValueError(f"Invalid type for field {field}")

        return True

    except Exception as e:
        logger.error(f"Schema validation failed: {e}")
        raise


def _validate_field_type(value: Any, field_schema: Dict[str, Any]) -> bool:
    """Validate a field against its schema."""
    expected_type = field_schema.get("type")

    if expected_type == "string":
        return isinstance(value, str)
    elif expected_type == "integer":
        return isinstance(value, int)
    elif expected_type == "number":
        return isinstance(value, (int, float))
    elif expected_type == "boolean":
        return isinstance(value, bool)
    elif expected_type == "array":
        return isinstance(value, list)
    elif expected_type == "object":
        return isinstance(value, dict)
    else:
        # Unknown type, assume valid
        return True


def _validate_health_response(data: Dict[str, Any]) -> bool:
    """Validate health response format."""
    required_fields = ["status", "service", "version"]
    for field in required_fields:
        if field not in data:
            raise ValueError(f"Missing required health field: {field}")

    if data["status"] not in ["healthy", "unhealthy", "degraded"]:
        raise ValueError(f"Invalid health status: {data['status']}")

    return True


# Ingest contracts service
class IngestContractsService:
    """Service for managing ingest contracts."""

    def __init__(self):
        logger.info("IngestContractsService initialized")

    def validate_ingest_request(self, data: Dict[str, Any]) -> bool:
        """Validate an ingest request."""
        try:
            return validate_context_plane_contract("ingest_request", data)
        except Exception as e:
            raise IngestContractValidationError(f"Ingest request validation failed: {e}")

    def validate_ingest_response(self, data: Dict[str, Any]) -> bool:
        """Validate an ingest response."""
        try:
            return validate_context_plane_contract("ingest_response", data)
        except Exception as e:
            raise IngestContractValidationError(f"Ingest response validation failed: {e}")

    def get_ingest_request_schema(self) -> Dict[str, Any]:
        """Get the ingest request schema."""
        return INGEST_REQUEST.copy()

    def get_ingest_response_schema(self) -> Dict[str, Any]:
        """Get the ingest response schema."""
        return INGEST_RESPONSE.copy()


# Global instance
_ingest_contracts_service = None


def get_ingest_contracts_service() -> IngestContractsService:
    """Get the global ingest contracts service."""
    global _ingest_contracts_service
    if _ingest_contracts_service is None:
        _ingest_contracts_service = IngestContractsService()
    return _ingest_contracts_service


# Validation helper functions
def validate_ingest_request(data: Dict[str, Any]) -> bool:
    """Helper function to validate ingest request."""
    return get_ingest_contracts_service().validate_ingest_request(data)


def validate_ingest_response(data: Dict[str, Any]) -> bool:
    """Helper function to validate ingest response."""
    return get_ingest_contracts_service().validate_ingest_response(data)