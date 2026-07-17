"""
Mock Performance Budget Service for testing context-plane imports
"""

class PerfBudgetViolation(Exception):
    """Mock performance budget violation exception."""
    pass

def budgeted_call(operation_name, func, metadata=None):
    """Mock budgeted call function."""
    return func()