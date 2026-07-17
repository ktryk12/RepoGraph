#!/bin/bash
set -e

# Wait for dependencies (Kafka, etc.)
echo "Starting orchestration-agents..."

# Run the service
exec uvicorn src.api.main:app --host 0.0.0.0 --port 8080
