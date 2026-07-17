"""
Agent Platform Kafka Event Consumer

Handles agent registration and task events for the platform registry.
"""

import asyncio
import json
import logging
from typing import Dict, Any

try:
    from babyai_bus import KafkaConsumer
    from babyai_schemas.event_schemas import AgentRegistrationEvent, AgentTaskEvent, AgentCompletionEvent
except ImportError:
    # Graceful fallback for missing dependencies
    KafkaConsumer = None
    AgentRegistrationEvent = None
    AgentTaskEvent = None
    AgentCompletionEvent = None

logger = logging.getLogger(__name__)


class AgentEventConsumer:
    """
    Kafka consumer for agent platform events.

    Handles:
    - Agent registration events
    - Task completion events
    - Platform-wide agent coordination
    """

    def __init__(self):
        self.consumer = None
        self.running = False

    async def start(self):
        """Start the Kafka consumer"""
        if not KafkaConsumer:
            logger.warning("KafkaConsumer not available - running in fallback mode")
            return

        try:
            self.consumer = KafkaConsumer(
                topics=['agent.registration', 'agent.task', 'agent.completion'],
                group_id='agent-platform-consumer'
            )
            self.running = True

            logger.info("Agent platform event consumer started")

            # Start consuming events
            await self._consume_events()

        except Exception as e:
            logger.error(f"Failed to start agent event consumer: {e}")

    async def stop(self):
        """Stop the Kafka consumer"""
        self.running = False
        if self.consumer:
            await self.consumer.close()
            logger.info("Agent platform event consumer stopped")

    async def _consume_events(self):
        """Main event consumption loop"""
        while self.running and self.consumer:
            try:
                async for message in self.consumer:
                    await self._handle_event(message)

            except Exception as e:
                logger.error(f"Error consuming events: {e}")
                await asyncio.sleep(1)  # Brief pause before retry

    async def _handle_event(self, message):
        """Handle incoming Kafka event"""
        try:
            topic = message.topic
            event_data = json.loads(message.value)

            if topic == 'agent.registration':
                await self._handle_registration_event(event_data)
            elif topic == 'agent.task':
                await self._handle_task_event(event_data)
            elif topic == 'agent.completion':
                await self._handle_completion_event(event_data)
            else:
                logger.warning(f"Unknown event topic: {topic}")

        except Exception as e:
            logger.error(f"Error handling event: {e}")

    async def _handle_registration_event(self, event_data: Dict[str, Any]):
        """Handle agent registration event"""
        try:
            if AgentRegistrationEvent:
                event = AgentRegistrationEvent.from_json(json.dumps(event_data))

                logger.info(f"Agent registration event: {event.agent_id} - {event.status}")

                # Update agent registry based on registration status
                if event.status == 'registered':
                    await self._register_agent(event)
                elif event.status == 'unregistered':
                    await self._unregister_agent(event)
                elif event.status == 'health_check':
                    await self._update_agent_health(event)

        except Exception as e:
            logger.error(f"Error handling registration event: {e}")

    async def _handle_task_event(self, event_data: Dict[str, Any]):
        """Handle agent task event"""
        try:
            if AgentTaskEvent:
                event = AgentTaskEvent.from_json(json.dumps(event_data))

                logger.info(f"Task event: {event.task_id} - {event.status}")

                # Update task tracking in agent platform
                await self._update_task_status(event)

        except Exception as e:
            logger.error(f"Error handling task event: {e}")

    async def _handle_completion_event(self, event_data: Dict[str, Any]):
        """Handle agent completion event"""
        try:
            if AgentCompletionEvent:
                event = AgentCompletionEvent.from_json(json.dumps(event_data))

                logger.info(f"Completion event: {event.task_id} - Success: {event.success}")

                # Record completion metrics and results
                await self._record_completion_metrics(event)

        except Exception as e:
            logger.error(f"Error handling completion event: {e}")

    # Registry Management Methods
    async def _register_agent(self, event: 'AgentRegistrationEvent'):
        """Register agent in platform registry"""
        # TODO: Implement agent registration logic
        logger.info(f"Registering agent {event.agent_id} with capabilities: {event.capabilities}")

    async def _unregister_agent(self, event: 'AgentRegistrationEvent'):
        """Unregister agent from platform registry"""
        # TODO: Implement agent unregistration logic
        logger.info(f"Unregistering agent {event.agent_id}")

    async def _update_agent_health(self, event: 'AgentRegistrationEvent'):
        """Update agent health status"""
        # TODO: Implement health status update logic
        logger.info(f"Health check for agent {event.agent_id}")

    async def _update_task_status(self, event: 'AgentTaskEvent'):
        """Update task status in platform tracking"""
        # TODO: Implement task status tracking logic
        logger.info(f"Task {event.task_id} status: {event.status}")

    async def _record_completion_metrics(self, event: 'AgentCompletionEvent'):
        """Record task completion metrics"""
        # TODO: Implement metrics recording logic
        logger.info(f"Task {event.task_id} completed in {event.execution_time}s")


# Global consumer instance
agent_consumer = AgentEventConsumer()


async def start_agent_consumer():
    """Start the agent event consumer"""
    await agent_consumer.start()


async def stop_agent_consumer():
    """Stop the agent event consumer"""
    await agent_consumer.stop()