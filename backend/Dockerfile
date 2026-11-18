FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ /app/backend/
COPY scripts/ /app/scripts/
COPY data/ /app/data/
ENV FLEETPILOT_DATASET_PATH=/app/data/device-telemetry-dataset.ndjson
EXPOSE 8000
# Migrate + idempotent ingest on every start, then serve.
CMD ["sh", "-c", "python /app/scripts/ingest_dataset.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
