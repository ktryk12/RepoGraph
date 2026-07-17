"""
Consolidated Data Platform Service

Integrates functionality from:
- data-exporter/ (Data export in multiple formats: JSON-LD, CSV, NDJSON)
- artifact-writer/ (Artifact storage with contracts and validation)
- execution-audit/ (Immutable audit trails via Kafka)
- publisher/ (Content publishing to multiple platforms)

Provides unified data platform with PostgreSQL persistence and event-driven architecture.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from uuid import uuid4

from postgresql_data_store import PostgreSQLDataStore

# Import consolidated modules
from export.export_manager import ExportManager
from artifacts.artifact_manager import ArtifactManager
from audit.audit_manager import AuditManager
from publishing.publishing_manager import PublishingManager
from infrastructure.data_event_bus import DataEventBus

logger = logging.getLogger(__name__)


class DataPlatformService:
    """
    Consolidated data platform service

    Provides unified interface for:
    - Data export in multiple formats (from data-exporter/)
    - Artifact storage and validation (from artifact-writer/)
    - Execution audit trails (from execution-audit/)
    - Content publishing operations (from publisher/)
    - Cross-platform data coordination and analytics
    """

    def __init__(self, database_url: str, kafka_servers: str = "kafka:9092"):
        self.database_url = database_url
        self.kafka_servers = kafka_servers

        # Core components
        self.store: Optional[PostgreSQLDataStore] = None
        self.export_manager: Optional[ExportManager] = None
        self.artifact_manager: Optional[ArtifactManager] = None
        self.audit_manager: Optional[AuditManager] = None
        self.publishing_manager: Optional[PublishingManager] = None
        self.event_bus: Optional[DataEventBus] = None

    async def initialize(self) -> None:
        """Initialize the data platform service"""
        try:
            # Initialize PostgreSQL store
            self.store = await PostgreSQLDataStore.create(self.database_url)
            logger.info("Data platform store initialized")

            # Initialize event bus
            self.event_bus = DataEventBus(
                kafka_servers=self.kafka_servers,
                group_id="data-platform"
            )
            await self.event_bus.initialize()

            # Initialize consolidated modules
            self.export_manager = ExportManager(self.store, self.event_bus)
            self.artifact_manager = ArtifactManager(self.store, self.event_bus)
            self.audit_manager = AuditManager(self.store, self.event_bus)
            self.publishing_manager = PublishingManager(self.store, self.event_bus)

            # Initialize all modules
            await asyncio.gather(
                self.export_manager.initialize(),
                self.artifact_manager.initialize(),
                self.audit_manager.initialize(),
                self.publishing_manager.initialize(),
            )

            # Setup event handlers
            await self._setup_event_handlers()

            # Start event consumer
            if self.event_bus:
                self.event_bus.start_consumer()

            logger.info("Data platform service initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize data platform service: {e}")
            raise

    async def _setup_event_handlers(self) -> None:
        """Setup data platform event handlers"""
        if not self.event_bus:
            return

        # Export events
        self.event_bus.register_handler("export_job_started", self._handle_export_job_started)
        self.event_bus.register_handler("export_job_completed", self._handle_export_job_completed)
        self.event_bus.register_handler("export_job_failed", self._handle_export_job_failed)

        # Artifact events
        self.event_bus.register_handler("artifact_created", self._handle_artifact_created)
        self.event_bus.register_handler("artifact_validated", self._handle_artifact_validated)

        # Audit events
        self.event_bus.register_handler("audit_record_created", self._handle_audit_record_created)

        # Publishing events
        self.event_bus.register_handler("content_publish_started", self._handle_content_publish_started)
        self.event_bus.register_handler("content_published", self._handle_content_published)
        self.event_bus.register_handler("content_publish_failed", self._handle_content_publish_failed)

        # Cross-platform events
        self.event_bus.register_handler("data_pipeline_completed", self._handle_data_pipeline_completed)

        logger.info("Data platform event handlers registered")

    # Event Handlers
    async def _handle_export_job_started(self, payload: Dict) -> None:
        """Handle export job started event"""
        try:
            job_id = payload.get("job_id")
            export_type = payload.get("export_type")

            logger.info(f"Export job started: {job_id} ({export_type})")

            # Record performance metric
            if self.store and job_id:
                metric_id = f"export_start_{uuid4().hex[:12]}"
                await self.store.record_performance_metric(
                    metric_id=metric_id,
                    resource_type="export_job",
                    resource_id=job_id,
                    metric_type="job_started",
                    metric_value=1.0
                )

        except Exception as e:
            logger.error(f"Failed to handle export job started: {e}")

    async def _handle_export_job_completed(self, payload: Dict) -> None:
        """Handle export job completed event"""
        try:
            job_id = payload.get("job_id")
            records_exported = payload.get("records_exported", 0)
            file_size = payload.get("file_size_bytes", 0)
            duration_ms = payload.get("duration_ms", 0)

            logger.info(f"Export job completed: {job_id} ({records_exported} records)")

            # Update job status
            if self.store:
                await self.store.update_export_job(
                    job_id=job_id,
                    status="completed",
                    records_exported=records_exported,
                    file_size_bytes=file_size,
                    completed_at=datetime.utcnow()
                )

                # Record completion metrics
                metric_id = f"export_complete_{uuid4().hex[:12]}"
                await self.store.record_performance_metric(
                    metric_id=metric_id,
                    resource_type="export_job",
                    resource_id=job_id,
                    metric_type="completion_time_ms",
                    metric_value=duration_ms
                )

        except Exception as e:
            logger.error(f"Failed to handle export job completed: {e}")

    async def _handle_export_job_failed(self, payload: Dict) -> None:
        """Handle export job failed event"""
        try:
            job_id = payload.get("job_id")
            error = payload.get("error", "Unknown error")

            logger.warning(f"Export job failed: {job_id} - {error}")

            # Update job status
            if self.store:
                await self.store.update_export_job(
                    job_id=job_id,
                    status="failed",
                    metadata={"error": error, "failed_at": datetime.utcnow().isoformat()}
                )

        except Exception as e:
            logger.error(f"Failed to handle export job failed: {e}")

    async def _handle_artifact_created(self, payload: Dict) -> None:
        """Handle artifact created event"""
        try:
            artifact_id = payload.get("artifact_id")
            artifact_type = payload.get("artifact_type")

            logger.info(f"Artifact created: {artifact_id} ({artifact_type})")

            # Could trigger validation pipeline or notifications

        except Exception as e:
            logger.error(f"Failed to handle artifact created: {e}")

    async def _handle_artifact_validated(self, payload: Dict) -> None:
        """Handle artifact validated event"""
        try:
            artifact_id = payload.get("artifact_id")
            validation_result = payload.get("validation_result", {})
            is_valid = validation_result.get("valid", False)

            logger.info(f"Artifact validated: {artifact_id} (valid: {is_valid})")

            # Update validation status
            if self.store:
                await self.store.update_artifact(
                    artifact_id=artifact_id,
                    validation_status="valid" if is_valid else "invalid",
                    validation_result=validation_result
                )

        except Exception as e:
            logger.error(f"Failed to handle artifact validated: {e}")

    async def _handle_audit_record_created(self, payload: Dict) -> None:
        """Handle audit record created event"""
        try:
            audit_id = payload.get("audit_id")
            event_type = payload.get("event_type")

            logger.debug(f"Audit record created: {audit_id} ({event_type})")

            # Could trigger compliance checks or analytics

        except Exception as e:
            logger.error(f"Failed to handle audit record created: {e}")

    async def _handle_content_publish_started(self, payload: Dict) -> None:
        """Handle content publish started event"""
        try:
            operation_id = payload.get("operation_id")
            platform = payload.get("platform")

            logger.info(f"Content publish started: {operation_id} ({platform})")

        except Exception as e:
            logger.error(f"Failed to handle content publish started: {e}")

    async def _handle_content_published(self, payload: Dict) -> None:
        """Handle content published event"""
        try:
            operation_id = payload.get("operation_id")
            platform = payload.get("platform")
            platform_ref = payload.get("platform_ref")

            logger.info(f"Content published: {operation_id} ({platform}) -> {platform_ref}")

            # Update publishing operation
            if self.store:
                await self.store.update_publishing_operation(
                    operation_id=operation_id,
                    publish_status="published",
                    platform_ref=platform_ref,
                    published_at=datetime.utcnow()
                )

        except Exception as e:
            logger.error(f"Failed to handle content published: {e}")

    async def _handle_content_publish_failed(self, payload: Dict) -> None:
        """Handle content publish failed event"""
        try:
            operation_id = payload.get("operation_id")
            platform = payload.get("platform")
            error = payload.get("error", "Unknown error")

            logger.warning(f"Content publish failed: {operation_id} ({platform}) - {error}")

            # Update publishing operation
            if self.store:
                await self.store.update_publishing_operation(
                    operation_id=operation_id,
                    publish_status="failed",
                    metadata={"error": error, "failed_at": datetime.utcnow().isoformat()}
                )

        except Exception as e:
            logger.error(f"Failed to handle content publish failed: {e}")

    async def _handle_data_pipeline_completed(self, payload: Dict) -> None:
        """Handle data pipeline completed event"""
        try:
            pipeline_id = payload.get("pipeline_id")
            steps_completed = payload.get("steps_completed", [])

            logger.info(f"Data pipeline completed: {pipeline_id} ({len(steps_completed)} steps)")

            # Could trigger downstream analytics or notifications

        except Exception as e:
            logger.error(f"Failed to handle data pipeline completed: {e}")

    # Export Manager Interface (from data-exporter/)
    async def create_export_job(self, export_type: str, format: str,
                               date_from: Optional[str] = None, date_to: Optional[str] = None,
                               metadata: Optional[Dict] = None) -> str:
        """Create a new data export job"""
        return await self.export_manager.create_export_job(export_type, format, date_from, date_to, metadata)

    async def get_export_job(self, job_id: str) -> Optional[Dict]:
        """Get export job by ID"""
        return await self.export_manager.get_export_job(job_id)

    async def list_export_jobs(self, export_type: Optional[str] = None,
                              status: Optional[str] = None) -> List[Dict]:
        """List export jobs"""
        return await self.export_manager.list_export_jobs(export_type, status)

    async def trigger_export(self, export_type: str) -> List[str]:
        """Trigger export for all formats"""
        return await self.export_manager.trigger_export(export_type)

    # Artifact Manager Interface (from artifact-writer/)
    async def create_artifact(self, artifact_type: str, file_path: str,
                             content: bytes, metadata: Optional[Dict] = None) -> str:
        """Create and store artifact"""
        return await self.artifact_manager.create_artifact(artifact_type, file_path, content, metadata)

    async def validate_artifact(self, artifact_id: str) -> Dict:
        """Validate artifact"""
        return await self.artifact_manager.validate_artifact(artifact_id)

    async def get_artifact(self, artifact_id: str) -> Optional[Dict]:
        """Get artifact by ID"""
        return await self.artifact_manager.get_artifact(artifact_id)

    async def commit_manifest(self, manifest_data: Dict) -> str:
        """Commit artifact manifest"""
        return await self.artifact_manager.commit_manifest(manifest_data)

    # Audit Manager Interface (from execution-audit/)
    async def record_audit_event(self, event_type: str, event_data: Dict,
                                 kafka_info: Optional[Dict] = None) -> str:
        """Record immutable audit event"""
        return await self.audit_manager.record_audit_event(event_type, event_data, kafka_info)

    async def get_audit_records(self, event_type: Optional[str] = None,
                               from_date: Optional[datetime] = None,
                               to_date: Optional[datetime] = None) -> List[Dict]:
        """Get audit records"""
        return await self.audit_manager.get_audit_records(event_type, from_date, to_date)

    async def generate_daily_report(self, target_date: Optional[str] = None) -> Dict:
        """Generate daily audit report"""
        return await self.audit_manager.generate_daily_report(target_date)

    # Publishing Manager Interface (from publisher/)
    async def publish_content(self, content_data: Dict, platforms: List[str],
                             metadata: Optional[Dict] = None) -> List[str]:
        """Publish content to multiple platforms"""
        return await self.publishing_manager.publish_content(content_data, platforms, metadata)

    async def get_publishing_operation(self, operation_id: str) -> Optional[Dict]:
        """Get publishing operation by ID"""
        return await self.publishing_manager.get_publishing_operation(operation_id)

    async def list_publishing_operations(self, platform: Optional[str] = None,
                                        status: Optional[str] = None) -> List[Dict]:
        """List publishing operations"""
        return await self.publishing_manager.list_publishing_operations(platform, status)

    async def get_platform_status(self, platform: str) -> Dict:
        """Get publishing platform status"""
        return await self.publishing_manager.get_platform_status(platform)

    # Cross-Platform Data Operations
    async def create_data_pipeline(self, pipeline_name: str, steps: List[Dict]) -> str:
        """Create data processing pipeline"""
        pipeline_id = f"pipeline_{uuid4().hex[:12]}"

        # Execute pipeline steps
        completed_steps = []
        try:
            for step in steps:
                step_type = step.get("type")
                step_params = step.get("params", {})

                if step_type == "export":
                    job_id = await self.create_export_job(**step_params)
                    completed_steps.append({"type": step_type, "job_id": job_id})

                elif step_type == "artifact":
                    artifact_id = await self.create_artifact(**step_params)
                    completed_steps.append({"type": step_type, "artifact_id": artifact_id})

                elif step_type == "audit":
                    audit_id = await self.record_audit_event(**step_params)
                    completed_steps.append({"type": step_type, "audit_id": audit_id})

                elif step_type == "publish":
                    operation_ids = await self.publish_content(**step_params)
                    completed_steps.append({"type": step_type, "operation_ids": operation_ids})

            # Publish pipeline completion event
            if self.event_bus:
                self.event_bus.publish_data_pipeline_completed(pipeline_id, {
                    "pipeline_name": pipeline_name,
                    "steps_completed": completed_steps
                })

            return pipeline_id

        except Exception as e:
            logger.error(f"Data pipeline failed: {pipeline_id} - {e}")
            raise

    # Platform Analytics
    async def get_platform_statistics(self) -> Dict:
        """Get data platform usage statistics"""
        try:
            export_jobs = await self.list_export_jobs()
            publishing_ops = await self.list_publishing_operations()

            stats = {
                "total_export_jobs": len(export_jobs),
                "completed_exports": len([j for j in export_jobs if j.get("status") == "completed"]),
                "total_publishing_operations": len(publishing_ops),
                "published_content": len([p for p in publishing_ops if p.get("publish_status") == "published"]),
                "export_formats": {},
                "publishing_platforms": {}
            }

            # Count by type
            for job in export_jobs:
                format_name = job.get("format", "unknown")
                stats["export_formats"][format_name] = stats["export_formats"].get(format_name, 0) + 1

            for op in publishing_ops:
                platform = op.get("platform", "unknown")
                stats["publishing_platforms"][platform] = stats["publishing_platforms"].get(platform, 0) + 1

            return stats

        except Exception as e:
            logger.error(f"Failed to get platform statistics: {e}")
            return {}

    async def get_data_health_report(self) -> Dict:
        """Generate data platform health report"""
        try:
            return {
                "status": "healthy",
                "modules": {
                    "export_manager": self.export_manager.is_healthy() if self.export_manager else False,
                    "artifact_manager": self.artifact_manager.is_healthy() if self.artifact_manager else False,
                    "audit_manager": self.audit_manager.is_healthy() if self.audit_manager else False,
                    "publishing_manager": self.publishing_manager.is_healthy() if self.publishing_manager else False
                },
                "statistics": await self.get_platform_statistics(),
                "timestamp": datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error(f"Failed to generate health report: {e}")
            return {"status": "unhealthy", "error": str(e)}

    async def shutdown(self) -> None:
        """Shutdown the data platform service"""
        try:
            # Stop event consumer
            if self.event_bus:
                self.event_bus.stop_consumer()

            # Shutdown modules
            if self.export_manager:
                await self.export_manager.shutdown()
            if self.artifact_manager:
                await self.artifact_manager.shutdown()
            if self.audit_manager:
                await self.audit_manager.shutdown()
            if self.publishing_manager:
                await self.publishing_manager.shutdown()

            # Shutdown infrastructure
            if self.event_bus:
                await self.event_bus.shutdown()

            # Close store
            if self.store:
                await self.store.close()

            logger.info("Data platform service shutdown complete")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            raise