FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/repo:/app/repo/services:/app/repo/services/expert-serving/src

WORKDIR /app/repo

RUN adduser --disabled-password --gecos "" appuser

COPY shared/ /shared/
COPY core/ /app/repo/core/
COPY policy/ /app/repo/policy/
COPY ops/ /app/repo/ops/
COPY schemas/ /app/repo/schemas/
COPY docs/ /app/repo/docs/
COPY schemas/ /app/schemas/
COPY docs/ /app/docs/
COPY services/aesa/ /app/repo/services/aesa/
COPY models/production/ /app/models/production/

RUN pip install --no-cache-dir /shared/

COPY services/expert-serving/requirements.txt /tmp/expert-serving-requirements.txt
RUN pip install --no-cache-dir -r /tmp/expert-serving-requirements.txt

COPY services/expert-serving/src/ /app/repo/services/expert-serving/src/

USER appuser

EXPOSE 8094

CMD ["uvicorn", "expert_serving_service:create_app", "--factory", "--host", "0.0.0.0", "--port", "8094"]
