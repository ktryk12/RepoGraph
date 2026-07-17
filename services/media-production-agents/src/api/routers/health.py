"""
Health check endpoints.
"""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response model."""
    status: str
    service: str
    version: str


@router.get("/", response_model=HealthResponse)
async def health_check():
    """Basic health check."""
    return HealthResponse(
        status="healthy",
        service="media-production-agents",
        version="1.0.0"
    )


@router.get("/ready")
async def readiness_check():
    """Readiness check - verify dependencies."""
    # Check Kafka connection, database, etc.
    return {"status": "ready"}


@router.get("/live")
async def liveness_check():
    """Liveness check - basic service health."""
    return {"status": "alive"}
