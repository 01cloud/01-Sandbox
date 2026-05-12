# ─────────────────────────────────────────────
# Dockerfile — Sandbox FastAPI App
# Build:  docker build -t sandbox-api .
# Run:    docker run -p 8000:8000 sandbox-api
# Test:   docker build --no-cache -t sandbox-api .
# ─────────────────────────────────────────────

# ── Stage 1: builder ─────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install dependencies into a separate layer for caching
COPY requirements.txt .
RUN apt-get update && apt-get install -y libpq-dev gcc \
 && pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime ─────────────────────────
FROM python:3.12-slim AS runtime

# Create a non-root user for security
RUN apt-get update && apt-get install -y libpq5 \
 && useradd --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code

COPY *.py *.pem ./

# Switch to non-root user
USER appuser

# Expose the API port
EXPOSE 8000

# Health check — hits /health every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Start the server
CMD ["uvicorn", "codeinspectior_api:app", "--host", "0.0.0.0", "--port", "8000"]