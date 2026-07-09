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
RUN apt-get update && apt-get install -y libpq-dev gcc curl git golang \
 && pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# tokei — secondary detector & LoC counter (static binary, fast)
RUN set -eux; \
    TOKEI_VERSION="12.1.2"; \
    case "$(uname -m)" in \
      x86_64)  ARCH="x86_64-unknown-linux-gnu" ;; \
      aarch64) ARCH="aarch64-unknown-linux-gnu" ;; \
      *) ARCH="x86_64-unknown-linux-gnu" ;; \
    esac; \
    curl -fsSL \
      "https://github.com/XAMPPRocky/tokei/releases/download/v${TOKEI_VERSION}/tokei-${ARCH}.tar.gz" \
      | tar -xz -C /usr/local/bin tokei; \
    chmod 755 /usr/local/bin/tokei

# enry — fallback detector (Go port of linguist, lightweight)
RUN GOPATH=/go go install github.com/go-enry/go-enry/v2/cmd/enry@latest \
 && cp /go/bin/enry /usr/local/bin/enry \
 && chmod 755 /usr/local/bin/enry

# ── Stage 2: runtime ─────────────────────────
FROM python:3.12-slim AS runtime

# Create a non-root user for security and install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 git ca-certificates \
    ruby ruby-dev ruby-full cmake pkg-config build-essential \
    libssl-dev libicu-dev libcurl4-openssl-dev \
    zlib1g-dev libonig-dev \
 && gem install github-linguist \
 && ln -sf $(which github-linguist) /usr/local/bin/linguist \
 && useradd --create-home --shell /bin/bash appuser \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Verify that tokei, enry, and linguist are available and functional in the runtime image
RUN tokei --version && enry --version && github-linguist --version && linguist --version

# Copy application code

COPY *.py ./
COPY observability/ ./observability/
COPY scan_repository/ ./scan_repository/
COPY scan_jobs/ ./scan_jobs/
COPY core/ ./core/
COPY auth/ ./auth/
COPY api_keys/ ./api_keys/
COPY sandboxes/ ./sandboxes/
COPY proxy/ ./proxy/
COPY health/ ./health/
COPY ratelimit/ ./ratelimit/
COPY services/ ./services/
COPY subscriptions/ ./subscriptions/


# Switch to non-root user
USER appuser

# Expose the API port
EXPOSE 8000

# Health check — hits /health every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# Start the server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
