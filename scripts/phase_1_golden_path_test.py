#!/usr/bin/env python3
"""
Golden Path Test for ADR-0015 Phase 1: Kafka Foundation

Tests the complete decision pipeline:
decision.intent.v1 -> decision.requested.v1 -> evaluation.started.v1 -> evaluation.completed.v1

This validates:
1. All schemas are valid and loadable
2. Envelope v1 format compliance
3. Event type routing works correctly
4. Correlation IDs propagate through pipeline
5. Producer/consumer contracts are valid

Usage:
    python scripts/phase_1_golden_path_test.py [--verbose]
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from typing import Dict, Any

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from babyai_shared.kafka.schema_validator import (
    KafkaEventValidator,
    SchemaValidationError,
    validate_kafka_event
)

logger = logging.getLogger(__name__)

class GoldenPathTest:
    """Tests the complete decision processing pipeline."""

    def __init__(self):
        self.validator = KafkaEventValidator()
        self.correlation_id = str(uuid4())
        self.decision_id = str(uuid4())
        self.evaluation_id = str(uuid4())

    def run_test(self) -> bool:
        """
        Run the complete golden path test.

        Returns:
            True if all tests pass, False otherwise
        """
        try:
            logger.info("Starting Phase 1 Golden Path Test...")
            logger.info(f"Correlation ID: {self.correlation_id}")
            logger.info(f"Decision ID: {self.decision_id}")

            # Test 1: Schema Loading
            self.test_schema_loading()

            # Test 2: Decision Request Event
            decision_event = self.test_decision_requested()

            # Test 3: Evaluation Started Event
            evaluation_started_event = self.test_evaluation_started()

            # Test 4: Context Request/Response
            context_requested_event = self.test_context_requested()
            context_retrieved_event = self.test_context_retrieved()

            # Test 5: Inference Request/Response
            inference_requested_event = self.test_inference_requested()
            inference_completed_event = self.test_inference_completed()

            # Test 6: Tool Request/Response
            tool_requested_event = self.test_tool_requested()
            tool_completed_event = self.test_tool_completed()

            # Test 7: Artifact Request/Response
            artifact_requested_event = self.test_artifact_requested()
            artifact_completed_event = self.test_artifact_completed()

            # Test 8: Evaluation Completed Event
            evaluation_completed_event = self.test_evaluation_completed()

            # Test 9: Correlation ID Propagation
            self.test_correlation_propagation([
                decision_event, evaluation_started_event, context_requested_event,
                context_retrieved_event, inference_requested_event, inference_completed_event,
                tool_requested_event, tool_completed_event, artifact_requested_event,
                artifact_completed_event, evaluation_completed_event
            ])

            # Test 10: Producer Authorization
            self.test_producer_authorization()

            logger.info("SUCCESS: All golden path tests passed!")
            return True

        except Exception as e:
            logger.error(f"FAILED: Golden path test failed: {e}")
            return False

    def test_schema_loading(self):
        """Test that all required schemas can be loaded."""
        logger.info("Testing schema loading...")

        required_schemas = [
            "schemas/kafka/envelope_v1.schema.json",
            "schemas/decision/requested_v1.json",
            "schemas/evaluation/started_v1.json",
            "schemas/evaluation/context_requested_v1.json",
            "schemas/evaluation/context_retrieved_v1.json",
            "schemas/evaluation/inference_requested_v1.json",
            "schemas/evaluation/inference_completed_v1.json",
            "schemas/evaluation/tool_requested_v1.json",
            "schemas/evaluation/tool_completed_v1.json",
            "schemas/artifact/write_requested_v1.json",
            "schemas/artifact/write_completed_v1.json",
            "schemas/evaluation/completed_v1.json",
        ]

        project_root = Path(__file__).parent.parent
        for schema_path in required_schemas:
            full_path = project_root / schema_path
            if not full_path.exists():
                raise FileNotFoundError(f"Required schema missing: {schema_path}")

            # Verify schema is valid JSON
            with open(full_path, 'r', encoding='utf-8') as f:
                json.load(f)

        logger.info("SUCCESS: All schemas loaded successfully")

    def test_decision_requested(self) -> Dict[str, Any]:
        """Test decision.requested.v1 event validation."""
        logger.info("Testing decision.requested.v1 event...")

        event = self._create_envelope(
            event_type="decision.requested.v1",
            producer="planner",
            payload={
                "decision_id": self.decision_id,
                "task_description": "Implement a new feature for user authentication",
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "truthpack": {
                    "version": "1.0.0",
                    "validated_at": datetime.now(timezone.utc).isoformat(),
                    "requirements": {
                        "constraints": ["Must be secure", "Must be scalable"],
                        "preferences": {"framework": "FastAPI"},
                        "success_criteria": ["Tests pass", "Security audit passes"]
                    },
                    "context": {
                        "background": "Current auth system is outdated",
                        "related_decisions": [],
                        "external_factors": ["Compliance requirements"]
                    }
                },
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456",
                "priority": "normal"
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: decision.requested.v1 validated successfully")
        return validated_event

    def test_evaluation_started(self) -> Dict[str, Any]:
        """Test evaluation.started.v1 event validation."""
        logger.info("Testing evaluation.started.v1 event...")

        event = self._create_envelope(
            event_type="evaluation.started.v1",
            producer="orchestrator-worker",
            payload={
                "decision_id": self.decision_id,
                "evaluation_id": self.evaluation_id,
                "episode_config": {
                    "task_type": "coding",
                    "timeout_seconds": 3600,
                    "priority": "normal",
                    "context_requirements": {
                        "max_context_tokens": 50000,
                        "include_history": True,
                        "include_artifacts": True
                    },
                    "inference_requirements": {
                        "model_family": "claude",
                        "max_tokens": 8000,
                        "temperature": 0.7
                    }
                },
                "started_at": datetime.now(timezone.utc).isoformat(),
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456",
                "approval_status": "approved",
                "policy_fingerprint": "sha256:abcd1234"
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: evaluation.started.v1 validated successfully")
        return validated_event

    def test_context_requested(self) -> Dict[str, Any]:
        """Test evaluation.context.requested.v1 event validation."""
        logger.info("Testing evaluation.context.requested.v1 event...")

        event = self._create_envelope(
            event_type="evaluation.context.requested.v1",
            producer="orchestrator-worker",
            payload={
                "decision_id": self.decision_id,
                "evaluation_id": self.evaluation_id,
                "context_request_id": str(uuid4()),
                "retrieval_requirements": {
                    "context_types": ["conversation_history", "code_repository"],
                    "max_tokens": 50000,
                    "relevance_filters": {
                        "keywords": ["authentication", "FastAPI"],
                        "entities": ["auth", "user"]
                    }
                },
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456",
                "timeout_seconds": 60
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: evaluation.context.requested.v1 validated successfully")
        return validated_event

    def test_context_retrieved(self) -> Dict[str, Any]:
        """Test evaluation.context.retrieved.v1 event validation."""
        logger.info("Testing evaluation.context.retrieved.v1 event...")

        event = self._create_envelope(
            event_type="evaluation.context.retrieved.v1",
            producer="context-plane",
            payload={
                "decision_id": self.decision_id,
                "evaluation_id": self.evaluation_id,
                "context_request_id": str(uuid4()),
                "status": "success",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "context_pack": {
                    "total_tokens": 15000,
                    "sources": [
                        {
                            "source_type": "conversation_history",
                            "source_id": "conv_123",
                            "content": "Previous discussion about auth requirements...",
                            "tokens": 5000,
                            "relevance_score": 0.85,
                            "retrieved_at": datetime.now(timezone.utc).isoformat()
                        },
                        {
                            "source_type": "code_repository",
                            "source_id": "repo_456",
                            "content": "Current auth implementation...",
                            "tokens": 10000,
                            "relevance_score": 0.90,
                            "retrieved_at": datetime.now(timezone.utc).isoformat()
                        }
                    ],
                    "metadata": {
                        "truncated": False,
                        "retrieval_strategy": "semantic_search",
                        "cache_hit": False,
                        "quality_score": 0.88
                    }
                },
                "performance_metrics": {
                    "retrieval_duration_ms": 1500,
                    "sources_scanned": 100,
                    "sources_matched": 2,
                    "cache_hits": 0
                },
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456"
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: evaluation.context.retrieved.v1 validated successfully")
        return validated_event

    def test_inference_requested(self) -> Dict[str, Any]:
        """Test evaluation.inference.requested.v1 event validation."""
        logger.info("Testing evaluation.inference.requested.v1 event...")

        event = self._create_envelope(
            event_type="evaluation.inference.requested.v1",
            producer="orchestrator-worker",
            payload={
                "decision_id": self.decision_id,
                "evaluation_id": self.evaluation_id,
                "inference_request_id": str(uuid4()),
                "model_requirements": {
                    "capabilities": ["text_generation", "code_generation"],
                    "model_family": "claude",
                    "min_context_window": 200000,
                    "max_tokens": 8000,
                    "temperature": 0.7
                },
                "prompt_context": {
                    "system_prompt": "You are an expert software engineer...",
                    "user_prompt": "Implement FastAPI authentication...",
                    "context_documents": [
                        {
                            "content": "Current auth implementation...",
                            "document_type": "code",
                            "relevance_score": 0.9
                        }
                    ]
                },
                "output_requirements": {
                    "format": "code",
                    "min_length": 100,
                    "max_length": 8000
                },
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "timeout_seconds": 300,
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456"
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: evaluation.inference.requested.v1 validated successfully")
        return validated_event

    def test_inference_completed(self) -> Dict[str, Any]:
        """Test evaluation.inference.completed.v1 event validation."""
        logger.info("Testing evaluation.inference.completed.v1 event...")

        event = self._create_envelope(
            event_type="evaluation.inference.completed.v1",
            producer="expert-serving",
            payload={
                "decision_id": self.decision_id,
                "evaluation_id": self.evaluation_id,
                "inference_request_id": str(uuid4()),
                "status": "success",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "inference_result": {
                    "generated_text": "from fastapi import FastAPI, Depends...",
                    "model_used": "claude-3-sonnet",
                    "token_usage": {
                        "prompt_tokens": 2000,
                        "completion_tokens": 1500,
                        "total_tokens": 3500
                    },
                    "quality_metrics": {
                        "confidence_score": 0.85,
                        "coherence_score": 0.90,
                        "relevance_score": 0.88,
                        "safety_flags": []
                    },
                    "generation_parameters": {
                        "temperature": 0.7,
                        "max_tokens": 8000
                    }
                },
                "performance_metrics": {
                    "inference_duration_ms": 5000,
                    "queue_wait_ms": 100,
                    "tokens_per_second": 300.0,
                    "cost_usd": 0.05
                },
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456"
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: evaluation.inference.completed.v1 validated successfully")
        return validated_event

    def test_tool_requested(self) -> Dict[str, Any]:
        """Test evaluation.tool.requested.v1 event validation."""
        logger.info("Testing evaluation.tool.requested.v1 event...")

        event = self._create_envelope(
            event_type="evaluation.tool.requested.v1",
            producer="orchestrator-worker",
            payload={
                "decision_id": self.decision_id,
                "evaluation_id": self.evaluation_id,
                "tool_request_id": str(uuid4()),
                "tool_spec": {
                    "tool_name": "python_executor",
                    "tool_version": "1.0.0",
                    "parameters": {
                        "script": "print('Testing auth implementation')",
                        "requirements": ["fastapi", "pytest"]
                    },
                    "environment": {
                        "PYTHONPATH": "/app"
                    },
                    "working_directory": "/tmp/workspace"
                },
                "execution_requirements": {
                    "timeout_seconds": 300,
                    "memory_limit_mb": 512,
                    "cpu_limit": 1.0,
                    "network_access": False,
                    "file_system_access": "read_write",
                    "isolation_level": "container"
                },
                "output_requirements": {
                    "capture_stdout": True,
                    "capture_stderr": True,
                    "output_format": "text",
                    "max_output_size_mb": 10,
                    "streaming": False
                },
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456",
                "security_context": {
                    "user_permissions": ["execute_code"],
                    "approval_level": "user_approved",
                    "audit_required": True,
                    "sensitive_data": False
                }
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: evaluation.tool.requested.v1 validated successfully")
        return validated_event

    def test_tool_completed(self) -> Dict[str, Any]:
        """Test evaluation.tool.completed.v1 event validation."""
        logger.info("Testing evaluation.tool.completed.v1 event...")

        event = self._create_envelope(
            event_type="evaluation.tool.completed.v1",
            producer="tool-runtime",
            payload={
                "decision_id": self.decision_id,
                "evaluation_id": self.evaluation_id,
                "tool_request_id": str(uuid4()),
                "status": "success",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "execution_result": {
                    "exit_code": 0,
                    "stdout": "Testing auth implementation\nAll tests passed!",
                    "stderr": "",
                    "output_files": [
                        {
                            "file_path": "/tmp/workspace/auth.py",
                            "file_size": 2048,
                            "file_type": "python",
                            "checksum": "sha256:def456"
                        }
                    ],
                    "structured_output": {
                        "test_results": {
                            "passed": 5,
                            "failed": 0,
                            "coverage": 95.0
                        }
                    }
                },
                "performance_metrics": {
                    "execution_duration_ms": 15000,
                    "setup_duration_ms": 2000,
                    "cleanup_duration_ms": 1000,
                    "memory_used_mb": 128.5,
                    "cpu_usage_percent": 45.0,
                    "cost_usd": 0.01
                },
                "security_audit": {
                    "commands_executed": ["python", "pytest"],
                    "files_accessed": [
                        {
                            "file_path": "/tmp/workspace/auth.py",
                            "access_type": "write"
                        }
                    ],
                    "network_connections": [],
                    "security_violations": [],
                    "isolation_breaches": []
                },
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456"
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: evaluation.tool.completed.v1 validated successfully")
        return validated_event

    def test_artifact_requested(self) -> Dict[str, Any]:
        """Test artifact.write.requested.v1 event validation."""
        logger.info("Testing artifact.write.requested.v1 event...")

        event = self._create_envelope(
            event_type="artifact.write.requested.v1",
            producer="orchestrator-worker",
            payload={
                "decision_id": self.decision_id,
                "evaluation_id": self.evaluation_id,
                "write_request_id": str(uuid4()),
                "artifact_spec": {
                    "artifact_type": "code_file",
                    "content": "from fastapi import FastAPI...",
                    "encoding": "utf-8",
                    "file_path": "src/auth/main.py",
                    "file_name": "main.py",
                    "file_extension": "py",
                    "mime_type": "text/x-python",
                    "size_bytes": 2048
                },
                "storage_requirements": {
                    "persistence": "session",
                    "access_level": "user",
                    "versioning": True,
                    "compression": False,
                    "encryption": False,
                    "backup": True
                },
                "metadata": {
                    "title": "FastAPI Authentication Implementation",
                    "description": "New authentication system using FastAPI",
                    "tags": ["auth", "fastapi", "security"],
                    "author": "AI Assistant",
                    "version": "1.0.0",
                    "source_evaluation": self.evaluation_id
                },
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456",
                "validation_requirements": {
                    "syntax_check": True,
                    "content_validation": True,
                    "security_scan": True,
                    "virus_scan": False,
                    "format_validation": True
                }
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: artifact.write.requested.v1 validated successfully")
        return validated_event

    def test_artifact_completed(self) -> Dict[str, Any]:
        """Test artifact.write.completed.v1 event validation."""
        logger.info("Testing artifact.write.completed.v1 event...")

        event = self._create_envelope(
            event_type="artifact.write.completed.v1",
            producer="artifact-writer",
            payload={
                "decision_id": self.decision_id,
                "evaluation_id": self.evaluation_id,
                "write_request_id": str(uuid4()),
                "status": "success",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "artifact_result": {
                    "artifact_id": str(uuid4()),
                    "storage_location": {
                        "storage_type": "local_filesystem",
                        "storage_path": "/artifacts/user_123/session_456/main.py",
                        "storage_url": "https://artifacts.babyai.ai/user_123/session_456/main.py"
                    },
                    "file_metadata": {
                        "final_size_bytes": 2048,
                        "checksum": "sha256:abc123def456",
                        "checksum_algorithm": "sha256",
                        "content_type": "text/x-python",
                        "compression_applied": False,
                        "encryption_applied": False
                    },
                    "access_info": {
                        "access_url": "https://artifacts.babyai.ai/user_123/session_456/main.py",
                        "download_url": "https://artifacts.babyai.ai/download/abc123"
                    },
                    "validation_results": {
                        "syntax_valid": True,
                        "content_valid": True,
                        "security_clean": True,
                        "format_compliant": True,
                        "validation_warnings": []
                    }
                },
                "performance_metrics": {
                    "write_duration_ms": 250,
                    "validation_duration_ms": 500,
                    "upload_duration_ms": 100,
                    "throughput_mbps": 8.0,
                    "storage_cost_usd": 0.001
                },
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456"
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: artifact.write.completed.v1 validated successfully")
        return validated_event

    def test_evaluation_completed(self) -> Dict[str, Any]:
        """Test evaluation.completed.v1 event validation."""
        logger.info("Testing evaluation.completed.v1 event...")

        event = self._create_envelope(
            event_type="evaluation.completed.v1",
            producer="orchestrator-worker",
            payload={
                "decision_id": self.decision_id,
                "evaluation_id": self.evaluation_id,
                "status": "success",
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "execution_summary": {
                    "duration_seconds": 125.5,
                    "iterations_completed": 1,
                    "context_retrievals": 1,
                    "inference_requests": 1,
                    "tool_executions": 1
                },
                "result": {
                    "outcome": "Successfully implemented FastAPI authentication system",
                    "confidence": 0.92,
                    "artifacts_created": [
                        {
                            "artifact_id": str(uuid4()),
                            "type": "code_file",
                            "path": "src/auth/main.py"
                        }
                    ],
                    "metrics": {
                        "passed": True,
                        "score": 0.95,
                        "component_scores": {
                            "functionality": 0.98,
                            "security": 0.95,
                            "performance": 0.92,
                            "maintainability": 0.94
                        }
                    }
                },
                "context_id": str(uuid4()),
                "user_id": "user_123",
                "session_id": "session_456"
            }
        )

        validated_event = self.validator.validate_event(event)
        logger.info("SUCCESS: evaluation.completed.v1 validated successfully")
        return validated_event

    def test_correlation_propagation(self, events: list[Dict[str, Any]]):
        """Test that correlation_id propagates through all events."""
        logger.info("Testing correlation ID propagation...")

        for event in events:
            event_correlation = event.get("correlation_id")
            if event_correlation != self.correlation_id:
                raise AssertionError(
                    f"Correlation ID mismatch in {event['event_type']}: "
                    f"expected {self.correlation_id}, got {event_correlation}"
                )

        logger.info("SUCCESS: Correlation ID propagated correctly through all events")

    def test_producer_authorization(self):
        """Test producer authorization validation."""
        logger.info("Testing producer authorization...")

        # Test valid producers
        valid_cases = [
            ("orchestrator-worker", "evaluation.started.v1"),
            ("context-plane", "evaluation.context.retrieved.v1"),
            ("expert-serving", "evaluation.inference.completed.v1"),
            ("tool-runtime", "evaluation.tool.completed.v1"),
            ("artifact-writer", "artifact.write.completed.v1"),
        ]

        for service, event_type in valid_cases:
            try:
                self.validator.validate_producer_contract(service, event_type)
            except SchemaValidationError:
                raise AssertionError(f"Valid producer rejected: {service} -> {event_type}")

        # Test invalid producers
        invalid_cases = [
            ("context-plane", "artifact.write.requested.v1"),  # Wrong domain
            ("tool-runtime", "decision.requested.v1"),         # Wrong domain
        ]

        for service, event_type in invalid_cases:
            try:
                self.validator.validate_producer_contract(service, event_type)
                raise AssertionError(f"Invalid producer accepted: {service} -> {event_type}")
            except SchemaValidationError:
                pass  # Expected

        logger.info("SUCCESS: Producer authorization working correctly")

    def _create_envelope(self, event_type: str, producer: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a valid event envelope."""
        return {
            "event_id": str(uuid4()),
            "event_type": event_type,
            "event_version": "v1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "producer": producer,
            "correlation_id": self.correlation_id,
            "causation_id": str(uuid4()),
            "payload": payload,
            "idempotency_key": str(uuid4()),
            "user_id": "user_123",
            "session_id": "session_456"
        }

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Run Phase 1 Golden Path Test")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Run test
    test = GoldenPathTest()
    success = test.run_test()

    if success:
        print("\nSUCCESS: Phase 1 Golden Path Test: PASSED")
        print("SUCCESS: All Kafka schemas validated successfully")
        print("SUCCESS: Event envelope format compliance verified")
        print("SUCCESS: Producer/consumer contracts validated")
        print("SUCCESS: Correlation ID propagation working")
        sys.exit(0)
    else:
        print("\nFAILED: Phase 1 Golden Path Test: FAILED")
        sys.exit(1)

if __name__ == "__main__":
    main()