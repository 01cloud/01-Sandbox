from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

metrics_router = APIRouter(tags=["Observability Metrics"])

# ──────────────────────────────────────────────────────────
# 📊 PROMETHEUS METRIC DEFINITIONS
# ──────────────────────────────────────────────────────────

# HTTP API Metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total number of HTTP requests processed by the API gateway",
    ["method", "path", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request execution latency in seconds",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

# Background Worker & Queue Scan Metrics
job_execution_duration_seconds = Histogram(
    "job_execution_duration_seconds",
    "Scan job execution latency in seconds",
    ["job_type"],  # 'quick-scan' or 'repo-scan'
    buckets=(5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 900.0),
)

job_execution_total = Counter(
    "job_execution_total",
    "Total number of scan jobs processed by queue workers",
    ["job_type", "status"],  # status: 'success', 'failure', 'retry', 'cancelled'
)

# Infrastructure/Operation Metrics
sandbox_provision_duration_seconds = Histogram(
    "sandbox_provision_duration_seconds",
    "Latency of dynamic sandbox folder provisioning in seconds",
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0),
)

repo_clone_duration_seconds = Histogram(
    "repo_clone_duration_seconds",
    "Time taken to clone git repositories in seconds",
    ["repo_host"],  # e.g., 'github.com', 'gitlab.com'
    buckets=(1.0, 3.0, 5.0, 10.0, 20.0, 45.0, 90.0, 180.0),
)


@metrics_router.get("/metrics")
def get_metrics():
    """
    Prometheus scraping endpoint.
    Exposes all registered application metrics.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
