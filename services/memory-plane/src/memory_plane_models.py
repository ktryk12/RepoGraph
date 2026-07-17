"""SQLAlchemy models for memory-plane service"""

from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Float,
    LargeBinary,
    ForeignKey,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

# Create declarative base
Base = declarative_base()

# Export metadata for Alembic
metadata = Base.metadata


class Memory(Base):
    """Memories table - stores memory content and metadata"""
    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    content = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    entity_type = Column(String)
    entity_id = Column(String)
    metadata_json = Column(Text)  # JSON string (renamed from metadata to avoid SQLAlchemy conflict)
    created_at = Column(Float, nullable=False)  # Unix timestamp as REAL
    importance = Column(Float, default=0.5)

    # Relationship to embeddings
    embedding = relationship("Embedding", back_populates="memory", uselist=False)

    def __repr__(self):
        return f"<Memory(id={self.id}, entity_type='{self.entity_type}', entity_id='{self.entity_id}')>"


class Embedding(Base):
    """Embeddings table - stores vector embeddings for memories"""
    __tablename__ = "embeddings"

    memory_id = Column(Integer, ForeignKey("memories.id"), primary_key=True)
    vector = Column(LargeBinary, nullable=False)  # Pickled vector data
    dim = Column(Integer, nullable=False)  # Dimension of the vector

    # Relationship to memory
    memory = relationship("Memory", back_populates="embedding")

    def __repr__(self):
        return f"<Embedding(memory_id={self.memory_id}, dim={self.dim})>"