#!/usr/bin/env python3
"""
Context Plane Kafka Event Handlers
ADR-0015 Phase 2: Database-per-Service

Handles Kafka event publishing and consuming for context database operations.
Publishes database change events to replace direct database access patterns.

Events Published:
- context.index.updated.v1: Index update completed
- context.index.started.v1: Index operation started
- context.index.completed.v1: Index operation completed
- context.document.indexed.v1: Document added to index
- context.document.removed.v1: Document removed from index
- context.search.performed.v1: Search operation completed
- evaluation.context.retrieved.v1: Context pack retrieved (Phase 1 compatibility)

Events Consumed:
- evaluation.context.requested.v1: Context retrieval requests
- repository.updated.v1: Repository update notifications
- context.cache.invalidate.v1: Cache invalidation requests
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from aiokafka.errors import KafkaError

logger = logging.getLogger(__name__)

class ContextKafkaHandlers:
    """Handles Kafka events for context-plane service."""

    def __init__(self, context_service, kafka_bootstrap_servers: str):
        self.context_service = context_service
        self.kafka_bootstrap_servers = kafka_bootstrap_servers
        self.producer = None
        self.consumer = None
        self.consumer_group = "context-plane-workers"

        # Topic configurations
        self.producer_topics = {
            "context.index.updated.v1": "context/index_updated_v1.json",
            "context.index.started.v1": "context/index_started_v1.json",
            "context.index.completed.v1": "context/index_completed_v1.json",
            "context.document.indexed.v1": "context/document_indexed_v1.json",
            "context.document.removed.v1": "context/document_removed_v1.json",
            "context.search.performed.v1": "context/search_performed_v1.json",
            "evaluation.context.retrieved.v1": "evaluation/context_retrieved_v1.json"
        }

        self.consumer_topics = [
            "evaluation.context.requested.v1",
            "repository.updated.v1",
            "context.cache.invalidate.v1"
        ]

    async def start(self):
        """Start Kafka producer and consumer."""
        try:
            # Initialize producer
            self.producer = AIOKafkaProducer(
                bootstrap_servers=self.kafka_bootstrap_servers,
                value_serializer=lambda x: json.dumps(x).encode('utf-8'),
                key_serializer=lambda x: x.encode('utf-8') if x else None,
                acks='all',  # Wait for all replicas
                enable_idempotence=True
            )
            await self.producer.start()
            logger.info("Kafka producer started")

            # Initialize consumer
            self.consumer = AIOKafkaConsumer(
                *self.consumer_topics,
                bootstrap_servers=self.kafka_bootstrap_servers,
                group_id=self.consumer_group,
                value_deserializer=lambda x: json.loads(x.decode('utf-8')),
                auto_offset_reset='latest',
                enable_auto_commit=False,  # Manual commit for reliability
                max_poll_records=10,  # Process in small batches
                session_timeout_ms=30000,
                heartbeat_interval_ms=10000
            )
            await self.consumer.start()
            logger.info(f"Kafka consumer started for topics: {self.consumer_topics}")

        except Exception as e:
            logger.error(f"Failed to start Kafka handlers: {e}")
            raise

    async def stop(self):
        """Stop Kafka producer and consumer."""
        try:
            if self.producer:
                await self.producer.stop()
                logger.info("Kafka producer stopped")

            if self.consumer:
                await self.consumer.stop()
                logger.info("Kafka consumer stopped")

        except Exception as e:
            logger.error(f"Error stopping Kafka handlers: {e}")

    async def consume_events(self):
        """Main event consumption loop."""
        logger.info("Starting event consumption loop")

        try:
            async for message in self.consumer:
                try:
                    await self._handle_consumed_event(message)
                    await self.consumer.commit()

                except Exception as e:
                    logger.error(f"Error processing message from {message.topic}: {e}")
                    # Continue processing other messages

        except Exception as e:
            logger.error(f"Event consumption loop failed: {e}")

    async def _handle_consumed_event(self, message):
        """Handle a consumed Kafka event."""
        topic = message.topic
        event_data = message.value

        logger.debug(f"Processing event from {topic}")

        # Extract correlation ID for tracing
        correlation_id = event_data.get("envelope", {}).get("correlation_id")

        if topic == "evaluation.context.requested.v1":
            await self._handle_context_requested(event_data, correlation_id)

        elif topic == "repository.updated.v1":
            await self._handle_repository_updated(event_data, correlation_id)

        elif topic == "context.cache.invalidate.v1":
            await self._handle_cache_invalidate(event_data, correlation_id)

        else:
            logger.warning(f"Unknown topic received: {topic}")

    async def _handle_context_requested(self, event_data: Dict[str, Any], correlation_id: str):
        """Handle context retrieval requests."""
        try:
            payload = event_data.get("payload", {})
            repository_id = payload.get("repository_id")
            query = payload.get("query")
            max_tokens = payload.get("max_context_tokens", 100000)

            logger.info(f"Processing context request for repository: {repository_id}")

            # Retrieve context using context service
            context_pack = await self.context_service.retrieve_context(
                repository_id=repository_id,
                query=query,
                max_tokens=max_tokens,
                correlation_id=correlation_id
            )

            # Publish context retrieved event
            await self.publish_context_retrieved(context_pack, correlation_id)

        except Exception as e:
            logger.error(f"Error handling context request: {e}")
            # Publish error event or send to DLQ
            await self._handle_context_request_error(event_data, str(e), correlation_id)

    async def _handle_repository_updated(self, event_data: Dict[str, Any], correlation_id: str):
        """Handle repository update notifications."""
        try:
            payload = event_data.get("payload", {})
            repository_id = payload.get("repository_id")
            changed_files = payload.get("changed_files", [])

            logger.info(f"Repository updated: {repository_id}, {len(changed_files)} files changed")

            # Start incremental indexing
            indexing_result = await self.context_service.update_repository_index(
                repository_id=repository_id,
                changed_files=changed_files,
                correlation_id=correlation_id
            )

            # Events are published by context_service during indexing
            logger.debug(f"Repository indexing initiated: {indexing_result}")

        except Exception as e:
            logger.error(f"Error handling repository update: {e}")

    async def _handle_cache_invalidate(self, event_data: Dict[str, Any], correlation_id: str):
        """Handle cache invalidation requests."""
        try:
            payload = event_data.get("payload", {})
            invalidation_scope = payload.get("invalidation_scope")
            repository_id = payload.get("repository_id")
            document_ids = payload.get("document_ids", [])
            cache_types = payload.get("cache_types", ["all"])

            logger.info(f"Cache invalidation requested: scope={invalidation_scope}, repo={repository_id}")

            # Invalidate cache using context service
            await self.context_service.invalidate_cache(
                scope=invalidation_scope,
                repository_id=repository_id,
                document_ids=document_ids,
                cache_types=cache_types,
                correlation_id=correlation_id
            )

        except Exception as e:
            logger.error(f"Error handling cache invalidation: {e}")

    async def _handle_context_request_error(self, original_event: Dict[str, Any], error_message: str, correlation_id: str):
        """Handle context request errors by publishing error event."""
        try:
            error_event = self._create_envelope("evaluation.context.error.v1", correlation_id)
            error_event["payload"] = {
                "request_id": original_event.get("envelope", {}).get("event_id"),
                "error_type": "context_retrieval_failed",
                "error_message": error_message,
                "original_request": original_event.get("payload", {})
            }

            await self._publish_event("evaluation.context.error.v1", error_event, correlation_id)

        except Exception as e:
            logger.error(f"Error publishing context error event: {e}")

    # Event publishing methods

    async def publish_context_retrieved(self, context_pack: Dict[str, Any], correlation_id: str):
        """Publish context retrieved event."""
        event = self._create_envelope("evaluation.context.retrieved.v1", correlation_id)
        event["payload"] = {
            "context_id": context_pack.get("context_id"),
            "repository_id": context_pack.get("repository_id"),
            "context_pack": context_pack.get("context_pack", {}),
            "token_count": context_pack.get("token_count", 0),
            "retrieval_stats": context_pack.get("retrieval_stats", {}),
            "retrieved_at": datetime.now(timezone.utc).isoformat()
        }

        await self._publish_event("evaluation.context.retrieved.v1", event, correlation_id)

    async def publish_index_started(self, indexing_operation: Dict[str, Any], correlation_id: str):
        """Publish index started event."""
        event = self._create_envelope("context.index.started.v1", correlation_id)
        event["payload"] = {
            "indexing_id": indexing_operation.get("indexing_id"),
            "repository_id": indexing_operation.get("repository_id"),
            "indexing_type": indexing_operation.get("indexing_type", "incremental"),
            "estimated_completion": indexing_operation.get("estimated_completion", {}),
            "repository_metadata": indexing_operation.get("repository_metadata", {}),
            "indexing_config": indexing_operation.get("indexing_config", {}),
            "triggered_by": indexing_operation.get("triggered_by", {}),
            "previous_index": indexing_operation.get("previous_index", {})
        }

        await self._publish_event("context.index.started.v1", event, correlation_id)

    async def publish_index_completed(self, indexing_result: Dict[str, Any], correlation_id: str):
        """Publish index completed event."""
        event = self._create_envelope("context.index.completed.v1", correlation_id)
        event["payload"] = {
            "indexing_id": indexing_result.get("indexing_id"),
            "repository_id": indexing_result.get("repository_id"),
            "completion_status": indexing_result.get("status", "success"),
            "final_stats": indexing_result.get("final_stats", {}),
            "performance_metrics": indexing_result.get("performance_metrics", {}),
            "error_summary": indexing_result.get("error_summary", {}),
            "quality_metrics": indexing_result.get("quality_metrics", {}),
            "index_metadata": indexing_result.get("index_metadata", {}),
            "next_actions": indexing_result.get("next_actions", {})
        }

        await self._publish_event("context.index.completed.v1", event, correlation_id)

    async def publish_index_updated(self, update_result: Dict[str, Any], correlation_id: str):
        """Publish index updated event."""
        event = self._create_envelope("context.index.updated.v1", correlation_id)
        event["payload"] = {
            "repository_id": update_result.get("repository_id"),
            "update_type": update_result.get("update_type", "incremental"),
            "documents_updated": update_result.get("documents_updated", 0),
            "documents_added": update_result.get("documents_added", 0),
            "documents_removed": update_result.get("documents_removed", 0),
            "index_stats": update_result.get("index_stats", {}),
            "performance_metrics": update_result.get("performance_metrics", {}),
            "metadata": update_result.get("metadata", {})
        }

        await self._publish_event("context.index.updated.v1", event, correlation_id)

    async def publish_document_indexed(self, document_info: Dict[str, Any], correlation_id: str):
        """Publish document indexed event."""
        event = self._create_envelope("context.document.indexed.v1", correlation_id)
        event["payload"] = {
            "document_id": document_info.get("document_id"),
            "repository_id": document_info.get("repository_id"),
            "file_path": document_info.get("file_path"),
            "document_type": document_info.get("document_type"),
            "content_hash": document_info.get("content_hash"),
            "file_size_bytes": document_info.get("file_size_bytes"),
            "language": document_info.get("language"),
            "indexing_metadata": document_info.get("indexing_metadata", {}),
            "semantic_metadata": document_info.get("semantic_metadata", {}),
            "dependencies": document_info.get("dependencies", []),
            "metadata": document_info.get("metadata", {})
        }

        await self._publish_event("context.document.indexed.v1", event, correlation_id)

    async def publish_document_removed(self, removal_info: Dict[str, Any], correlation_id: str):
        """Publish document removed event."""
        event = self._create_envelope("context.document.removed.v1", correlation_id)
        event["payload"] = {
            "document_id": removal_info.get("document_id"),
            "repository_id": removal_info.get("repository_id"),
            "file_path": removal_info.get("file_path"),
            "removal_reason": removal_info.get("removal_reason"),
            "removed_at": datetime.now(timezone.utc).isoformat(),
            "previous_metadata": removal_info.get("previous_metadata", {}),
            "cleanup_stats": removal_info.get("cleanup_stats", {}),
            "metadata": removal_info.get("metadata", {})
        }

        await self._publish_event("context.document.removed.v1", event, correlation_id)

    async def publish_search_performed(self, search_info: Dict[str, Any], correlation_id: str):
        """Publish search performed event."""
        event = self._create_envelope("context.search.performed.v1", correlation_id)
        event["payload"] = {
            "search_id": search_info.get("search_id"),
            "repository_id": search_info.get("repository_id"),
            "search_type": search_info.get("search_type", "semantic"),
            "query_hash": search_info.get("query_hash"),
            "query_metadata": search_info.get("query_metadata", {}),
            "result_count": search_info.get("result_count", 0),
            "result_metadata": search_info.get("result_metadata", {}),
            "performance_metrics": search_info.get("performance_metrics", {}),
            "cache_metadata": search_info.get("cache_metadata", {}),
            "user_context": search_info.get("user_context", {}),
            "metadata": search_info.get("metadata", {})
        }

        await self._publish_event("context.search.performed.v1", event, correlation_id)

    async def _publish_event(self, topic: str, event: Dict[str, Any], partition_key: str = None):
        """Publish event to Kafka topic."""
        try:
            if not self.producer:
                logger.error("Kafka producer not initialized")
                return

            # Use correlation_id as partition key for ordering
            await self.producer.send(
                topic=topic,
                value=event,
                key=partition_key
            )

            logger.debug(f"Published event to {topic}: {event['envelope']['event_id']}")

        except Exception as e:
            logger.error(f"Error publishing event to {topic}: {e}")
            raise

    def _create_envelope(self, event_type: str, correlation_id: str = None) -> Dict[str, Any]:
        """Create ADR-0015 compliant event envelope."""
        return {
            "envelope": {
                "version": "v1",
                "event_type": event_type,
                "event_id": str(uuid.uuid4()),
                "correlation_id": correlation_id or str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source_service": "context-plane",
                "idempotency_key": f"{event_type}_{correlation_id}_{int(datetime.now(timezone.utc).timestamp())}"
            }
        }

# Event handler factory
async def create_kafka_handlers(context_service, kafka_bootstrap_servers: str) -> ContextKafkaHandlers:
    """Create and initialize Kafka handlers."""
    handlers = ContextKafkaHandlers(context_service, kafka_bootstrap_servers)
    await handlers.start()
    return handlers

# Background task for event consumption
async def run_event_consumer(kafka_handlers: ContextKafkaHandlers):
    """Run the event consumer as a background task."""
    try:
        await kafka_handlers.consume_events()
    except Exception as e:
        logger.error(f"Event consumer failed: {e}")
        raise

if __name__ == "__main__":
    import sys
    import os

    # Example usage
    async def main():
        from context_service import ContextService

        kafka_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

        # Create mock context service for testing
        class MockContextService:
            async def retrieve_context(self, **kwargs):
                return {"context_id": "test", "repository_id": "test", "context_pack": {}}

            async def update_repository_index(self, **kwargs):
                return {"status": "started", "indexing_id": "test"}

            async def invalidate_cache(self, **kwargs):
                pass

        context_service = MockContextService()

        # Create and test handlers
        handlers = await create_kafka_handlers(context_service, kafka_servers)

        try:
            # Test event publishing
            await handlers.publish_index_started({
                "indexing_id": "test_index",
                "repository_id": "test_repo",
                "indexing_type": "full"
            }, "test_correlation")

            print("Event handlers tested successfully")

        finally:
            await handlers.stop()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    asyncio.run(main())