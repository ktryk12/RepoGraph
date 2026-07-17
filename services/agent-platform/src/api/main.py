"""
agent-platform FastAPI Application

Agent microservice following ADR-0015 architecture.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import logging

# Handle missing shared libraries gracefully
try:
    from babyai_observability import get_logger
    logger = get_logger("agent-platform")
except ImportError:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("agent-platform")

try:
    from babyai_config_client import get_config
except ImportError:
    def get_config():
        return {"service": {"port": 8080}}

from .routers import health, agents

# Import middleware with fallbacks
try:
    from .middleware import auth_middleware, logging_middleware
except ImportError:
    # Minimal middleware fallbacks
    async def auth_middleware(request, call_next):
        return await call_next(request)

    async def logging_middleware(request, call_next):
        logger.info(f"Request: {request.method} {request.url}")
        return await call_next(request)

app = FastAPI(
    title="Agent Platform",
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
