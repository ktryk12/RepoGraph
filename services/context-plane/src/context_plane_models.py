"""SQLAlchemy models for context-plane service"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    PrimaryKeyConstraint,
    Index,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func

# Create declarative base
Base = declarative_base()

# Export metadata for Alembic
metadata = Base.metadata


class ContextPayload(Base):
    """Context payloads table - stores ContextPlaneRecord objects as JSON"""
    __tablename__ = "context_payloads"

    context_id = Column(String, primary_key=True)
    payload_json = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<ContextPayload(context_id='{self.context_id}')>"


class ContextEntry(Base):
    """Context entries table - stores agent index records"""
    __tablename__ = "context_entries"

    doc_id = Column(String, nullable=False)
    doc_version = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    metadata_json = Column(Text)  # JSON string (renamed from metadata to avoid SQLAlchemy conflict)
    module_layer = Column(String)
    summary = Column(Text)
    exports = Column(Text)  # JSON array string
    internal_deps = Column(Text)  # JSON array string
    checksum = Column(String)
    ingested_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("doc_id", "doc_version"),
        Index("idx_module_layer", "module_layer"),
        Index("idx_checksum", "checksum"),
    )

    def __repr__(self):
        return f"<ContextEntry(doc_id='{self.doc_id}', doc_version='{self.doc_version}')>"


class DependencyGraph(Base):
    """Dependency graph table - stores relationships between documents"""
    __tablename__ = "dep_graph"

    from_doc_id = Column(String, nullable=False)
    to_doc_id = Column(String, nullable=False)
    dep_type = Column(String, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("from_doc_id", "to_doc_id", "dep_type"),
    )

    def __repr__(self):
        return f"<DependencyGraph(from='{self.from_doc_id}', to='{self.to_doc_id}', type='{self.dep_type}')>"


class ContextRetrieval(Base):
    """Context retrievals table - stores retrieval history for analytics"""
    __tablename__ = "context_retrievals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_description = Column(Text)
    task_type = Column(String)
    doc_ids_retrieved = Column(Text)  # JSON array string
    strategy_used = Column(String)
    was_useful = Column(Integer)  # 0/1 for boolean
    consumer = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<ContextRetrieval(id={self.id}, task_type='{self.task_type}')>"