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


def test_get_scan_source_success(tmp_path):
    """
    SCENARIO: A valid source file exists in the workspace.
    EXPECTATION: Downloads the file successfully.
    """
    job_id = "test-job-uuid-1234"
    workspace_dir = tmp_path / job_id / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    source_file = workspace_dir / "main.py"

    file_content = "print('hello world')"
    source_file.write_text(file_content)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SCAN_DATA_ROOT", str(tmp_path))

        response = client.get(f"/api/v1/01sbx/scan-jobs/{job_id}/workspace/main.py")

        assert response.status_code == status.HTTP_200_OK
        assert response.text == file_content


def test_get_scan_source_not_found():
    """
    SCENARIO: No source file exists for the given file path/job ID.
    EXPECTATION: Returns 404 Not Found.
    """
    job_id = "nonexistent-job-uuid"

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SCAN_DATA_ROOT", "/nonexistent_dir")

        response = client.get(f"/api/v1/01sbx/scan-jobs/{job_id}/workspace/main.py")

        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert data["code"] == "FILE_NOT_FOUND"
