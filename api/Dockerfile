# api/Dockerfile

FROM python:3.13-slim

WORKDIR /app

# OCR fallback for scanned PDFs (no embedded text layer)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (better layer caching)
COPY pyproject.toml .
# src/ must exist before editable install - setuptools needs it for egg_info
RUN mkdir -p src && pip install --no-cache-dir -e ".[dev]"

# Source code (mounted as volume in dev, baked in for prod builds)
COPY src/ ./src/

# No CMD - defined in docker-compose.yml
