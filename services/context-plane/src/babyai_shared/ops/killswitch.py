"""
Mock Killswitch Service for testing context-plane imports
"""

class KillSwitchViolation(Exception):
    """Mock killswitch violation exception."""
    pass

class MockKillswitchService:
    """Mock killswitch service for testing."""

    def require_write(self, operation, scope):
        """Mock require write method - always allows writes."""
        pass

def get_killswitch_service():
    """Get mock killswitch service instance."""
    return MockKillswitchService()