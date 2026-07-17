"""
Mock Rights Guard Service for testing context-plane imports
"""

class RightsGuardService:
    """Mock rights guard service for testing."""

    def evaluate(self, candidates, policy_name):
        """Mock evaluate method."""
        return MockRightsDecision("ALLOW")

class MockRightsDecision:
    """Mock rights decision."""

    def __init__(self, verdict):
        self.overall_verdict = verdict

    def to_dict(self):
        return {"overall_verdict": self.overall_verdict}

def get_rights_guard_service():
    """Get mock rights guard service instance."""
    return RightsGuardService()