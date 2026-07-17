# Media Production Agents

BabyAI Agent Microservice following ADR-0015 architecture.

## Overview

This service manages the following agents:
- video_edit_agent
- voice_overlay_agent
- comfyui_workplan_agent
- creative_brief_agent

## Architecture

```
media-production-agents/
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
- `GET /v1/agents/status/{task_id}` - Get task status
- `GET /v1/agents/capabilities` - List agent capabilities

## Development

### Setup
```bash
cd services/media-production-agents
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
docker build -t media-production-agents .
docker run -p 8080:8080 media-production-agents
```

## Configuration

Service configuration is in `config.yaml`. Key settings:

- `service.port` - Service port (default: 8080)
- `kafka.bootstrap_servers` - Kafka brokers
- `logging.level` - Log level

## Deployment

This service is deployed as part of the BabyAI agent platform. See main repository documentation for deployment instructions.
