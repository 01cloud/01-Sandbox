import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Inject current directory into python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth import validate_token
from codeinspectior_api import app
from core.queue.delete_handler import handle_delete_job


@pytest.mark.asyncio
async def test_delete_endpoint_rabbitmq_available():
    app.dependency_overrides[validate_token] = lambda: {"sub": "test_user"}
    try:
        with patch("core.queue.is_available", return_value=True), patch(
            "core.queue.publish", new_callable=AsyncMock
        ) as mock_publish:
            client = TestClient(app)
            response = client.delete(
                "/v1/jobs/test-job-123?purge=true",
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == "test-job-123"
            assert data["status"] == "DELETE_QUEUED"
            mock_publish.assert_called_once_with(
                "scan.delete", {"job_id": "test-job-123", "purge": True}
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delete_endpoint_rabbitmq_unavailable():
    app.dependency_overrides[validate_token] = lambda: {"sub": "test_user"}
    try:
        with patch("core.queue.is_available", return_value=False):
            client = TestClient(app)
            response = client.delete(
                "/v1/jobs/test-job-123",
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 503
            assert "unavailable" in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_proxy_delete_endpoint():
    app.dependency_overrides[validate_token] = lambda: {"sub": "test_user"}
    try:
        with patch("core.queue.is_available", return_value=True), patch(
            "core.queue.publish", new_callable=AsyncMock
        ) as mock_publish:
            client = TestClient(app)
            response = client.delete(
                "/api/v1/some-backend/v1/jobs/test-job-abc?purge=false",
                headers={"Authorization": "Bearer test-token"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == "test-job-abc"
            assert data["status"] == "DELETE_QUEUED"
            mock_publish.assert_called_once_with(
                "scan.delete", {"job_id": "test-job-abc", "purge": False}
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_delete_handler():
    mock_state = MagicMock()
    mock_state.active_tasks = {}
    mock_state.job_tracker = MagicMock()

    mock_task = MagicMock()
    mock_state.active_tasks["test-job-xxx"] = mock_task

    with patch(
        "core.queue.delete_handler.cancel_active_task", new_callable=AsyncMock
    ) as mock_cancel, patch(
        "asyncio.to_thread", new_callable=AsyncMock
    ) as mock_to_thread:
        await handle_delete_job(mock_state, "test-job-xxx", purge=True)

        mock_cancel.assert_called_once_with(mock_state, "test-job-xxx")
        mock_to_thread.assert_called_once_with(
            mock_state.backend.delete_scan_job, "test-job-xxx", terminate=True
        )
        mock_state.job_tracker.delete_job.assert_called_once_with("test-job-xxx")
