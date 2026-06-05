FROM python:3.12-slim

WORKDIR /app

# Install deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy pipeline source as a package so relative imports work
COPY . pipeline/

# data/ is a volume mount — just ensure the dir exists
RUN mkdir -p /app/data

EXPOSE 8888

# Run as pipeline.review_ui.app so Python resolves relative imports correctly
CMD ["uvicorn", "pipeline.review_ui.app:app", "--host", "0.0.0.0", "--port", "8888", "--reload"]
