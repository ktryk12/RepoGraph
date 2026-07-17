"""
Pytest configuration for media services tests

Shared fixtures and configuration for video-service, voice-service, and ui-service tests.
"""

import pytest
import asyncio
import sys
from pathlib import Path

# Add service directories to Python path for testing
repo_root = Path(__file__).parent.parent.parent
services_dir = repo_root / "services"

sys.path.insert(0, str(services_dir / "video-service"))
sys.path.insert(0, str(services_dir / "voice-service"))
sys.path.insert(0, str(services_dir / "ui-service"))

# Import shared test utilities
from media_services_test_utils import (
    MockMediaStore,
    MockEventBus,
    MockWebSocketConnection,
    MediaServiceTestBase
)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_store():
    """Shared mock store fixture"""
    return MockMediaStore()


@pytest.fixture
def mock_event_bus():
    """Shared mock event bus fixture"""
    return MockEventBus()


@pytest.fixture
def mock_websocket():
    """Shared mock WebSocket fixture"""
    return MockWebSocketConnection()


@pytest.fixture
def media_test_base():
    """Shared media service test base"""
    return MediaServiceTestBase()


# Pytest markers for categorizing tests
pytest.mark.unit = pytest.mark.unit
pytest.mark.integration = pytest.mark.integration
pytest.mark.async_test = pytest.mark.asyncio


# Configure pytest options
def pytest_configure(config):
    """Configure pytest with custom markers"""
    config.addinivalue_line(
        "markers", "unit: mark test as a unit test"
    )
    config.addinivalue_line(
        "markers", "integration: mark test as an integration test"
    )
    config.addinivalue_line(
        "markers", "async_test: mark test as requiring async support"
    )


# Custom pytest collection hooks
def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers automatically"""
    for item in items:
        # Add async marker to async tests
        if asyncio.iscoroutinefunction(item.function):
            item.add_marker(pytest.mark.asyncio)

        # Add unit marker to tests that don't have integration marker
        if not item.get_closest_marker("integration"):
            item.add_marker(pytest.mark.unit)


# Test environment setup/teardown
@pytest.fixture(autouse=True)
def setup_test_environment():
    """Setup and teardown for each test"""
    # Setup
    yield
    # Teardown (if needed)
    pass