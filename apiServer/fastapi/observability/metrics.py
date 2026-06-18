from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

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

# Database Connection Pool Metrics
db_connections_active = Gauge(
    "db_connections_active",
    "Number of active PostgreSQL or SQLite database connections",
)

# Authentication & API Key Metrics
api_key_requests_total = Counter(
    "api_key_requests_total",
    "Total requests successfully authenticated by API Key ID",
    ["api_key_id"],
)

auth_failures_total = Counter(
    "auth_failures_total",
    "Total number of authentication failures classifed by reason",
    ["reason"],
)

# Queue Metrics
queue_depth_jobs = Gauge(
    "queue_depth_jobs", "Queue depth of RabbitMQ background scan jobs", ["queue_name"]
)

# Exception & Application Error Metrics
application_errors_total = Counter(
    "application_errors_total",
    "Total number of unhandled application exceptions and HTTP 500 errors",
    ["status_code", "path"],
)


@metrics_router.get("/metrics")
async def get_metrics():
    """
    Prometheus scraping endpoint.
    Exposes all registered application metrics.
    """
    # Dynamically update queue depths from RabbitMQ before generating metrics
    from core.queue.connection import get_connection

    conn = get_connection()
    if conn and not conn.is_closed:
        queues_to_query = ["scan.quick", "scan.repo", "scan.failed"]
        for q_name in queues_to_query:
            try:
                # Open a short-lived channel to passively declare the queue and read counts
                async with conn.channel() as ch:
                    q = await ch.declare_queue(q_name, passive=True)
                    depth = q.declaration_result.message_count
                    queue_depth_jobs.labels(queue_name=q_name).set(depth)
            except Exception:
                # Ignore failures if queue is not created yet or during startup connection jitter
                pass

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
