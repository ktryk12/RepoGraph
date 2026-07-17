#!/usr/bin/env python3
"""
Agent Service Template Generator

Creates standardized microservice structure for agent services.
Following ADR-0015 agent microservice architecture.

Usage:
    python scripts/migration/create_agent_service.py --service-name agent-platform --phase 1
    python scripts/migration/create_agent_service.py --service-name verification-agents --agents "claim_router,evidence_gatherer"
"""

import argparse
import os
from pathlib import Path
from typing import List, Dict, Any
import yaml


class AgentServiceTemplate:
    """Generate agent microservice templates."""

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.services_dir = project_root / "services"

    def create_service(self, service_name: str, agents: List[str] = None, communication_pattern: str = "kafka-http"):
        """Create complete service structure."""
        service_dir = self.services_dir / service_name

        print(f"Creating agent service: {service_name}")
        print(f"Target directory: {service_dir}")

        # Create directory structure
        self._create_directory_structure(service_dir)

        # Create configuration files
        self._create_config_files(service_dir, service_name, communication_pattern)

        # Create source code templates
        self._create_source_templates(service_dir, service_name, agents or [])

        # Create tests
        self._create_test_templates(service_dir, service_name)

        # Create documentation
        self._create_documentation(service_dir, service_name, agents or [])

        print(f"SUCCESS: Service {service_name} created successfully!")
        return service_dir

    def _create_directory_structure(self, service_dir: Path):
        """Create standard microservice directory structure."""
        dirs = [
            "src",
            "src/domain",
            "src/agents",
            "src/api",
            "src/config",
            "tests",
            "tests/unit",
            "tests/integration",
            "docker",
            "docs"
        ]

        for dir_path in dirs:
            (service_dir / dir_path).mkdir(parents=True, exist_ok=True)

        # Create __init__.py files
        init_dirs = ["src", "src/domain", "src/agents", "src/api", "tests"]
        for init_dir in init_dirs:
            (service_dir / init_dir / "__init__.py").touch()

    def _create_config_files(self, service_dir: Path, service_name: str, communication_pattern: str):
        """Create service configuration files."""

        # requirements.txt
        requirements_content = """# Core dependencies
fastapi>=0.104.0
uvicorn>=0.24.0
pydantic>=2.5.0
httpx>=0.24.0

# BabyAI shared libraries
-e ../../libs/babyai-schemas
-e ../../libs/babyai-bus
-e ../../libs/babyai-observability
-e ../../libs/babyai-config-client

# Agent framework
-e ../../libs/babyai-utils

# Optional: Add specific dependencies per service type
# kafka-python>=2.0.2  # For Kafka-heavy services
# pillow>=10.0.0       # For media services
# pandas>=2.1.0        # For intelligence services
"""

        with open(service_dir / "requirements.txt", "w") as f:
            f.write(requirements_content)

        # Docker configuration
        dockerfile_content = f"""FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY docker/entrypoint.sh ./

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \\
    CMD curl -f http://localhost:8080/health || exit 1

# Run service
ENTRYPOINT ["./entrypoint.sh"]
"""

        with open(service_dir / "Dockerfile", "w") as f:
            f.write(dockerfile_content)

        # Docker entrypoint
        entrypoint_content = f"""#!/bin/bash
set -e

# Wait for dependencies (Kafka, etc.)
echo "Starting {service_name}..."

# Run the service
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8080
"""

        entrypoint_path = service_dir / "docker" / "entrypoint.sh"
        with open(entrypoint_path, "w") as f:
            f.write(entrypoint_content)
        entrypoint_path.chmod(0o755)

        # Service configuration
        config_content = f"""# {service_name} Configuration
service:
  name: {service_name}
  version: "1.0.0"
  port: 8080

kafka:
  bootstrap_servers: "localhost:9092"
  consumer_group: "{service_name}-group"

logging:
  level: INFO
  format: json

health:
  check_interval: 30
  timeout: 10
"""

        with open(service_dir / "config.yaml", "w") as f:
            f.write(config_content)

    def _create_source_templates(self, service_dir: Path, service_name: str, agents: List[str]):
        """Create source code templates."""

        # Main FastAPI application
        main_app_content = f'''"""
{service_name} FastAPI Application

Agent microservice following ADR-0015 architecture.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from babyai_observability import get_logger
from babyai_config_client import get_config

from .routers import health, agents
from .middleware import auth_middleware, logging_middleware

logger = get_logger("{service_name}")

app = FastAPI(
    title="{service_name.replace('-', ' ').title()}",
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
    logger.info(f"Starting {{service_name}}")
    # Initialize Kafka connections, agent registry, etc.


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info(f"Shutting down {{service_name}}")


if __name__ == "__main__":
    config = get_config()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=config.get("service.port", 8080)
    )
'''

        with open(service_dir / "src" / "api" / "main.py", "w") as f:
            f.write(main_app_content)

        # Health router
        health_router_content = f'''"""
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
        service="{service_name}",
        version="1.0.0"
    )


@router.get("/ready")
async def readiness_check():
    """Readiness check - verify dependencies."""
    # Check Kafka connection, database, etc.
    return {{"status": "ready"}}


@router.get("/live")
async def liveness_check():
    """Liveness check - basic service health."""
    return {{"status": "alive"}}
'''

        # Create routers directory
        (service_dir / "src" / "api" / "routers").mkdir(exist_ok=True)
        (service_dir / "src" / "api" / "routers" / "__init__.py").touch()

        with open(service_dir / "src" / "api" / "routers" / "health.py", "w") as f:
            f.write(health_router_content)

        # Agents router template
        agents_router_content = f'''"""
Agent management endpoints for {service_name}.
"""

from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from pydantic import BaseModel

from babyai_schemas import AgentTaskEvent, AgentCompletionEvent
from babyai_bus import create_event_bus_for_service

router = APIRouter()


class AgentRequest(BaseModel):
    """Base agent request model."""
    task_type: str
    payload: Dict[str, Any]
    priority: str = "normal"
    context_id: str = None


class AgentResponse(BaseModel):
    """Base agent response model."""
    task_id: str
    status: str
    message: str


@router.post("/execute", response_model=AgentResponse)
async def execute_agent_task(request: AgentRequest):
    """Execute an agent task."""
    try:
        # TODO: Implement agent task execution
        # 1. Validate request
        # 2. Route to appropriate agent
        # 3. Execute task
        # 4. Return response

        return AgentResponse(
            task_id="uuid-placeholder",
            status="accepted",
            message="Task queued for execution"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status/{{task_id}}")
async def get_task_status(task_id: str):
    """Get status of a specific task."""
    # TODO: Implement task status lookup
    return {{"task_id": task_id, "status": "running"}}


@router.get("/capabilities")
async def get_agent_capabilities():
    """Get list of agent capabilities."""
    # TODO: Return actual agent capabilities
    return {{"capabilities": ["placeholder"]}}
'''

        with open(service_dir / "src" / "api" / "routers" / "agents.py", "w") as f:
            f.write(agents_router_content)

        # Middleware templates
        middleware_content = f'''"""
Middleware for {service_name}.
"""

from fastapi import Request, Response
import time
import uuid

from babyai_observability import get_logger
from babyai_auth import verify_service_token

logger = get_logger("{service_name}")


async def logging_middleware(request: Request, call_next):
    """Log all requests and responses."""
    start_time = time.time()
    request_id = str(uuid.uuid4())

    logger.info("Request started", extra={{
        "request_id": request_id,
        "method": request.method,
        "url": str(request.url),
        "user_agent": request.headers.get("user-agent")
    }})

    response = await call_next(request)

    process_time = time.time() - start_time
    logger.info("Request completed", extra={{
        "request_id": request_id,
        "status_code": response.status_code,
        "process_time": process_time
    }})

    return response


async def auth_middleware(request: Request, call_next):
    """Authenticate inter-service requests."""
    # Skip auth for health checks
    if request.url.path.startswith("/health"):
        return await call_next(request)

    # Validate service token for /v1/ endpoints
    if request.url.path.startswith("/v1/"):
        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return Response("Unauthorized", status_code=401)

        try:
            token = auth_header.split(" ")[1]
            service_info = await verify_service_token(token)
            request.state.calling_service = service_info
        except Exception as e:
            logger.warning(f"Auth validation failed: {{e}}")
            return Response("Unauthorized", status_code=401)

    return await call_next(request)
'''

        (service_dir / "src" / "api" / "middleware.py").touch()
        with open(service_dir / "src" / "api" / "middleware.py", "w") as f:
            f.write(middleware_content)

    def _create_test_templates(self, service_dir: Path, service_name: str):
        """Create test templates."""

        # Test configuration
        test_config = f'''"""
Test configuration for {service_name}.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture
def client():
    """Test client fixture."""
    return TestClient(app)


@pytest.fixture
def test_config():
    """Test configuration fixture."""
    return {{
        "service": {{
            "name": "{service_name}",
            "port": 8080
        }},
        "kafka": {{
            "bootstrap_servers": "localhost:9092"
        }}
    }}
'''

        with open(service_dir / "tests" / "conftest.py", "w") as f:
            f.write(test_config)

        # Unit test template
        unit_test = f'''"""
Unit tests for {service_name} health endpoints.
"""

def test_health_check(client):
    """Test basic health check."""
    response = client.get("/health/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "{service_name}"


def test_readiness_check(client):
    """Test readiness check."""
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_liveness_check(client):
    """Test liveness check."""
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"
'''

        with open(service_dir / "tests" / "unit" / "test_health.py", "w") as f:
            f.write(unit_test)

    def _create_documentation(self, service_dir: Path, service_name: str, agents: List[str]):
        """Create service documentation."""

        readme_content = f'''# {service_name.replace("-", " ").title()}

BabyAI Agent Microservice following ADR-0015 architecture.

## Overview

This service manages the following agents:
{chr(10).join(f"- {agent}" for agent in agents) if agents else "- TBD"}

## Architecture

```
{service_name}/
|-- src/
|   |-- api/          # FastAPI application
|   |-- agents/       # Agent implementations
|   +-- domain/       # Domain logic
|-- tests/            # Unit and integration tests
+-- docker/           # Docker configuration
```

## API Endpoints

### Health Checks
- `GET /health/` - Basic health check
- `GET /health/ready` - Readiness check
- `GET /health/live` - Liveness check

### Agent Management
- `POST /v1/agents/execute` - Execute agent task
- `GET /v1/agents/status/{{task_id}}` - Get task status
- `GET /v1/agents/capabilities` - List agent capabilities

## Development

### Setup
```bash
cd services/{service_name}
pip install -r requirements.txt
```

### Run Locally
```bash
python -m src.api.main
```

### Run Tests
```bash
pytest tests/
```

### Docker
```bash
docker build -t {service_name} .
docker run -p 8080:8080 {service_name}
```

## Configuration

Service configuration is in `config.yaml`. Key settings:

- `service.port` - Service port (default: 8080)
- `kafka.bootstrap_servers` - Kafka brokers
- `logging.level` - Log level

## Deployment

This service is deployed as part of the BabyAI agent platform. See main repository documentation for deployment instructions.
'''

        with open(service_dir / "README.md", "w", encoding="utf-8") as f:
            f.write(readme_content)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Create agent service template")
    parser.add_argument("--service-name", required=True, help="Service name (e.g., agent-platform)")
    parser.add_argument("--agents", help="Comma-separated list of agents")
    parser.add_argument("--phase", type=int, help="Migration phase (1-4)")
    parser.add_argument("--communication-pattern",
                       choices=["kafka-http", "kafka-pipeline", "http-only"],
                       default="kafka-http",
                       help="Primary communication pattern")

    args = parser.parse_args()

    # Find project root
    project_root = Path(__file__).parent.parent.parent

    # Parse agents list
    agents = []
    if args.agents:
        agents = [agent.strip() for agent in args.agents.split(",")]

    # Create service
    template_generator = AgentServiceTemplate(project_root)
    service_dir = template_generator.create_service(
        service_name=args.service_name,
        agents=agents,
        communication_pattern=args.communication_pattern
    )

    print(f"\nSUCCESS: Service created at: {service_dir}")
    print(f"\nNext steps:")
    print(f"1. cd {service_dir}")
    print(f"2. pip install -r requirements.txt")
    print(f"3. python -m src.api.main")
    print(f"4. Visit http://localhost:8080/docs for API docs")


if __name__ == "__main__":
    main()