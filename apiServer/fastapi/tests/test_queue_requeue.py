import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Inject current directory into python path to load core modules correctly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.queue.dlq import DLQ_QUEUE_NAME
from core.queue.job_types import EXCHANGE_NAME
from core.queue.requeue import requeue_failed_jobs


@pytest.mark.asyncio
async def test_requeue_failed_jobs_service():
    # Mocking connection & channel
    mock_conn = MagicMock()
    mock_conn.is_closed = False

    mock_channel = AsyncMock()
    mock_conn.channel.return_value.__aenter__.return_value = mock_channel

    # Mock DLQ Queue
    mock_dlq = MagicMock()
    mock_dlq.declaration_result.message_count = 1
    mock_channel.declare_queue.return_value = mock_dlq

    # Mock Message in DLQ
    mock_msg = AsyncMock()
    job_payload = {
        "job_id": "test-job-id-123",
        "job_type": "quick-scan",
        "retry_count": 3,
    }
    mock_msg.body = json.dumps(job_payload).encode()
    mock_msg.headers = {
        "x-death": [{"routing-keys": ["scan.quick"], "exchange": EXCHANGE_NAME}]
    }

    # We call get twice: first returns mock_msg, second returns None
    get_calls = [mock_msg, None]

    async def side_effect(*args, **kwargs):
        if get_calls:
            return get_calls.pop(0)
        return None

    mock_dlq.get = side_effect

    # Mock Exchange
    mock_ex = AsyncMock()
    mock_channel.declare_exchange.return_value = mock_ex

    # Run service
    with patch("core.queue.requeue.get_connection", return_value=mock_conn):
        res = await requeue_failed_jobs()
        assert res["success"] is True
        assert res["requeued"] == 1
        assert res["retained"] == 0

        # Assert ack was called
        mock_msg.ack.assert_called_once()

        # Assert message was published back to main exchange with routing key scan.quick
        mock_ex.publish.assert_called_once()
        published_msg = mock_ex.publish.call_args[0][0]
        published_routing_key = mock_ex.publish.call_args[1]["routing_key"]

        assert published_routing_key == "scan.quick"
        published_body = json.loads(published_msg.body.decode())
        assert published_body["job_id"] == "test-job-id-123"
        assert published_body["retry_count"] == 0


@pytest.mark.asyncio
async def test_requeue_failed_jobs_router():
    from auth import validate_token
    from codeinspectior_api import app

    app.dependency_overrides[validate_token] = lambda: {"sub": "test_user"}
    try:
        with patch("core.queue.router.requeue_failed_jobs") as mock_requeue:
            mock_requeue.return_value = {"success": True, "requeued": 1, "retained": 0}
            client = TestClient(app)
            response = client.post(
                "/v1/queue/requeue-failed",
                headers={"Authorization": "Bearer test-token"},
                json={"job_id": "test-job-id-123"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["requeued"] == 1
            mock_requeue.assert_called_once_with("test-job-id-123")
    finally:
        app.dependency_overrides.clear()
