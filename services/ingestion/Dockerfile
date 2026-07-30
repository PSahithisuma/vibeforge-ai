FROM python:3.12-slim

WORKDIR /app

# System deps needed by Docling (poppler for PDF, tesseract for OCR)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    libgl1 \
    libglib2.0-0 \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# bge-m3 and Docling models download to /root/.cache — shared via model_cache volume
CMD ["python", "ingestion.py"]
