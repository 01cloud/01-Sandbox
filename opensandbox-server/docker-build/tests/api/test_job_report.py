import os
import sys

import pytest
from fastapi import status
from fastapi.testclient import TestClient

# Inject project root into Python path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from src.main import app

client = TestClient(app)


def test_get_scan_report_success(tmp_path):
    """
    SCENARIO: A valid JSON scan report exists.
    EXPECTATION: Returns the parsed JSON report.
    """
    job_id = "test-job-uuid-1234"
    report_dir = tmp_path / job_id / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "security_scan_report.json"

    mock_report = {"summary": {"issues": 0}, "findings": []}
    import json

    report_file.write_text(json.dumps(mock_report))

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SCAN_DATA_ROOT", str(tmp_path))

        response = client.get(f"/api/v1/01sbx/scan-jobs/{job_id}/report")

        assert response.status_code == status.HTTP_200_OK
        assert response.json() == mock_report


def test_get_scan_report_not_found():
    """
    SCENARIO: No report file exists for the job ID.
    EXPECTATION: Returns 404 Not Found.
    """
    job_id = "nonexistent-job-uuid"

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SCAN_DATA_ROOT", "/nonexistent_dir")

        response = client.get(f"/api/v1/01sbx/scan-jobs/{job_id}/report")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert data["code"] == "REPORT_NOT_FOUND"


def test_get_scan_report_corrupted(tmp_path):
    """
    SCENARIO: The report file contains corrupted, invalid JSON.
    EXPECTATION: Returns 500 Internal Server Error with FILE_READ_ERROR code.
    """
    job_id = "test-job-uuid-1234"
    report_dir = tmp_path / job_id / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / "security_scan_report.json"

    report_file.write_text("invalid json content {")

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SCAN_DATA_ROOT", str(tmp_path))

        response = client.get(f"/api/v1/01sbx/scan-jobs/{job_id}/report")

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert data["code"] == "FILE_READ_ERROR"
