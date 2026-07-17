"""
Test configuration for agent-platform.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client():
    """Test client fixture."""
    return TestClient(app)


@pytest.fixture
def test_config():
    """Test configuration fixture."""
    return {
        "service": {
            "name": "agent-platform",
            "port": 8080
        },
        "kafka": {
            "bootstrap_servers": "localhost:9092"
        }
    }
