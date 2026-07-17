FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1     PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" appuser

# ── Shared library ─────────────────────────────────────────────────────────
COPY shared/ /shared/
COPY aesa/ /app/aesa/
COPY policy/ /app/policy/
COPY bus/ /app/bus/

RUN pip install --no-cache-dir /shared/

# ── Service-specific dependencies ──────────────────────────────────────────
COPY services/verify/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Service code ───────────────────────────────────────────────────────────
COPY services/verify/src/ .

USER appuser

CMD ["python", "main.py"]
