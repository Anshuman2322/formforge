FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Tesseract is a system binary, not a Python package — pytesseract (in
# requirements.txt) only calls it via subprocess (see backend/ocr_fallback.py).
# Without this, scanned/flattened PDFs fail with OcrUnavailable.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY backend/ backend/
COPY frontend/ frontend/

EXPOSE 8000
# Render sets $PORT at runtime; 8000 is only the local `docker run` default.
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
