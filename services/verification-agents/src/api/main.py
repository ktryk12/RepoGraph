"""
verification-agents FastAPI Application

Agent microservice following ADR-0015 architecture.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from babyai_observability import get_logger
from babyai_config_client import get_config

from .routers import health, agents
from .middleware import auth_middleware, logging_middleware

logger = get_logger("verification-agents")

app = FastAPI(
    title="Verification Agents",
    description="BabyAI Agent Microservice",
    version="1.0.0"
)

# Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.middleware("http")(auth_middleware)
app.middleware("http")(logging_middleware)

# Routes
app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(agents.router, prefix="/v1/agents", tags=["agents"])


@app.on_event("startup")
async def startup_event():
    """Initialize service on startup."""
    logger.info(f"Starting {service_name}")
    # Initialize Kafka connections, agent registry, etc.


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info(f"Shutting down {service_name}")


if __name__ == "__main__":
    config = get_config()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=config.get("service.port", 8080)
    )
