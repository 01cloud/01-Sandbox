import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Inject directory into python path to import correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.delete_job import perform_job_deletion
from scan_repository.file_scanner import (
    _submit_scan_job,
    active_child_jobs_by_parent,
    all_child_jobs_by_parent,
)


@pytest.mark.asyncio
async def test_cascading_job_deletion_in_memory():
    """Verify that deleting a parent job cascades to all in-memory child jobs."""
    # Setup mock state
    mock_state = MagicMock()
    mock_state.use_redis = False
    mock_state.redis_client = None
    mock_state.job_tracker = MagicMock()
    mock_state.active_tasks = {}

    parent_job_id = "parent-xyz"
    child_job_id_1 = "child-1"
    child_job_id_2 = "child-2"

    # Register child jobs
    all_child_jobs_by_parent[parent_job_id].add(child_job_id_1)
    all_child_jobs_by_parent[parent_job_id].add(child_job_id_2)
    active_child_jobs_by_parent[parent_job_id].add(child_job_id_1)

    with patch(
        "core.delete_job.cancel_active_task", new_callable=AsyncMock
    ) as mock_cancel, patch(
        "asyncio.to_thread", new_callable=AsyncMock
    ) as mock_to_thread:
        await perform_job_deletion(mock_state, parent_job_id, purge=True)

        # Assert cancel was called for parent and children
        cancel_calls = [c[0][1] for c in mock_cancel.call_args_list]
        assert parent_job_id in cancel_calls
        assert child_job_id_1 in cancel_calls
        assert child_job_id_2 in cancel_calls

        # Assert delete_scan_job was called for parent and children
        delete_args = [c[0][1] for c in mock_to_thread.call_args_list]
        assert parent_job_id in delete_args
        assert child_job_id_1 in delete_args
        assert child_job_id_2 in delete_args

        # Assert clean up of in-memory maps
        assert parent_job_id not in all_child_jobs_by_parent
        assert parent_job_id not in active_child_jobs_by_parent


@pytest.mark.asyncio
async def test_cascading_job_deletion_redis():
    """Verify that deleting a parent job cascades to all child jobs tracked in Redis."""
    mock_state = MagicMock()
    mock_state.use_redis = True

    mock_redis = MagicMock()
    # Mock smembers returning a set of child IDs
    mock_redis.smembers.return_value = {b"child-redis-1", b"child-redis-2"}
    mock_state.redis_client = mock_redis
    mock_state.job_tracker = MagicMock()
    mock_state.active_tasks = {}

    parent_job_id = "parent-redis"

    with patch(
        "core.delete_job.cancel_active_task", new_callable=AsyncMock
    ) as mock_cancel, patch(
        "asyncio.to_thread", new_callable=AsyncMock
    ) as mock_to_thread:
        await perform_job_deletion(mock_state, parent_job_id, purge=True)

        # Redis members were queried
        mock_redis.smembers.assert_called_once_with("job:parent-redis:child_jobs")

        # Assert cancel was called for parent and Redis children
        cancel_calls = [c[0][1] for c in mock_cancel.call_args_list]
        assert parent_job_id in cancel_calls
        assert "child-redis-1" in cancel_calls
        assert "child-redis-2" in cancel_calls

        # Assert delete_scan_job was called for parent and Redis children
        delete_args = [c[0][1] for c in mock_to_thread.call_args_list]
        assert parent_job_id in delete_args
        assert "child-redis-1" in delete_args
        assert "child-redis-2" in delete_args

        # Redis child tracker set was deleted
        mock_redis.delete.assert_any_call("job:parent-redis:child_jobs")


@pytest.mark.asyncio
async def test_child_job_registration_in_submit():
    """Test that _submit_scan_job registers the child job in maps and Redis."""
    parent_job_id = "parent-submit-test"

    # Mock global/state configs
    mock_state = MagicMock()
    mock_state.use_redis = True
    mock_redis = MagicMock()
    mock_state.redis_client = mock_redis

    with patch(
        "scan_repository.file_scanner.opensandbox_base_url", return_value="http://mock"
    ), patch(
        "scan_repository.file_scanner.opensandbox_route_prefix", return_value="/v1"
    ), patch(
        "scan_repository.file_scanner.opensandbox_headers", return_value={}
    ), patch(
        "core.state", mock_state
    ), patch(
        "httpx.AsyncClient"
    ) as mock_client_class:
        # Mock successful httpx response
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"report": {"findings": []}}
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_class.return_value.__aenter__.return_value = mock_client

        # Submit scan job with parent_job_id
        await _submit_scan_job(
            files_dict={"main.py": "print(1)"}, parent_job_id=parent_job_id
        )

        # Verify registration in in-memory maps
        assert parent_job_id in all_child_jobs_by_parent
        child_ids = all_child_jobs_by_parent[parent_job_id]
        assert len(child_ids) == 1
        child_job_id = list(child_ids)[0]

        # Verify Redis sadd was called
        mock_redis.sadd.assert_called_once_with(
            f"job:{parent_job_id}:child_jobs", child_job_id
        )
        mock_redis.expire.assert_called_once_with(
            f"job:{parent_job_id}:child_jobs", 86400
        )
