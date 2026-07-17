# Truth Service Dockerfile
# ADR-0015 Phase 2: Database-per-Service

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY src/ ./src/
COPY database/ ./database/
COPY service_manifest.yaml .
COPY kafka_contract.yaml .

# Create database directory
RUN mkdir -p /app/data

# Set environment variables
ENV DATABASE_PATH=/app/data/truth.db
ENV HOST=0.0.0.0
ENV PORT=8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Expose port
EXPOSE 8080

# Run the service
CMD ["python", "-m", "src.main"]