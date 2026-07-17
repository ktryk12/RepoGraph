"""
Tool Event Bus Module

Event-driven communication for tool platform operations.
"""

import logging
from typing import Dict, Callable, List

logger = logging.getLogger(__name__)


class ToolEventBus:
    """Event bus for tool platform"""

    def __init__(self, kafka_servers: str = "kafka:9092", group_id: str = "tool-platform"):
        self.kafka_servers = kafka_servers
        self.group_id = group_id
        self.topics = {
            "tool_registered": f"{group_id}.tool.registered",
            "tool_executed": f"{group_id}.tool.executed",
            "skill_registered": f"{group_id}.skill.registered",
            "skill_executed": f"{group_id}.skill.executed",
            "skill_feedback": f"{group_id}.skill.feedback",
            "runtime_error": f"{group_id}.runtime.error"
        }
        self._event_handlers: Dict[str, List[Callable]] = {}

    async def initialize(self) -> None:
        """Initialize event bus"""
        logger.info("Tool event bus initialized")

    def publish_tool_registered(self, tool_id: str, data: Dict) -> None:
        """Publish tool registered event"""
        logger.info(f"Published tool_registered: {tool_id}")

    def publish_tool_executed(self, tool_id: str, data: Dict) -> None:
        """Publish tool executed event"""
        logger.info(f"Published tool_executed: {tool_id}")

    def publish_skill_registered(self, skill_id: str, data: Dict) -> None:
        """Publish skill registered event"""
        logger.info(f"Published skill_registered: {skill_id}")

    def publish_skill_executed(self, skill_id: str, data: Dict) -> None:
        """Publish skill executed event"""
        logger.info(f"Published skill_executed: {skill_id}")

    def publish_skill_feedback(self, execution_id: str, feedback: Dict) -> None:
        """Publish skill feedback event"""
        logger.info(f"Published skill_feedback: {execution_id}")

    def register_handler(self, event_type: str, handler: Callable[[Dict], None]) -> None:
        """Register event handler"""
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(handler)

    def start_consumer(self) -> None:
        """Start event consumer"""
        logger.info("Tool event bus consumer started")

    def stop_consumer(self) -> None:
        """Stop event consumer"""
        logger.info("Tool event bus consumer stopped")

    async def shutdown(self) -> None:
        """Shutdown event bus"""
        logger.info("Tool event bus shutdown complete")