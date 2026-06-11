import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Inject current directory into python path to load core modules correctly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.queue.stats import QueueStatsTracker


def test_queue_stats_tracker():
    tracker = QueueStatsTracker()

    # Test initial throughput
    assert tracker.get_throughput("scan.quick") == 0.0

    # Record processed
    tracker.record_processed("scan.quick")
    tracker.record_processed("scan.quick")

    # Throughput over 60 seconds should be 2 / 60.0 = 0.03
    assert tracker.get_throughput("scan.quick") == 0.03

    # Throughput over 1 second should be 2 / 1.0 = 2.0
    assert tracker.get_throughput("scan.quick", window_seconds=1.0) == 2.0


def test_queue_stats_endpoint_unavailable():
    # Test endpoint when rabbitmq is unavailable
    from auth import validate_token
    from codeinspectior_api import app

    app.dependency_overrides[validate_token] = lambda: {"sub": "test_user"}
    try:
        with patch("core.queue.router.get_connection", return_value=None):
            client = TestClient(app)
            response = client.get(
                "/v1/queue/stats", headers={"Authorization": "Bearer test-token"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["available"] is False
            assert data["queues"] == {}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_queue_stats_endpoint_available():
    from auth import validate_token
    from codeinspectior_api import app

    # Mock connection and channel
    mock_conn = MagicMock()
    mock_conn.is_closed = False

    mock_channel = AsyncMock()
    mock_conn.channel.return_value.__aenter__.return_value = mock_channel

    mock_queue = MagicMock()
    mock_queue.declaration_result.message_count = 5
    mock_queue.declaration_result.consumer_count = 2
    mock_channel.declare_queue.return_value = mock_queue

    app.dependency_overrides[validate_token] = lambda: {"sub": "test_user"}
    try:
        with patch("core.queue.router.get_connection", return_value=mock_conn):
            client = TestClient(app)
            # Seed stats
            from core import state

            state.queue_stats.record_processed("scan.quick")

            response = client.get(
                "/v1/queue/stats", headers={"Authorization": "Bearer test-token"}
            )
            assert response.status_code == 200
            data = response.json()
            assert data["available"] is True
            assert "scan.quick" in data["queues"]

            q_stats = data["queues"]["scan.quick"]
            assert q_stats["depth"] == 5
            assert q_stats["consumers"] == 2
            assert q_stats["throughput"] > 0.0
    finally:
        app.dependency_overrides.clear()


def test_public_queue_stats_endpoint_unavailable():
    from codeinspectior_api import app

    with patch("core.queue.router.get_connection", return_value=None):
        client = TestClient(app)
        # Verify no Authorization header is needed
        response = client.get("/queue-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["available"] is False
        assert data["queues"] == {}
