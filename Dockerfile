# Stage 1: builder — needs gcc for tree-sitter compilation
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
COPY repograph/ repograph/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install ".[cache,postgres]"

# Stage 2: runtime — no build tools
FROM python:3.13-slim AS runtime

COPY --from=builder /install /usr/local
COPY --from=builder /app/repograph /app/repograph
COPY --from=builder /app/pyproject.toml /app/pyproject.toml

WORKDIR /app

RUN useradd -r -u 1000 -m repograph && \
    mkdir -p /data && \
    chown repograph:repograph /data

ENV REPOGRAPH_HOST=0.0.0.0
ENV REPOGRAPH_PORT=8001
ENV REPOGRAPH_DB_PATH=/data/repograph
ENV REPOGRAPH_DB_BACKEND=cog
ENV REPOGRAPH_TENANT_ID=default

VOLUME ["/data"]
EXPOSE 8001

USER repograph
CMD ["repograph"]
