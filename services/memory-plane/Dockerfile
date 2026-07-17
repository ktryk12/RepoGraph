FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

# ── Service-specific dependencies ──────────────────────────────────────────
COPY services/memory_plane/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Service code ───────────────────────────────────────────────────────────
COPY services/memory_plane/src/ .

USER appuser

EXPOSE 8101

CMD ["python", "api.py"]
