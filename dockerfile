FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1
WORKDIR /app

# PyTorch CPU necesita libgomp; el resto de dependencias llegan en wheels.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.cache/huggingface \
    DOCLING_ARTIFACTS_PATH=/app/.cache/docling

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]