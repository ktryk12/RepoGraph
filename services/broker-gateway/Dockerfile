FROM python:3.12-slim

WORKDIR /app

# Create non-root user for security
RUN adduser --disabled-password --gecos "" appuser

COPY services/broker-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Switch to non-root user
USER appuser

ENV PYTHONPATH=/app
CMD ["python", "services/broker-gateway/main.py"]
