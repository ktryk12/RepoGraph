"""
Agent Platform Service

Microservice for agent discovery, registration, and coordination.
Following ADR-0015 agent microservice architecture.
"""

# Only import the components we know work
try:
    from .agents.registry import AgentRegistry
except ImportError:
    AgentRegistry = None

__all__ = ["AgentRegistry"]