FROM python:3.13-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App
COPY main.py ./

ENV PYTHONUNBUFFERED=1 \
    VIDEO_RENDERER=stub \
    KAFKA_BOOTSTRAP_SERVERS=kafka:9092

ENTRYPOINT ["python", "main.py"]
