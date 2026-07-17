FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

# ── Shared library ─────────────────────────────────────────────────────────
COPY shared/ /shared/
COPY core/ /app/core/
COPY policy/ /app/policy/
COPY bus/ /app/bus/

RUN pip install --no-cache-dir /shared/

# ── Service-specific dependencies ──────────────────────────────────────────
COPY services/planner/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import confluent_kafka"

# ── Service code ───────────────────────────────────────────────────────────
COPY storage/ /app/storage/
COPY services/planner/src/ .

USER appuser

EXPOSE 8099

CMD ["python", "main.py"]
