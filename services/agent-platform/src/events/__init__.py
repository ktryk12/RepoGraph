"""
Agent Platform Events Module

Handles Kafka event consumption and platform-wide agent coordination.
"""

from .agent_event_consumer import start_agent_consumer, stop_agent_consumer, agent_consumer

__all__ = ['start_agent_consumer', 'stop_agent_consumer', 'agent_consumer']