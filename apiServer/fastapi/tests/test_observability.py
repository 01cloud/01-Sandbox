import logging

import pytest
from codeinspectior_api import app
from fastapi.testclient import TestClient


def test_metrics_endpoint():
    """Verify that the /metrics endpoint is exposed and returns Prometheus formatted text."""
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert "# HELP" in response.text


def test_correlation_id_middleware():
    """Verify that requests receive a unique X-Correlation-ID in response headers."""
    client = TestClient(app)
    response = client.get("/v1/health")
    assert response.status_code in [200, 401, 404]  # We just care about headers
    assert "X-Correlation-ID" in response.headers
    assert len(response.headers["X-Correlation-ID"]) > 0


def test_correlation_id_propagation():
    """Verify that an incoming X-Correlation-ID header is propagated back in the response."""
    client = TestClient(app)
    custom_corr_id = "test-correlation-12345"
    response = client.get("/v1/health", headers={"X-Correlation-ID": custom_corr_id})
    assert response.headers.get("X-Correlation-ID") == custom_corr_id
