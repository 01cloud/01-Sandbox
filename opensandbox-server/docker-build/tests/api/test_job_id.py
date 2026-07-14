# We import the FastAPI app instance from main
import os
import sys

import pytest
from fastapi import status

# You can use the TestClient class to test FastAPI
# applications without creating an actual HTTP and socket connection, just communicating directly
# with the FastAPI code.
from fastapi.testclient import TestClient

# Inject project root (two folders up from tests/api) into Python path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from src.main import app

# Create a TestClient wrapper around the app to make HTTP calls in memory
client = TestClient(app)


def test_get_latest_job_id_no_jobs():
    """
    SCENARIO: No scan job has been initiated yet in this session.
    EXPECTATION: GET /job_id returns a HTTP 404 Not Found error.
    """
    # 1. Patch the global variable in lifecycle to simulate an empty cache state
    # We patch the import location 'src.api.lifecycle._latest_job_id' to be None
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.api.lifecycle._latest_job_id", None)

        # 2. Call the endpoint using our TestClient
        response = client.get("/api/v1/01sbx/job_id")

        # 3. Assert the outcome
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert data["code"] == "NO_JOBS_FOUND"


def test_get_latest_job_id_success():
    """
    SCENARIO: A scan job was previously initiated (cached job_id exists).
    EXPECTATION: GET /job_id returns HTTP 200 OK containing the job ID.
    """
    expected_id = "test-job-uuid-1234"

    # 1. Patch the global variable to hold a mock job ID
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.api.lifecycle._latest_job_id", expected_id)

        # 2. Call the endpoint
        response = client.get("/api/v1/01sbx/job_id")

        # 3. Assert success and contents
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == {"job_id": expected_id}
