"""
Export Manager Module

Consolidated from services/data-exporter/
Provides data export in multiple formats: JSON-LD, CSV, NDJSON.
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from uuid import uuid4
import json
import csv
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class ExportManager:
    """
    Data export service

    Consolidated functionality from data-exporter service:
    - JSON-LD export (schema.org/ClaimReview format)
    - CSV export for analysts
    - NDJSON export for data scientists
    - Scheduled and on-demand exports
    """

    def __init__(self, store, event_bus=None):
        self.store = store
        self.event_bus = event_bus

        # Configuration
        self.export_dir = Path(os.getenv("EXPORT_DIR", "artifacts/exports"))
        self.fact_check_db = os.getenv("FACT_CHECK_DB", "artifacts/fact_check/claims.db")
        self.audit_db = os.getenv("AUDIT_DB", "artifacts/audit/audit.db")

        # Supported export types and formats
        self.export_types = ["claims", "trades", "audit"]
        self.export_formats = ["jsonld", "csv", "ndjson"]

        # Ensure export directory exists
        self.export_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Initialize export manager"""
        try:
            logger.info("Export manager initialized")

            # Validate configuration
            if not self.export_dir.exists():
                self.export_dir.mkdir(parents=True, exist_ok=True)

        except Exception as e:
            logger.error(f"Failed to initialize export manager: {e}")
            raise

    async def create_export_job(self, export_type: str, format: str,
                               date_from: Optional[str] = None, date_to: Optional[str] = None,
                               metadata: Optional[Dict] = None) -> str:
        """Create a new data export job"""
        try:
            job_id = f"export_{uuid4().hex[:12]}"

            # Validate parameters
            if export_type not in self.export_types:
                raise ValueError(f"Invalid export type: {export_type}. Supported: {self.export_types}")

            if format not in self.export_formats:
                raise ValueError(f"Invalid format: {format}. Supported: {self.export_formats}")

            # Create job record
            await self.store.create_export_job(
                job_id=job_id,
                export_type=export_type,
                format=format,
                date_from=date_from,
                date_to=date_to,
                metadata=metadata
            )

            # Publish job started event
            if self.event_bus:
                self.event_bus.publish_export_job_started(job_id, {
                    "export_type": export_type,
                    "format": format,
                    "date_from": date_from,
                    "date_to": date_to
                })

            # Process job asynchronously
            asyncio.create_task(self._process_export_job(job_id))

            logger.info(f"Export job created: {job_id} ({export_type} -> {format})")
            return job_id

        except Exception as e:
            logger.error(f"Failed to create export job: {e}")
            raise

    async def _process_export_job(self, job_id: str) -> None:
        """Process export job"""
        try:
            # Get job data
            job = await self.store.get_export_job(job_id)
            if not job:
                raise ValueError(f"Job not found: {job_id}")

            # Update status to processing
            await self.store.update_export_job(job_id, status="processing")

            start_time = datetime.utcnow()

            # Perform export based on type and format
            export_type = job["export_type"]
            format_type = job["format"]

            result = await self._perform_export(export_type, format_type, job)

            # Calculate duration
            duration_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            # Update job completion
            await self.store.update_export_job(
                job_id,
                status="completed",
                output_path=result["file_path"],
                records_exported=result["records_count"],
                file_size_bytes=result["file_size"],
                completed_at=datetime.utcnow()
            )

            # Publish completion event
            if self.event_bus:
                self.event_bus.publish_export_job_completed(job_id, {
                    "duration_ms": duration_ms,
                    "output_path": result["file_path"],
                    "records_exported": result["records_count"],
                    "file_size_bytes": result["file_size"]
                })

            logger.info(f"Export job completed: {job_id}")

        except Exception as e:
            logger.error(f"Export job failed: {job_id} - {e}")

            # Update job as failed
            await self.store.update_export_job(job_id, status="failed")

            # Publish failure event
            if self.event_bus:
                self.event_bus.publish_export_job_failed(job_id, {
                    "error": str(e)
                })

    async def _perform_export(self, export_type: str, format_type: str, job: Dict) -> Dict:
        """Perform the actual export operation"""
        try:
            # Generate output path
            today = datetime.utcnow().strftime("%Y-%m-%d")
            output_dir = self.export_dir / format_type / today
            output_dir.mkdir(parents=True, exist_ok=True)

            filename = f"{export_type}_{job['job_id']}.{format_type}"
            output_path = output_dir / filename

            # Mock data for demonstration
            # In real implementation, this would query the appropriate databases
            mock_data = self._generate_mock_data(export_type, 100)

            # Export based on format
            if format_type == "jsonld":
                result = await self._export_jsonld(mock_data, output_path, export_type)
            elif format_type == "csv":
                result = await self._export_csv(mock_data, output_path, export_type)
            elif format_type == "ndjson":
                result = await self._export_ndjson(mock_data, output_path, export_type)
            else:
                raise ValueError(f"Unsupported format: {format_type}")

            return {
                "file_path": str(output_path),
                "records_count": result["records_count"],
                "file_size": result["file_size"]
            }

        except Exception as e:
            logger.error(f"Export operation failed: {e}")
            raise

    def _generate_mock_data(self, export_type: str, count: int) -> List[Dict]:
        """Generate mock data for export"""
        mock_data = []

        for i in range(count):
            if export_type == "claims":
                mock_data.append({
                    "claim_id": f"claim_{i}",
                    "claim_text": f"Mock claim statement {i}",
                    "verdict": "true" if i % 3 == 0 else "false",
                    "confidence": 0.85 + (i % 10) * 0.01,
                    "created_at": datetime.utcnow().isoformat(),
                    "sources": [f"source_{i}_1", f"source_{i}_2"]
                })
            elif export_type == "trades":
                mock_data.append({
                    "trade_id": f"trade_{i}",
                    "symbol": f"SYMBOL{i % 10}",
                    "action": "buy" if i % 2 == 0 else "sell",
                    "quantity": 100 + i,
                    "price": 50.0 + i * 0.5,
                    "timestamp": datetime.utcnow().isoformat()
                })
            elif export_type == "audit":
                mock_data.append({
                    "audit_id": f"audit_{i}",
                    "event_type": f"order.{['intent', 'executed', 'failed'][i % 3]}",
                    "event_data": {"mock": f"data_{i}"},
                    "recorded_at": datetime.utcnow().isoformat()
                })

        return mock_data

    async def _export_jsonld(self, data: List[Dict], output_path: Path, export_type: str) -> Dict:
        """Export data in JSON-LD format"""
        try:
            jsonld_data = {
                "@context": "https://schema.org",
                "@type": "Dataset",
                "name": f"{export_type.title()} Data Export",
                "description": f"Exported {export_type} data in JSON-LD format",
                "dateCreated": datetime.utcnow().isoformat(),
                "mainEntity": data
            }

            # Write to file
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(jsonld_data, f, indent=2, ensure_ascii=False)

            file_size = output_path.stat().st_size

            return {
                "records_count": len(data),
                "file_size": file_size
            }

        except Exception as e:
            logger.error(f"JSON-LD export failed: {e}")
            raise

    async def _export_csv(self, data: List[Dict], output_path: Path, export_type: str) -> Dict:
        """Export data in CSV format"""
        try:
            if not data:
                # Create empty file
                output_path.touch()
                return {"records_count": 0, "file_size": 0}

            # Write CSV
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=data[0].keys())
                writer.writeheader()
                writer.writerows(data)

            file_size = output_path.stat().st_size

            return {
                "records_count": len(data),
                "file_size": file_size
            }

        except Exception as e:
            logger.error(f"CSV export failed: {e}")
            raise

    async def _export_ndjson(self, data: List[Dict], output_path: Path, export_type: str) -> Dict:
        """Export data in NDJSON format"""
        try:
            # Write NDJSON (one JSON object per line)
            with open(output_path, "w", encoding="utf-8") as f:
                for record in data:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

            file_size = output_path.stat().st_size

            return {
                "records_count": len(data),
                "file_size": file_size
            }

        except Exception as e:
            logger.error(f"NDJSON export failed: {e}")
            raise

    async def get_export_job(self, job_id: str) -> Optional[Dict]:
        """Get export job by ID"""
        try:
            return await self.store.get_export_job(job_id)

        except Exception as e:
            logger.error(f"Failed to get export job {job_id}: {e}")
            return None

    async def list_export_jobs(self, export_type: Optional[str] = None,
                              status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """List export jobs"""
        try:
            return await self.store.list_export_jobs(export_type, status, limit)

        except Exception as e:
            logger.error(f"Failed to list export jobs: {e}")
            return []

    async def trigger_export(self, export_type: str) -> List[str]:
        """Trigger export for all formats"""
        try:
            job_ids = []

            for format_type in self.export_formats:
                job_id = await self.create_export_job(export_type, format_type)
                job_ids.append(job_id)

            logger.info(f"Triggered exports for {export_type}: {job_ids}")
            return job_ids

        except Exception as e:
            logger.error(f"Failed to trigger exports for {export_type}: {e}")
            raise

    async def get_export_statistics(self) -> Dict:
        """Get export statistics"""
        try:
            jobs = await self.list_export_jobs()

            stats = {
                "total_jobs": len(jobs),
                "completed_jobs": len([j for j in jobs if j.get("status") == "completed"]),
                "failed_jobs": len([j for j in jobs if j.get("status") == "failed"]),
                "by_type": {},
                "by_format": {}
            }

            # Count by type and format
            for job in jobs:
                export_type = job.get("export_type", "unknown")
                format_type = job.get("format", "unknown")

                stats["by_type"][export_type] = stats["by_type"].get(export_type, 0) + 1
                stats["by_format"][format_type] = stats["by_format"].get(format_type, 0) + 1

            return stats

        except Exception as e:
            logger.error(f"Failed to get export statistics: {e}")
            return {}

    def is_healthy(self) -> bool:
        """Check if export manager is healthy"""
        return (
            self.store is not None and
            self.export_dir.exists()
        )

    async def shutdown(self) -> None:
        """Shutdown export manager"""
        try:
            logger.info("Export manager shutdown complete")

        except Exception as e:
            logger.error(f"Error during export manager shutdown: {e}")
            raise