import time
import uuid

from fastapi import Request
from observability.logging import set_correlation_id
from observability.metrics import (
    application_errors_total,
    http_request_duration_seconds,
    http_requests_total,
)
from starlette.middleware.base import BaseHTTPMiddleware


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware that ensures every request has a unique correlation ID.
    It reads 'X-Correlation-ID' or 'X-Request-ID' from incoming headers,
    generates a new one if missing, binds it to the context, and adds
    it to the outgoing response headers.
    """

    async def dispatch(self, request: Request, call_next):
        corr_id = request.headers.get("X-Correlation-ID") or request.headers.get(
            "X-Request-ID"
        )
        if not corr_id:
            corr_id = str(uuid.uuid4())

        set_correlation_id(corr_id)
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = corr_id
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Middleware that collects HTTP API request throughput and latency.
    Excludes the '/metrics' endpoint from measurement to avoid polling noise.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip metrics scraping path to keep metrics clean
        if path == "/metrics":
            return await call_next(request)

        start_time = time.time()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            # Re-raise exceptions while ensuring metrics are captured
            raise
        finally:
            duration = time.time() - start_time
            http_requests_total.labels(
                method=request.method, path=path, status_code=str(status_code)
            ).inc()
            http_request_duration_seconds.labels(
                method=request.method, path=path
            ).observe(duration)
            if status_code >= 500:
                application_errors_total.labels(
                    status_code=str(status_code), path=path
                ).inc()
