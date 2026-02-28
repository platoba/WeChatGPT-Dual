FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Create knowledge store dir
RUN mkdir -p knowledge_store

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8900/health || exit 1

# Default: run webhook API
CMD ["uvicorn", "webhook:app", "--host", "0.0.0.0", "--port", "8900"]
