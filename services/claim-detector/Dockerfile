FROM python:3.12-slim

WORKDIR /app

COPY services/claim-detector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
CMD ["python", "services/claim-detector/main.py"]
