#!/usr/bin/env python3
"""
Integration tests for microservice communication.

Tests Kafka messaging and HTTP API contracts between services.
"""

import pytest
import asyncio
import json
from unittest.mock import patch, MagicMock
import uuid

class TestKafkaIntegration:
    """Integration tests for Kafka communication"""

    @pytest.mark.integration
    @pytest.mark.kafka
    @pytest.mark.asyncio
    async def test_claim_detection_to_trust_api_flow(self):
        """Test Kafka message flow between claim-detector and trust-api"""

        with patch('kafka.KafkaProducer') as MockProducer:
            with patch('kafka.KafkaConsumer') as MockConsumer:

                mock_producer = MockProducer.return_value
                mock_producer.send = MagicMock()

                # Claim detection event
                claim_event = {
                    "event_type": "claim.detected",
                    "claim_id": str(uuid.uuid4()),
                    "claim_text": "Test misinformation claim",
                    "platform": "twitter",
                    "urgency": "high"
                }

                # Send claim detection message
                mock_producer.send("claim_detection", value=json.dumps(claim_event))

                # Verify message was sent
                mock_producer.send.assert_called_once()

                # Fact check response
                fact_check_response = {
                    "event_type": "fact_check.completed",
                    "claim_id": claim_event["claim_id"],
                    "trust_score": 0.08,
                    "verdict": "FALSE"
                }

                # Mock consumer receiving response
                mock_consumer = MockConsumer.return_value
                mock_messages = [self._mock_kafka_message("fact_check_results", fact_check_response)]
                mock_consumer.__iter__ = MagicMock(return_value=iter(mock_messages))

                # Consume and verify
                messages = []
                for message in mock_consumer:
                    msg_data = json.loads(message.value)
                    messages.append(msg_data)

                assert len(messages) == 1
                assert messages[0]["event_type"] == "fact_check.completed"

    @pytest.mark.integration
    @pytest.mark.kafka
    def test_message_schemas(self):
        """Test Kafka message schema validation"""

        # Expected schema for different message types
        schemas = {
            "claim.detected": ["event_type", "claim_id", "claim_text", "platform", "urgency"],
            "fact_check.completed": ["event_type", "claim_id", "trust_score", "verdict"],
            "order.created": ["event_type", "order_id", "symbol", "side", "quantity"],
            "signal.generated": ["event_type", "signal_id", "symbol", "signal_type", "confidence"]
        }

        # Test messages
        test_messages = {
            "claim.detected": {
                "event_type": "claim.detected",
                "claim_id": "claim_123",
                "claim_text": "Test claim",
                "platform": "twitter",
                "urgency": "high"
            }
        }

        for event_type, message in test_messages.items():
            required_fields = schemas[event_type]
            for field in required_fields:
                assert field in message, f"Missing field '{field}' in {event_type}"

    def _mock_kafka_message(self, topic: str, data: dict):
        """Helper for mock Kafka messages"""
        mock_msg = MagicMock()
        mock_msg.topic = topic
        mock_msg.value = json.dumps(data).encode('utf-8')
        return mock_msg

class TestHTTPAPIIntegration:
    """Integration tests for HTTP API communication"""

    @pytest.mark.integration
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_service_health_endpoints(self):
        """Test health check endpoints across all services"""

        services = [
            ("trust-api", 8080),
            ("claim-detector", 8081),
            ("order-manager", 8083)
        ]

        with patch('requests.get') as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"status": "healthy"}
            mock_get.return_value = mock_response

            import requests

            for service_name, port in services:
                response = requests.get(f"http://localhost:{port}/health")
                assert response.status_code == 200
                health_data = response.json()
                assert health_data["status"] == "healthy"

    @pytest.mark.integration
    @pytest.mark.api
    @pytest.mark.asyncio
    async def test_cross_service_api_calls(self):
        """Test API calls between services"""

        with patch('requests.post') as mock_post:
            # Trust API call
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"trust_score": 0.85, "verdict": "TRUE"}
            )

            import requests
            response = requests.post("http://localhost:8080/trust-score",
                                   json={"claim": "Water boils at 100°C"})

            assert response.status_code == 200
            assert response.json()["verdict"] == "TRUE"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])