import os
import shutil
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# Mock external runtime libraries to run test in standard env
class DummyException(Exception):
    pass


docker_mock = MagicMock()
docker_errors_mock = MagicMock()
docker_errors_mock.DockerException = DummyException
docker_errors_mock.ImageNotFound = DummyException
sys.modules["docker"] = docker_mock
sys.modules["docker.errors"] = docker_errors_mock

kubernetes_mock = MagicMock()
kubernetes_exceptions_mock = MagicMock()
kubernetes_exceptions_mock.ApiException = DummyException
sys.modules["kubernetes"] = kubernetes_mock
sys.modules["kubernetes.client"] = MagicMock()
sys.modules["kubernetes.config"] = MagicMock()
sys.modules["kubernetes.client.exceptions"] = kubernetes_exceptions_mock

# Add opensandbox-server/docker-build to path to import delete_scan_job
sys.path.insert(
    0,
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "../../../opensandbox-server/docker-build"
        )
    ),
)

from src.api.lifecycle import delete_scan_job


@pytest.mark.asyncio
async def test_delete_scan_job_retry_success():
    """Test that delete_scan_job retries on PermissionError/OSError and eventually succeeds."""
    temp_dir = tempfile.mkdtemp()
    parent_dir = os.path.dirname(temp_dir)
    job_id = os.path.basename(temp_dir)

    # We mock os.path.exists, shutil.rmtree, and os.environ
    mock_env = {"SCAN_DATA_ROOT": parent_dir}

    # Simulate shutil.rmtree failing twice with PermissionError (e.g. busy mount) and then succeeding
    failures = [0]
    original_rmtree = shutil.rmtree

    def mock_rmtree(path):
        if failures[0] < 2:
            failures[0] += 1
            raise OSError("Device or resource busy")
        return original_rmtree(path)

    with patch.dict(os.environ, mock_env), patch(
        "shutil.rmtree", side_effect=mock_rmtree
    ), patch("asyncio.sleep") as mock_sleep:
        # We also mock list_sandboxes and delete_sandbox to do nothing
        with patch(
            "src.api.lifecycle.sandbox_service.list_sandboxes",
            return_value=MagicMock(items=[]),
        ), patch("src.api.lifecycle.sandbox_service.delete_sandbox"):
            resp = await delete_scan_job(job_id=job_id, terminate=True)
            assert resp.status_code == 204
            assert failures[0] == 2
            assert mock_sleep.call_count == 2


@pytest.mark.asyncio
async def test_delete_scan_job_parent_cleanup():
    """Test that deleting a child job's directory also cleans up the parent directory if empty."""
    temp_parent = tempfile.mkdtemp()
    temp_child = tempfile.mkdtemp(dir=temp_parent)

    child_job_id = os.path.basename(temp_child)

    mock_env = {"SCAN_DATA_ROOT": os.path.dirname(temp_parent)}

    # We mock list_sandboxes and delete_sandbox to do nothing
    with patch.dict(os.environ, mock_env), patch(
        "src.api.lifecycle.sandbox_service.list_sandboxes",
        return_value=MagicMock(items=[]),
    ), patch("src.api.lifecycle.sandbox_service.delete_sandbox"):
        # Deleting the child job should clean up both child and parent directory
        resp = await delete_scan_job(job_id=child_job_id, terminate=True)
        assert resp.status_code == 204
        assert not os.path.exists(temp_child)
        assert not os.path.exists(temp_parent)
