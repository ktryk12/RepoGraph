"""
Development Agents Task Consumer

Handles development-specific task events and coordination.
"""

import asyncio
import json
import logging
from typing import Dict, Any

try:
    from babyai_bus import KafkaConsumer
    from babyai_schemas.event_schemas import AgentTaskEvent, AgentCompletionEvent
except ImportError:
    KafkaConsumer = None
    AgentTaskEvent = None
    AgentCompletionEvent = None

logger = logging.getLogger(__name__)


class DevelopmentTaskConsumer:
    """Kafka consumer for development agent tasks"""

    def __init__(self):
        self.consumer = None
        self.running = False

    async def start(self):
        """Start the task consumer"""
        if not KafkaConsumer:
            logger.warning("KafkaConsumer not available")
            return

        try:
            self.consumer = KafkaConsumer(
                topics=['agent.task.development', 'agent.task.architecture', 'agent.task.repair'],
                group_id='development-agents-consumer'
            )
            self.running = True
            logger.info("Development task consumer started")
            await self._consume_tasks()
        except Exception as e:
            logger.error(f"Failed to start development task consumer: {e}")

    async def _consume_tasks(self):
        """Consume development task events"""
        while self.running and self.consumer:
            try:
                async for message in self.consumer:
                    await self._handle_task_event(message)
            except Exception as e:
                logger.error(f"Error consuming tasks: {e}")
                await asyncio.sleep(1)

    async def _handle_task_event(self, message):
        """Handle incoming development task"""
        try:
            event_data = json.loads(message.value)
            if AgentTaskEvent:
                event = AgentTaskEvent.from_json(json.dumps(event_data))
                logger.info(f"Received development task: {event.task_id} - {event.task_type}")

                # Route to appropriate development agent
                if event.task_type in ['architecture', 'design']:
                    await self._handle_architecture_task(event)
                elif event.task_type in ['bug_fix', 'repair']:
                    await self._handle_repair_task(event)
                elif event.task_type in ['requirements']:
                    await self._handle_requirements_task(event)

        except Exception as e:
            logger.error(f"Error handling task event: {e}")

    async def _handle_architecture_task(self, event):
        """Handle architecture/design tasks"""
        logger.info(f"Processing architecture task: {event.task_id}")

    async def _handle_repair_task(self, event):
        """Handle bug fix/repair tasks"""
        logger.info(f"Processing repair task: {event.task_id}")

    async def _handle_requirements_task(self, event):
        """Handle requirements analysis tasks"""
        logger.info(f"Processing requirements task: {event.task_id}")

    async def stop(self):
        """Stop the consumer"""
        self.running = False
        if self.consumer:
            await self.consumer.close()


# Global consumer
dev_consumer = DevelopmentTaskConsumer()