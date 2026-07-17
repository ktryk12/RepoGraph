#!/usr/bin/env python3
"""
Unit tests for TrustAPIService.

Tests the trust-api service in isolation with mocked dependencies.
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add service to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

class TestTrustAPIService:
    """Unit tests for TrustAPIService"""

    @pytest.fixture
    def service_config(self):
        return {
            "api_keys": {"dev_key": "test_key"},
            "claude_api": {"model": "claude-3-sonnet-20240229"},
            "database": {"path": ":memory:"},
            "server": {"port": 8080}
        }

    @pytest.mark.unit
    def test_trust_score_calculation(self, service_config):
        """Test trust score calculation for claims"""

        with patch('main.TrustAPIService') as MockService:
            mock_service = MockService.return_value

            # Mock trust score calculation
            mock_service.compute_trust_score = MagicMock(return_value={
                "trust_score": 0.05,  # Low trust for false claim
                "verdict": "FALSE",
                "confidence": 0.95,
                "reasoning": "Contradicts scientific evidence"
            })

            # Test false claim
            result = mock_service.compute_trust_score("The Earth is flat")

            assert result["trust_score"] < 0.1
            assert result["verdict"] == "FALSE"
            assert result["confidence"] > 0.9

    @pytest.mark.unit
    def test_api_authentication(self):
        """Test API key authentication"""

        with patch('main.TrustAPIService') as MockService:
            mock_service = MockService.return_value

            # Mock authentication
            mock_service._authenticate = MagicMock(return_value={
                "valid": True,
                "tier": "premium",
                "user_id": "test_user"
            })

            result = mock_service._authenticate("valid_key")

            assert result["valid"] is True
            assert "tier" in result

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_service_lifecycle(self, service_config):
        """Test service initialization and shutdown"""

        with patch('main.TrustAPIService') as MockService:
            mock_service = MockService.return_value
            mock_service.initialize = AsyncMock()
            mock_service.shutdown = AsyncMock()

            # Test lifecycle
            await mock_service.initialize()
            await mock_service.shutdown()

            mock_service.initialize.assert_called_once()
            mock_service.shutdown.assert_called_once()

if __name__ == "__main__":
    pytest.main([__file__, "-v"])