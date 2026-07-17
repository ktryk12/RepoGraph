FROM python:3.12-slim
WORKDIR /app
COPY services/trust-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app
CMD ["python", "services/trust-api/main.py"]
