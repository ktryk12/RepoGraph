"""
Schema validation infrastructure for ADR-0015 Kafka events.

Provides validation for:
1. Envelope v1 format compliance
2. Payload schema validation
3. Event type routing
4. Schema registry integration (Phase 2+)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union, List
from datetime import datetime
from uuid import UUID

from jsonschema import Draft202012Validator, ValidationError
from jsonschema.exceptions import SchemaError

logger = logging.getLogger(__name__)

class SchemaValidationError(Exception):
    """Schema validation failed."""
    def __init__(self, message: str, errors: List[str] = None):
        super().__init__(message)
        self.errors = errors or []

class EventEnvelopeValidator:
    """Validates Kafka event envelope v1 format."""

    def __init__(self):
        self.envelope_schema = self._load_envelope_schema()
        self.envelope_validator = Draft202012Validator(self.envelope_schema)

    def _load_envelope_schema(self) -> Dict[str, Any]:
        """Load the envelope v1 schema."""
        schema_path = Path(__file__).parent.parent.parent / "schemas" / "kafka" / "envelope_v1.schema.json"
        if not schema_path.exists():
            raise RuntimeError(f"Envelope schema not found: {schema_path}")

        with open(schema_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def validate_envelope(self, event: Dict[str, Any]) -> None:
        """
        Validate event envelope format.

        Args:
            event: Event to validate

        Raises:
            SchemaValidationError: If envelope is invalid
        """
        try:
            self.envelope_validator.validate(event)
        except ValidationError as e:
            raise SchemaValidationError(
                f"Invalid envelope format: {e.message}",
                [str(e)]
            )

        # Additional envelope validations
        self._validate_envelope_consistency(event)

    def _validate_envelope_consistency(self, event: Dict[str, Any]) -> None:
        """Additional envelope validation beyond JSON schema."""
        errors = []

        # Validate event_type format
        event_type = event.get("event_type", "")
        if not self._is_valid_event_type(event_type):
            errors.append(f"Invalid event_type format: {event_type}")

        # Validate UUIDs
        for field in ["event_id", "correlation_id", "causation_id"]:
            if field in event:
                try:
                    UUID(event[field])
                except (ValueError, TypeError):
                    errors.append(f"Invalid UUID format for {field}: {event[field]}")

        # Validate timestamp
        try:
            datetime.fromisoformat(event["occurred_at"].replace('Z', '+00:00'))
        except (ValueError, KeyError):
            errors.append(f"Invalid occurred_at timestamp: {event.get('occurred_at')}")

        if errors:
            raise SchemaValidationError(f"Envelope validation failed: {'; '.join(errors)}", errors)

    def _is_valid_event_type(self, event_type: str) -> bool:
        """Check if event_type follows domain.entity[.action].version format."""
        parts = event_type.split('.')
        if len(parts) < 3 or len(parts) > 4:
            return False

        # Last part must be version (v1, v2, etc.)
        if not parts[-1].startswith('v') or not parts[-1][1:].isdigit():
            return False

        # All other parts should be alphanumeric with underscores
        for part in parts[:-1]:
            if not part.replace('_', '').isalnum():
                return False

        return True

class PayloadValidator:
    """Validates event payloads against their schemas."""

    def __init__(self, schemas_dir: Optional[Path] = None):
        self.schemas_dir = schemas_dir or (Path(__file__).parent.parent.parent / "schemas")
        self.schema_cache: Dict[str, Dict[str, Any]] = {}
        self.validator_cache: Dict[str, Draft202012Validator] = {}

    def validate_payload(self, event_type: str, payload: Dict[str, Any]) -> None:
        """
        Validate event payload against its schema.

        Args:
            event_type: Type of event (e.g., evaluation.started.v1)
            payload: Payload to validate

        Raises:
            SchemaValidationError: If payload is invalid
        """
        schema_path = self._get_schema_path(event_type)
        if not schema_path or not schema_path.exists():
            logger.warning(f"No schema found for event type: {event_type}")
            return  # Skip validation if no schema available

        validator = self._get_validator(event_type, schema_path)

        try:
            validator.validate(payload)
        except ValidationError as e:
            errors = [f"{'.'.join(map(str, e.absolute_path))}: {e.message}" for e in validator.iter_errors(payload)]
            raise SchemaValidationError(
                f"Payload validation failed for {event_type}",
                errors
            )

    def _get_schema_path(self, event_type: str) -> Optional[Path]:
        """Get schema file path for event type."""
        # Convert event_type to schema path
        # e.g., evaluation.started.v1 -> schemas/evaluation/started_v1.json
        parts = event_type.split('.')
        if len(parts) != 3:
            return None

        domain, entity, version = parts
        schema_file = f"{entity}_{version}.json"
        return self.schemas_dir / domain / schema_file

    def _get_validator(self, event_type: str, schema_path: Path) -> Draft202012Validator:
        """Get cached validator for schema."""
        cache_key = str(schema_path)

        if cache_key not in self.validator_cache:
            schema = self._load_schema(schema_path)
            self.schema_cache[cache_key] = schema
            self.validator_cache[cache_key] = Draft202012Validator(schema)

        return self.validator_cache[cache_key]

    def _load_schema(self, schema_path: Path) -> Dict[str, Any]:
        """Load schema from file."""
        try:
            with open(schema_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            raise SchemaValidationError(f"Failed to load schema {schema_path}: {e}")

class KafkaEventValidator:
    """Complete Kafka event validator combining envelope and payload validation."""

    def __init__(self, schemas_dir: Optional[Path] = None):
        self.envelope_validator = EventEnvelopeValidator()
        self.payload_validator = PayloadValidator(schemas_dir)

    def validate_event(self, raw_message: Union[str, bytes, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validate complete Kafka event.

        Args:
            raw_message: Raw message from Kafka (JSON string, bytes, or dict)

        Returns:
            Parsed and validated event

        Raises:
            SchemaValidationError: If validation fails
        """
        # Parse message
        event = self._parse_message(raw_message)

        # Validate envelope
        self.envelope_validator.validate_envelope(event)

        # Validate payload
        event_type = event["event_type"]
        payload = event["payload"]
        self.payload_validator.validate_payload(event_type, payload)

        return event

    def _parse_message(self, raw_message: Union[str, bytes, Dict[str, Any]]) -> Dict[str, Any]:
        """Parse raw message to dict."""
        if isinstance(raw_message, dict):
            return raw_message

        if isinstance(raw_message, bytes):
            raw_message = raw_message.decode('utf-8')

        try:
            return json.loads(raw_message)
        except json.JSONDecodeError as e:
            raise SchemaValidationError(f"Invalid JSON message: {e}")

    def validate_producer_contract(self, service_name: str, event_type: str) -> None:
        """
        Validate that service is allowed to produce this event type.

        Args:
            service_name: Name of producing service
            event_type: Type of event being produced

        Raises:
            SchemaValidationError: If service not authorized to produce event
        """
        # This could be enhanced with contract registry in Phase 2+
        # For now, do basic domain validation
        domain = event_type.split('.')[0]

        # Basic authorization rules (can be expanded)
        authorized_producers = {
            'orchestrator-worker': ['evaluation', 'artifact'],
            'context-plane': ['evaluation'],
            'expert-serving': ['evaluation'],
            'tool-runtime': ['evaluation'],
            'artifact-writer': ['artifact'],
        }

        allowed_domains = authorized_producers.get(service_name, [])
        if domain not in allowed_domains:
            raise SchemaValidationError(
                f"Service {service_name} not authorized to produce {domain} events"
            )

# Convenience functions
def validate_kafka_event(raw_message: Union[str, bytes, Dict[str, Any]],
                        schemas_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Validate complete Kafka event (convenience function)."""
    validator = KafkaEventValidator(schemas_dir)
    return validator.validate_event(raw_message)

def validate_event_envelope(event: Dict[str, Any]) -> None:
    """Validate event envelope only (convenience function)."""
    validator = EventEnvelopeValidator()
    validator.validate_envelope(event)

def validate_event_payload(event_type: str, payload: Dict[str, Any],
                          schemas_dir: Optional[Path] = None) -> None:
    """Validate event payload only (convenience function)."""
    validator = PayloadValidator(schemas_dir)
    validator.validate_payload(event_type, payload)