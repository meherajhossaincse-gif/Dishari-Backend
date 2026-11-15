FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app:/app/ml_package
WORKDIR /app
# OCR for question-paper photos/PDFs (Bangla + English) — free, offline
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-ben tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-ml.txt ./
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir -r requirements-ml.txt
COPY . .
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh
EXPOSE 8000
CMD ["./start.sh"]
