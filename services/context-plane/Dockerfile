FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/repo:/app/repo/services:/app/repo/services/context-plane/src

WORKDIR /app/repo

RUN adduser --disabled-password --gecos "" appuser

COPY shared/ /shared/
COPY core/ /app/repo/core/
COPY policy/ /app/repo/policy/
COPY ops/ /app/repo/ops/
COPY storage/ /app/repo/storage/
COPY verify/ /app/repo/verify/
COPY schemas/ /app/repo/schemas/
COPY docs/ /app/repo/docs/
COPY schemas/ /app/schemas/
COPY docs/ /app/docs/
COPY services/aesa/ /app/repo/services/aesa/

COPY services/context-plane/requirements.txt /tmp/context-plane-requirements.txt
RUN pip install --no-cache-dir /shared/ \
    && pip install --no-cache-dir -r /tmp/context-plane-requirements.txt

COPY services/context-plane/src/ /app/repo/services/context-plane/src/

USER appuser

EXPOSE 8092

CMD ["uvicorn", "context_plane_service:create_app", "--factory", "--host", "0.0.0.0", "--port", "8092"]
