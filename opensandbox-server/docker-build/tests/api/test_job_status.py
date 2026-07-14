# Inject project root (two folders up from tests/api) into Python path
import os
import sys
from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from src.main import app

# Create a TestClient wrapper around the app to make HTTP calls in memory
client = TestClient(app)


def test_get_latest_job_status_no_jobs():
    """
    SCENARIO: No scan job has been initiated yet.
    EXPECTATION: GET /scan-status and GET /job_status return 404 Not Found.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.api.lifecycle._latest_job_id", None)

        # Test scan-status without job_id (which falls back to _latest_job_id)
        response = client.get("/api/v1/01sbx/scan-status")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.json()["code"] == "NO_JOBS_FOUND"

        # Test job_status alias
        response_alias = client.get("/api/v1/01sbx/job_status")
        assert response_alias.status_code == status.HTTP_404_NOT_FOUND
        assert response_alias.json()["code"] == "NO_JOBS_FOUND"


def test_get_scan_status_from_log_file(tmp_path):
    """
    SCENARIO: A process log file exists for the scan job.
    EXPECTATION: Returns the log file content as plain text.
    """
    job_id = "test-job-uuid-1234"

    # Create the reports/process.log file inside a temporary directory
    log_dir = tmp_path / job_id / "reports"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "process.log"
    log_file.write_text("Security scan in progress...")

    with pytest.MonkeyPatch.context() as mp:
        # Override SCAN_DATA_ROOT env var to point to our tmp_path
        mp.setenv("SCAN_DATA_ROOT", str(tmp_path))

        response = client.get(f"/api/v1/01sbx/scan-status/{job_id}")

        assert response.status_code == status.HTTP_200_OK
        assert response.text == "Security scan in progress..."
        assert "text/plain" in response.headers["content-type"]


def test_get_scan_status_fallback_to_sandbox_service_active():
    """
    SCENARIO: No log file exists, but there is an active sandbox for the job.
    EXPECTATION: Queries sandbox_service and returns its runtime status.
    """
    job_id = "test-job-uuid-1234"

    # Setup mock sandbox with "Running" status
    mock_sandbox = MagicMock()
    mock_sandbox.id = "sandbox-999"
    mock_sandbox.status.state = "Running"

    mock_res = MagicMock()
    mock_res.items = [mock_sandbox]

    with pytest.MonkeyPatch.context() as mp:
        # Ensure no log files are found
        mp.setenv("SCAN_DATA_ROOT", "/nonexistent_dir")

        # Mock the sandbox service list method
        mock_service = MagicMock()
        mock_service.list_sandboxes.return_value = mock_res
        mp.setattr("src.api.lifecycle.sandbox_service", mock_service)

        response = client.get(f"/api/v1/01sbx/scan-status/{job_id}")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["job_id"] == job_id
        assert data["sandbox_id"] == "sandbox-999"
        assert data["status"] == "Running"


def test_get_scan_status_fallback_to_sandbox_service_not_found():
    """
    SCENARIO: Neither a log file nor an active sandbox exists for the job.
    EXPECTATION: Returns a 200 response indicating the job status is NOT_FOUND.
    """
    job_id = "test-job-uuid-1234"

    mock_res = MagicMock()
    mock_res.items = []

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SCAN_DATA_ROOT", "/nonexistent_dir")

        mock_service = MagicMock()
        mock_service.list_sandboxes.return_value = mock_res
        mp.setattr("src.api.lifecycle.sandbox_service", mock_service)

        response = client.get(f"/api/v1/01sbx/scan-status/{job_id}")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "NOT_FOUND"
        assert "No active sandbox found" in data["message"]


def test_get_latest_job_status_alias_success():
    """
    SCENARIO: An active job exists and GET /job_status is queried.
    EXPECTATION: Resolves latest job_id and successfully queries status.
    """
    job_id = "latest-job-123"

    mock_res = MagicMock()
    mock_res.items = []

    with pytest.MonkeyPatch.context() as mp:
        # Seed the latest job ID
        mp.setattr("src.api.lifecycle._latest_job_id", job_id)
        mp.setenv("SCAN_DATA_ROOT", "/nonexistent_dir")

        mock_service = MagicMock()
        mock_service.list_sandboxes.return_value = mock_res
        mp.setattr("src.api.lifecycle.sandbox_service", mock_service)

        response = client.get("/api/v1/01sbx/job_status")

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "NOT_FOUND"
