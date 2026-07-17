FROM python:3.12-slim

WORKDIR /app

COPY services/order-manager/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
CMD ["python", "services/order-manager/main.py"]
