FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

# ── Shared library ─────────────────────────────────────────────────────────
COPY shared/ /shared/
COPY aesa/ /app/aesa/
COPY core/ /app/core/
COPY policy/ /app/policy/
COPY bus/ /app/bus/

RUN pip install --no-cache-dir /shared/

# ── Service-specific dependencies ──────────────────────────────────────────
COPY services/request-gate/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "import confluent_kafka"

# ── Service code ───────────────────────────────────────────────────────────
COPY storage/ /app/storage/
COPY services/request-gate/src/ .

USER appuser

EXPOSE 8097

CMD ["python", "main.py"]
