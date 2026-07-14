import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

# ===========================================================================
# UNIT TESTS for the Active Sandbox Lifecycle Endpoints.
# Covers GET, DELETE, and POST pause/resume/renew-expiration endpoints.
# ===========================================================================

# Inject project root (two folders up from tests/api) into Python path
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from src.main import app

# Create a TestClient wrapper around the app to make HTTP calls in memory
client = TestClient(app)


def test_get_sandbox_success():
    """
    SCENARIO: Retrieve information for an existing sandbox.
    EXPECTATION: GET /sandboxes/{sandbox_id} calls sandbox_service.get_sandbox and returns the detailed info.
    """
    sandbox_id = "test-sb-123"

    # 1. Setup the mock Sandbox response object matching the Sandbox schema
    mock_sandbox = MagicMock()
    mock_sandbox.id = sandbox_id
    mock_sandbox.image.uri = "python:3.11"
    mock_sandbox.image.auth = None
    mock_sandbox.status.state = "Running"
    mock_sandbox.status.reason = None
    mock_sandbox.status.message = None
    mock_sandbox.status.last_transition_at = datetime.now(timezone.utc)
    mock_sandbox.metadata = {"app": "test"}
    mock_sandbox.entrypoint = ["python", "-c", "print(1)"]
    mock_sandbox.expires_at = datetime.now(timezone.utc)
    mock_sandbox.created_at = datetime.now(timezone.utc)

    # 2. Patch sandbox_service.get_sandbox to return our mock Sandbox
    with pytest.MonkeyPatch.context() as mp:
        mock_service = MagicMock()
        mock_service.get_sandbox.return_value = mock_sandbox
        mp.setattr("src.api.lifecycle.sandbox_service", mock_service)

        # 3. Call the API endpoint
        response = client.get(f"/api/v1/01sbx/sandboxes/{sandbox_id}")

        # 4. Assert response code and check matching contents
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == sandbox_id
        assert data["status"]["state"] == "Running"
        mock_service.get_sandbox.assert_called_once_with(sandbox_id)


def test_delete_sandbox_success():
    """
    SCENARIO: Delete/terminate an existing sandbox.
    EXPECTATION: DELETE /sandboxes/{sandbox_id} calls sandbox_service.delete_sandbox and returns HTTP 204 No Content.
    """
    sandbox_id = "test-sb-123"

    # 1. Setup the mocked sandbox_service
    with pytest.MonkeyPatch.context() as mp:
        mock_service = MagicMock()
        mp.setattr("src.api.lifecycle.sandbox_service", mock_service)

        # 2. Call the deletion endpoint
        response = client.delete(f"/api/v1/01sbx/sandboxes/{sandbox_id}")

        # 3. Assert HTTP status code is 204 (No Content) and delete was called on backend service
        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_service.delete_sandbox.assert_called_once_with(sandbox_id)


def test_pause_sandbox_success():
    """
    SCENARIO: Pause execution of a running sandbox.
    EXPECTATION: POST /sandboxes/{sandbox_id}/pause calls sandbox_service.pause_sandbox and returns HTTP 202 Accepted.
    """
    sandbox_id = "test-sb-123"

    # 1. Setup the mocked sandbox_service
    with pytest.MonkeyPatch.context() as mp:
        mock_service = MagicMock()
        mp.setattr("src.api.lifecycle.sandbox_service", mock_service)

        # 2. Call the pause endpoint
        response = client.post(f"/api/v1/01sbx/sandboxes/{sandbox_id}/pause")

        # 3. Assert HTTP status code is 202 (Accepted) and pause was triggered in the backend
        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_service.pause_sandbox.assert_called_once_with(sandbox_id)


def test_resume_sandbox_success():
    """
    SCENARIO: Resume execution of a paused sandbox.
    EXPECTATION: POST /sandboxes/{sandbox_id}/resume calls sandbox_service.resume_sandbox and returns HTTP 202 Accepted.
    """
    sandbox_id = "test-sb-123"

    # 1. Setup the mocked sandbox_service
    with pytest.MonkeyPatch.context() as mp:
        mock_service = MagicMock()
        mp.setattr("src.api.lifecycle.sandbox_service", mock_service)

        # 2. Call the resume endpoint
        response = client.post(f"/api/v1/01sbx/sandboxes/{sandbox_id}/resume")

        # 3. Assert HTTP status code is 202 (Accepted) and resume was triggered in the backend
        assert response.status_code == status.HTTP_202_ACCEPTED
        mock_service.resume_sandbox.assert_called_once_with(sandbox_id)


def test_renew_sandbox_expiration_success():
    """
    SCENARIO: Extend/renew the expiration TTL for an active sandbox.
    EXPECTATION: POST /sandboxes/{sandbox_id}/renew-expiration sends new time and returns HTTP 200 OK with updated expiry date.
    """
    from src.api.schema import RenewSandboxExpirationResponse

    sandbox_id = "test-sb-123"
    new_expiry_str = "2026-07-20T12:00:00Z"

    # 1. Setup mock response model mapping the schema
    mock_res = RenewSandboxExpirationResponse(
        expiresAt=datetime.fromisoformat(new_expiry_str.replace("Z", "+00:00"))
    )

    # 2. Mock renew_expiration on sandbox_service
    with pytest.MonkeyPatch.context() as mp:
        mock_service = MagicMock()
        mock_service.renew_expiration.return_value = mock_res
        mp.setattr("src.api.lifecycle.sandbox_service", mock_service)

        # 3. Make HTTP request with the renewal payload
        payload = {"expiresAt": new_expiry_str}
        response = client.post(
            f"/api/v1/01sbx/sandboxes/{sandbox_id}/renew-expiration", json=payload
        )

        # 4. Assert success and verify arguments passed to the backend service
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["expiresAt"].startswith("2026-07-20T12:00:00")

        mock_service.renew_expiration.assert_called_once()
        args, kwargs = mock_service.renew_expiration.call_args
        assert args[0] == sandbox_id
        assert args[1].expires_at == mock_res.expires_at
