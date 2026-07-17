"""
Unit tests for media-production-agents health endpoints.
"""

def test_health_check(client):
    """Test basic health check."""
    response = client.get("/health/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "media-production-agents"


def test_readiness_check(client):
    """Test readiness check."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_liveness_check(client):
    """Test liveness check."""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"
