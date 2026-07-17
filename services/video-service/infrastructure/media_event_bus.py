"""
Media Event Bus

Kafka-based event-driven architecture for the media platform.
Handles events for video generation, voice processing, and UI interactions.
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime
from uuid import uuid4

try:
    from confluent_kafka import Consumer, Producer, KafkaError
except ImportError:
    Consumer = None
    Producer = None
    KafkaError = None

logger = logging.getLogger(__name__)


class MediaEventBus:
    """
    Event bus for media platform operations

    Manages Kafka-based events for:
    - Video job lifecycle (started, completed, failed)
    - Voice processing operations (STT/TTS)
    - UI session and interaction events
    - Media asset creation and management
    - Performance monitoring events
    """

    def __init__(self, kafka_servers: str, group_id: str = "media-platform"):
        self.kafka_servers = kafka_servers
        self.group_id = group_id

        # Kafka clients
        self.producer: Optional[Producer] = None
        self.consumer: Optional[Consumer] = None

        # Event handlers
        self.handlers: Dict[str, List[Callable]] = {}

        # Consumer control
        self.consumer_running = False
        self.consumer_task: Optional[asyncio.Task] = None

        # Event topics
        self.topics = {
            # Video events
            "video_job_started": "media.video.job.started",
            "video_job_completed": "media.video.job.completed",
            "video_job_failed": "media.video.job.failed",
            "video_job_cancelled": "media.video.job.cancelled",

            # Voice events
            "voice_operation_started": "media.voice.operation.started",
            "voice_operation_completed": "media.voice.operation.completed",
            "voice_operation_failed": "media.voice.operation.failed",

            # UI events
            "ui_session_created": "media.ui.session.created",
            "ui_action_performed": "media.ui.action.performed",
            "ui_session_expired": "media.ui.session.expired",

            # Media asset events
            "media_asset_created": "media.asset.created",
            "media_asset_updated": "media.asset.updated",
            "media_asset_deleted": "media.asset.deleted",

            # Platform events
            "platform_health_check": "media.platform.health",
            "performance_metric_recorded": "media.platform.metric"
        }

    async def initialize(self) -> None:
        """Initialize the event bus"""
        try:
            if not Consumer or not Producer:
                logger.warning("Kafka libraries not available, using mock event bus")
                return

            # Initialize producer
            producer_config = {
                'bootstrap.servers': self.kafka_servers,
                'client.id': f'media-platform-producer-{uuid4().hex[:8]}',
                'acks': 'all',
                'retries': 3
            }
            self.producer = Producer(producer_config)

            # Initialize consumer
            consumer_config = {
                'bootstrap.servers': self.kafka_servers,
                'group.id': self.group_id,
                'client.id': f'media-platform-consumer-{uuid4().hex[:8]}',
                'auto.offset.reset': 'latest',
                'enable.auto.commit': True
            }
            self.consumer = Consumer(consumer_config)

            # Subscribe to all media platform topics
            topic_list = list(self.topics.values())
            self.consumer.subscribe(topic_list)

            logger.info(f"Media event bus initialized - Topics: {len(topic_list)}")

        except Exception as e:
            logger.error(f"Failed to initialize media event bus: {e}")
            raise

    def register_handler(self, event_type: str, handler: Callable) -> None:
        """Register an event handler"""
        if event_type not in self.handlers:
            self.handlers[event_type] = []
        self.handlers[event_type].append(handler)
        logger.debug(f"Registered handler for event: {event_type}")

    def start_consumer(self) -> None:
        """Start the event consumer"""
        if self.consumer_running:
            logger.warning("Event consumer is already running")
            return

        self.consumer_running = True
        self.consumer_task = asyncio.create_task(self._consumer_loop())
        logger.info("Media event consumer started")

    def stop_consumer(self) -> None:
        """Stop the event consumer"""
        if not self.consumer_running:
            return

        self.consumer_running = False
        if self.consumer_task:
            self.consumer_task.cancel()
        logger.info("Media event consumer stopped")

    async def _consumer_loop(self) -> None:
        """Event consumer loop"""
        if not self.consumer:
            logger.warning("No Kafka consumer available")
            return

        try:
            while self.consumer_running:
                msg = self.consumer.poll(timeout=1.0)

                if msg is None:
                    continue

                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        logger.error(f"Consumer error: {msg.error()}")
                    continue

                try:
                    # Decode message
                    event_data = json.loads(msg.value().decode('utf-8'))
                    topic = msg.topic()

                    # Find event type from topic
                    event_type = None
                    for etype, etopic in self.topics.items():
                        if etopic == topic:
                            event_type = etype
                            break

                    if event_type:
                        await self._handle_event(event_type, event_data)

                except Exception as e:
                    logger.error(f"Failed to process message: {e}")

        except asyncio.CancelledError:
            logger.info("Event consumer loop cancelled")
        except Exception as e:
            logger.error(f"Event consumer loop error: {e}")

    async def _handle_event(self, event_type: str, event_data: Dict) -> None:
        """Handle incoming event"""
        try:
            handlers = self.handlers.get(event_type, [])

            if not handlers:
                logger.debug(f"No handlers for event type: {event_type}")
                return

            # Execute all handlers for this event type
            for handler in handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(event_data)
                    else:
                        handler(event_data)

                except Exception as e:
                    logger.error(f"Handler failed for {event_type}: {e}")

            logger.debug(f"Event handled: {event_type}")

        except Exception as e:
            logger.error(f"Failed to handle event {event_type}: {e}")

    def _publish_event(self, event_type: str, payload: Dict) -> None:
        """Publish event to Kafka"""
        if not self.producer:
            logger.debug(f"Mock event published: {event_type}")
            return

        try:
            topic = self.topics.get(event_type)
            if not topic:
                logger.warning(f"Unknown event type: {event_type}")
                return

            # Enhance payload with metadata
            enhanced_payload = {
                **payload,
                "event_type": event_type,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "media-platform"
            }

            # Publish to Kafka
            self.producer.produce(
                topic=topic,
                value=json.dumps(enhanced_payload),
                callback=lambda err, msg: self._delivery_callback(err, msg, event_type)
            )
            self.producer.poll(0)  # Non-blocking

            logger.debug(f"Event published: {event_type}")

        except Exception as e:
            logger.error(f"Failed to publish event {event_type}: {e}")

    def _delivery_callback(self, err, msg, event_type: str) -> None:
        """Kafka delivery callback"""
        if err is not None:
            logger.error(f"Failed to deliver {event_type} event: {err}")
        else:
            logger.debug(f"Event delivered: {event_type}")

    # Video Event Publishers
    def publish_video_job_started(self, job_id: str, payload: Dict) -> None:
        """Publish video job started event"""
        self._publish_event("video_job_started", {
            "job_id": job_id,
            **payload
        })

    def publish_video_job_completed(self, job_id: str, payload: Dict) -> None:
        """Publish video job completed event"""
        self._publish_event("video_job_completed", {
            "job_id": job_id,
            **payload
        })

    def publish_video_job_failed(self, job_id: str, payload: Dict) -> None:
        """Publish video job failed event"""
        self._publish_event("video_job_failed", {
            "job_id": job_id,
            **payload
        })

    def publish_video_job_cancelled(self, job_id: str, payload: Dict) -> None:
        """Publish video job cancelled event"""
        self._publish_event("video_job_cancelled", {
            "job_id": job_id,
            **payload
        })

    # Voice Event Publishers
    def publish_voice_operation_started(self, operation_id: str, payload: Dict) -> None:
        """Publish voice operation started event"""
        self._publish_event("voice_operation_started", {
            "operation_id": operation_id,
            **payload
        })

    def publish_voice_operation_completed(self, operation_id: str, payload: Dict) -> None:
        """Publish voice operation completed event"""
        self._publish_event("voice_operation_completed", {
            "operation_id": operation_id,
            **payload
        })

    def publish_voice_operation_failed(self, operation_id: str, payload: Dict) -> None:
        """Publish voice operation failed event"""
        self._publish_event("voice_operation_failed", {
            "operation_id": operation_id,
            **payload
        })

    # UI Event Publishers
    def publish_ui_session_created(self, session_id: str, payload: Dict) -> None:
        """Publish UI session created event"""
        self._publish_event("ui_session_created", {
            "session_id": session_id,
            **payload
        })

    def publish_ui_action_performed(self, session_id: str, payload: Dict) -> None:
        """Publish UI action performed event"""
        self._publish_event("ui_action_performed", {
            "session_id": session_id,
            **payload
        })

    def publish_ui_session_expired(self, session_id: str, payload: Dict) -> None:
        """Publish UI session expired event"""
        self._publish_event("ui_session_expired", {
            "session_id": session_id,
            **payload
        })

    # Media Asset Event Publishers
    def publish_media_asset_created(self, asset_id: str, payload: Dict) -> None:
        """Publish media asset created event"""
        self._publish_event("media_asset_created", {
            "asset_id": asset_id,
            **payload
        })

    def publish_media_asset_updated(self, asset_id: str, payload: Dict) -> None:
        """Publish media asset updated event"""
        self._publish_event("media_asset_updated", {
            "asset_id": asset_id,
            **payload
        })

    def publish_media_asset_deleted(self, asset_id: str, payload: Dict) -> None:
        """Publish media asset deleted event"""
        self._publish_event("media_asset_deleted", {
            "asset_id": asset_id,
            **payload
        })

    # Platform Event Publishers
    def publish_platform_health_check(self, payload: Dict) -> None:
        """Publish platform health check event"""
        self._publish_event("platform_health_check", payload)

    def publish_performance_metric_recorded(self, metric_id: str, payload: Dict) -> None:
        """Publish performance metric recorded event"""
        self._publish_event("performance_metric_recorded", {
            "metric_id": metric_id,
            **payload
        })

    async def shutdown(self) -> None:
        """Shutdown the event bus"""
        try:
            # Stop consumer
            self.stop_consumer()

            # Close Kafka clients
            if self.consumer:
                self.consumer.close()

            if self.producer:
                self.producer.flush()

            logger.info("Media event bus shutdown complete")

        except Exception as e:
            logger.error(f"Error during event bus shutdown: {e}")
            raise