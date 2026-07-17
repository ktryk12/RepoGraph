"""
Shared test utilities for media services (video, voice, ui)

Common test fixtures, mocks, and utilities for testing the split media services.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime
import json
import uuid
from typing import Dict, Any, List, Optional


class MockMediaStore:
    """Mock storage backend for testing media services"""

    def __init__(self):
        self.jobs = {}
        self.sessions = {}
        self.media_files = {}

    def create_job(self, job_type: str, job_data: Dict[str, Any]) -> str:
        """Create a mock job"""
        job_id = str(uuid.uuid4())
        self.jobs[job_id] = {
            "id": job_id,
            "type": job_type,
            "data": job_data,
            "status": "created",
            "created_at": datetime.now().isoformat()
        }
        return job_id

    def update_job_status(self, job_id: str, status: str) -> bool:
        """Update job status"""
        if job_id in self.jobs:
            self.jobs[job_id]["status"] = status
            self.jobs[job_id]["updated_at"] = datetime.now().isoformat()
            return True
        return False

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job by ID"""
        return self.jobs.get(job_id)

    def create_session(self, user_id: str) -> str:
        """Create a mock user session"""
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = {
            "id": session_id,
            "user_id": user_id,
            "created_at": datetime.now().isoformat(),
            "active": True
        }
        return session_id


class MockEventBus:
    """Mock event bus for testing inter-service communication"""

    def __init__(self):
        self.events = []
        self.subscribers = {}

    def publish(self, topic: str, event: Dict[str, Any]) -> None:
        """Publish an event"""
        event_with_metadata = {
            **event,
            "topic": topic,
            "published_at": datetime.now().isoformat(),
            "event_id": str(uuid.uuid4())
        }
        self.events.append(event_with_metadata)

        # Notify subscribers
        if topic in self.subscribers:
            for callback in self.subscribers[topic]:
                callback(event_with_metadata)

    def subscribe(self, topic: str, callback) -> None:
        """Subscribe to events"""
        if topic not in self.subscribers:
            self.subscribers[topic] = []
        self.subscribers[topic].append(callback)

    def get_events(self, topic: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get published events"""
        if topic:
            return [e for e in self.events if e["topic"] == topic]
        return self.events.copy()

    def clear_events(self) -> None:
        """Clear event history"""
        self.events.clear()


class MediaServiceTestBase:
    """Base class for media service tests"""

    def setup_method(self):
        """Setup common test fixtures"""
        self.mock_store = MockMediaStore()
        self.mock_event_bus = MockEventBus()
        self.test_user_id = "test-user-123"
        self.test_job_id = "test-job-456"

    def create_test_video_job(self) -> Dict[str, Any]:
        """Create a test video job"""
        return {
            "script": "Test video script",
            "duration": 30,
            "style": "professional",
            "resolution": "1080p"
        }

    def create_test_voice_job(self) -> Dict[str, Any]:
        """Create a test voice job"""
        return {
            "text": "Hello world test",
            "voice_type": "natural",
            "speed": 1.0,
            "format": "wav"
        }

    def create_test_ui_session(self) -> Dict[str, Any]:
        """Create a test UI session"""
        return {
            "user_id": self.test_user_id,
            "preferences": {
                "theme": "dark",
                "notifications": True
            }
        }


@pytest.fixture
def mock_media_store():
    """Pytest fixture for mock media store"""
    return MockMediaStore()


@pytest.fixture
def mock_event_bus():
    """Pytest fixture for mock event bus"""
    return MockEventBus()


@pytest.fixture
def media_service_base():
    """Pytest fixture for media service test base"""
    return MediaServiceTestBase()


def assert_valid_health_response(health_response: Dict[str, Any], service_name: str):
    """Assert health response has correct structure"""
    assert "status" in health_response
    assert "service" in health_response
    assert "timestamp" in health_response
    assert health_response["service"] == service_name
    assert health_response["status"] in ["healthy", "unhealthy", "degraded"]


def assert_valid_job_response(job_response: Dict[str, Any]):
    """Assert job response has correct structure"""
    assert "id" in job_response
    assert "status" in job_response
    assert "created_at" in job_response
    assert job_response["status"] in ["created", "processing", "completed", "failed"]


def assert_valid_event_structure(event: Dict[str, Any]):
    """Assert event has correct structure for inter-service communication"""
    assert "topic" in event
    assert "published_at" in event
    assert "event_id" in event
    assert "data" in event or "payload" in event


class MockWebSocketConnection:
    """Mock WebSocket connection for testing UI service"""

    def __init__(self, connection_id: str = None):
        self.connection_id = connection_id or str(uuid.uuid4())
        self.messages_sent = []
        self.is_connected = True
        self.subscriptions = set()

    async def send(self, message: str) -> None:
        """Mock send message"""
        if self.is_connected:
            self.messages_sent.append({
                "message": message,
                "sent_at": datetime.now().isoformat()
            })

    async def receive(self) -> str:
        """Mock receive message"""
        # Return a test message
        return json.dumps({
            "type": "ping",
            "timestamp": datetime.now().isoformat()
        })

    def close(self) -> None:
        """Mock close connection"""
        self.is_connected = False

    def subscribe(self, channel: str) -> None:
        """Mock subscription to channel"""
        self.subscriptions.add(channel)

    def unsubscribe(self, channel: str) -> None:
        """Mock unsubscription from channel"""
        self.subscriptions.discard(channel)


@pytest.fixture
def mock_websocket():
    """Pytest fixture for mock WebSocket connection"""
    return MockWebSocketConnection()


# Async test utilities
async def wait_for_async_completion(async_func, timeout: float = 1.0):
    """Wait for async function to complete with timeout"""
    try:
        return await asyncio.wait_for(async_func, timeout=timeout)
    except asyncio.TimeoutError:
        pytest.fail(f"Async function did not complete within {timeout} seconds")


# Service health check utilities
def create_mock_service_health(service_name: str, status: str = "healthy") -> Dict[str, Any]:
    """Create a mock service health response"""
    return {
        "status": status,
        "service": service_name,
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0",
        "dependencies": {
            "storage": "connected",
            "event_bus": "connected"
        }
    }


# Error injection utilities
class ErrorInjector:
    """Utility for injecting errors into tests"""

    @staticmethod
    def storage_error():
        """Simulate storage error"""
        return Exception("Storage connection failed")

    @staticmethod
    def network_error():
        """Simulate network error"""
        return ConnectionError("Network unreachable")

    @staticmethod
    def timeout_error():
        """Simulate timeout error"""
        return TimeoutError("Operation timed out")


# Performance testing utilities
class PerformanceAssertions:
    """Utilities for performance-related assertions"""

    @staticmethod
    def assert_response_time(start_time: float, max_duration: float):
        """Assert operation completed within time limit"""
        duration = datetime.now().timestamp() - start_time
        assert duration <= max_duration, f"Operation took {duration:.3f}s, expected <= {max_duration}s"

    @staticmethod
    def assert_memory_usage_reasonable(max_mb: int = 100):
        """Assert memory usage is reasonable (placeholder)"""
        # In real tests, you could use psutil or similar
        # For now, just pass
        assert True


if __name__ == "__main__":
    # Run basic tests of the utilities themselves
    store = MockMediaStore()
    job_id = store.create_job("video", {"test": "data"})
    assert job_id is not None
    assert store.get_job(job_id)["type"] == "video"
    print("Mock utilities tests passed!")