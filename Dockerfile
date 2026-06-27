FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY alembic ./alembic
COPY alembic.ini .
COPY run.py .
COPY entrypoint.sh .

EXPOSE 8000

CMD ["/app/entrypoint.sh"]
