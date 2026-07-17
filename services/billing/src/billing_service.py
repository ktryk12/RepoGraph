"""
Billing Service

FastAPI application for subscription and billing management with Stripe integration.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .database.postgresql_billing_store import initialize_billing_store, close_billing_store
from .api.billing_router import router as billing_router
from .api.stripe_client import initialize_stripe_client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("billing-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management"""
    # Startup
    try:
        # Initialize database
        database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/billing_db")
        await initialize_billing_store(database_url)
        logger.info("Database store initialized")

        # Initialize Stripe client
        stripe_key = os.getenv("STRIPE_SECRET_KEY", "")
        initialize_stripe_client(stripe_key)
        logger.info("Stripe client initialized")

        logger.info("Billing service startup complete")

    except Exception as e:
        logger.error(f"Failed to initialize billing service: {e}")
        raise

    yield

    # Shutdown
    try:
        await close_billing_store()
        logger.info("Billing service shutdown complete")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


def create_app() -> FastAPI:
    """Create the FastAPI application"""
    app = FastAPI(
        title="Billing Service",
        description="Subscription and billing management with Stripe integration",
        version="1.0.0",
        lifespan=lifespan
    )

    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers
    app.include_router(billing_router, prefix="/api/v1", tags=["billing"])

    # Root endpoint
    @app.get("/")
    async def root():
        return {
            "service": "billing",
            "version": "1.0.0",
            "status": "running"
        }

    return app


# Create the app instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("BILLING_PORT", "8134"))
    uvicorn.run(
        "billing_service:app",
        host="0.0.0.0",
        port=port,
        reload=True
    )