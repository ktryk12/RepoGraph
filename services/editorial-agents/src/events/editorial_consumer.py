"""
Editorial Agents Task Consumer

Handles editorial and content-related task events.
"""

import asyncio
import json
import logging

try:
    from babyai_bus import KafkaConsumer
    from babyai_schemas.event_schemas import AgentTaskEvent, AgentCompletionEvent
except ImportError:
    KafkaConsumer = None
    AgentTaskEvent = None
    AgentCompletionEvent = None

logger = logging.getLogger(__name__)


class EditorialTaskConsumer:
    """Kafka consumer for editorial agent tasks"""

    def __init__(self):
        self.consumer = None
        self.running = False

    async def start(self):
        """Start the editorial task consumer"""
        if not KafkaConsumer:
            logger.warning("KafkaConsumer not available")
            return

        try:
            self.consumer = KafkaConsumer(
                topics=['agent.task.editorial', 'agent.task.content', 'agent.task.review'],
                group_id='editorial-agents-consumer'
            )
            self.running = True
            logger.info("Editorial task consumer started")
            await self._consume_tasks()
        except Exception as e:
            logger.error(f"Failed to start editorial task consumer: {e}")

    async def _consume_tasks(self):
        """Consume editorial task events"""
        while self.running and self.consumer:
            try:
                async for message in self.consumer:
                    await self._handle_task_event(message)
            except Exception as e:
                logger.error(f"Error consuming editorial tasks: {e}")
                await asyncio.sleep(1)

    async def _handle_task_event(self, message):
        """Handle incoming editorial task"""
        try:
            event_data = json.loads(message.value)
            if AgentTaskEvent:
                event = AgentTaskEvent.from_json(json.dumps(event_data))
                logger.info(f"Received editorial task: {event.task_id} - {event.task_type}")

                # Route to appropriate editorial agent
                if event.task_type in ['article', 'content_creation']:
                    await self._handle_content_task(event)
                elif event.task_type in ['legal_review', 'compliance']:
                    await self._handle_legal_task(event)
                elif event.task_type in ['audience', 'targeting']:
                    await self._handle_audience_task(event)

        except Exception as e:
            logger.error(f"Error handling editorial task event: {e}")

    async def _handle_content_task(self, event):
        """Handle content creation tasks"""
        logger.info(f"Processing content task: {event.task_id}")

    async def _handle_legal_task(self, event):
        """Handle legal review tasks"""
        logger.info(f"Processing legal review task: {event.task_id}")

    async def _handle_audience_task(self, event):
        """Handle audience analysis tasks"""
        logger.info(f"Processing audience task: {event.task_id}")

    async def stop(self):
        """Stop the consumer"""
        self.running = False
        if self.consumer:
            await self.consumer.close()


# Global consumer
editorial_consumer = EditorialTaskConsumer()