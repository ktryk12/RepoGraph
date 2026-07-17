FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/repo:/app/repo/services:/app/repo/services/orchestrator-worker/src

WORKDIR /app/repo

RUN adduser --disabled-password --gecos "" appuser

COPY shared/ /shared/
COPY core/ /app/repo/core/
COPY policy/ /app/repo/policy/
COPY bus/ /app/repo/bus/
COPY storage/ /app/repo/storage/
COPY agents/ /app/repo/agents/
COPY truth/ /app/repo/truth/
COPY privacy/ /app/repo/privacy/
COPY babyai/ /app/repo/babyai/
COPY tools/ /app/repo/tools/
COPY verify/ /app/repo/verify/
COPY config/ /app/repo/config/
COPY docs/ /app/repo/docs/
COPY schemas/ /app/repo/schemas/
COPY docs/ /app/docs/
COPY schemas/ /app/schemas/
COPY services/aesa/ /app/repo/services/aesa/
COPY services/artifact-writer/src/ /app/repo/services/artifact-writer/src/
COPY services/policy-validator/src/ /app/repo/services/policy-validator/src/
COPY services/tool-runtime/src/ /app/repo/services/tool-runtime/src/

COPY services/orchestrator-worker/requirements.txt /tmp/orchestrator-worker-requirements.txt
RUN pip install --no-cache-dir /shared/ \
    && pip install --no-cache-dir -r /tmp/orchestrator-worker-requirements.txt

COPY services/orchestrator-worker/src/ /app/repo/services/orchestrator-worker/src/

USER appuser

CMD ["python", "services/orchestrator-worker/src/main.py"]
