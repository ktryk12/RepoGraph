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

RUN pip install --no-cache-dir /shared/

# ── Additional repo modules used by this service ───────────────────────────
COPY babyai/ /app/babyai/

# ── Service-specific dependencies ──────────────────────────────────────────
COPY services/lora-orchestrator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Service code ───────────────────────────────────────────────────────────
COPY services/lora-orchestrator/src/ .

USER appuser

CMD ["python", "main.py"]
