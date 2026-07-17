"""
Mock Discovery Service for testing context-plane imports
"""

class DiscoveryService:
    """Mock discovery service for testing."""

    def extract_candidates(self, request):
        """Mock extract candidates method."""
        return []

def get_discovery_service():
    """Get mock discovery service instance."""
    return DiscoveryService()