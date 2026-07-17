FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy service code
COPY . ./services/exercise_runner/

# Copy shared exercise models/hooks
COPY ../../shared/exercise ./shared/exercise/

ENV PYTHONUNBUFFERED=1 \
    EXERCISE_MODE=dry_run \
    DRY_RUN=true \
    EXERCISE_ALLOW_EXTERNAL_WRITES=false \
    KAFKA_BOOTSTRAP_SERVERS=kafka:9092

WORKDIR /app/services/exercise_runner
ENTRYPOINT ["python", "main.py"]
