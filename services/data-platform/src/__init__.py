"""
Data Platform Service

Consolidated data platform providing unified functionality from:
- data-exporter/ (Data export in multiple formats)
- artifact-writer/ (Artifact storage with contracts and validation)
- execution-audit/ (Immutable audit trails via Kafka)
- publisher/ (Content publishing to multiple platforms)
"""

from .data_platform_service import DataPlatformService
from .postgresql_data_store import PostgreSQLDataStore

__all__ = ["DataPlatformService", "PostgreSQLDataStore"]