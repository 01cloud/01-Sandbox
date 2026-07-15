import asyncio
import os
import shutil
import sys
from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

# Inject project root into Python path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from src.main import app

client = TestClient(app)


def test_delete_scan_job_success(tmp_path):
    """
    SCENARIO: Deleting a scan job without sandbox termination.
    EXPECTATION: Deletes the directory from the filesystem and returns 204 No Content.
    """
    job_id = "test-job-uuid-1234"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SCAN_DATA_ROOT", str(tmp_path))

        response = client.delete(f"/api/v1/01sbx/scan-jobs/{job_id}")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not job_dir.exists()


def test_delete_scan_job_with_termination(tmp_path):
    """
    SCENARIO: Deleting a scan job and requesting termination of active sandboxes.
    EXPECTATION: Queries active sandboxes, terminates the matching ones, and deletes filesystem directory.
    """
    job_id = "test-job-uuid-1234"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Mock matching sandbox
    matching_sb = MagicMock()
    matching_sb.id = "sb-match"
    matching_sb.metadata = {"job_id": job_id}

    # Mock non-matching sandbox
    other_sb = MagicMock()
    other_sb.id = "sb-other"
    other_sb.metadata = {"job_id": "different-job"}

    mock_res = MagicMock()
    mock_res.items = [matching_sb, other_sb]

    mock_service = MagicMock()
    mock_service.list_sandboxes.return_value = mock_res

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SCAN_DATA_ROOT", str(tmp_path))
        mp.setattr("src.api.lifecycle.sandbox_service", mock_service)

        response = client.delete(f"/api/v1/01sbx/scan-jobs/{job_id}?terminate=true")

        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify it attempted to delete only the matching sandbox
        mock_service.delete_sandbox.assert_called_once_with("sb-match")
        assert not job_dir.exists()


def test_delete_scan_job_filesystem_failure(tmp_path):
    """
    SCENARIO: The filesystem directory is locked or permissions are denied.
    EXPECTATION: Retries deletion and eventually raises 500 FILE_SYSTEM_ERROR.
    """
    job_id = "test-job-uuid-1234"
    job_dir = tmp_path / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Mock shutil.rmtree to raise PermissionError
    def mock_rmtree(path, *args, **kwargs):
        raise PermissionError("Access denied")

    async def mock_sleep(seconds):
        pass

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SCAN_DATA_ROOT", str(tmp_path))
        mp.setattr(shutil, "rmtree", mock_rmtree)
        # Prevent sleeping during retries to make the test instant
        mp.setattr(asyncio, "sleep", mock_sleep)

        response = client.delete(f"/api/v1/01sbx/scan-jobs/{job_id}")

        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        data = response.json()
        assert data["code"] == "FILE_SYSTEM_ERROR"
        assert "Access denied" in data["message"]
