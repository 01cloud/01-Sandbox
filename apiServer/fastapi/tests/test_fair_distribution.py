import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Inject current directory into python path to load core modules correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.queue.consumer import _start_single_consumer
from core.queue.job_types import QUICK_SCAN


@pytest.mark.asyncio
async def test_start_single_consumer_prefetch_defaults():
    # Test that it uses the default prefetch (which should be 1 for quick-scan now)
    mock_conn = MagicMock()
    mock_channel = AsyncMock()
    mock_conn.channel = AsyncMock(return_value=mock_channel)

    mock_app_state = MagicMock()

    with patch(
        "core.queue.consumer.get_connection", return_value=mock_conn
    ), patch.dict(os.environ, {}, clear=True):
        await _start_single_consumer(QUICK_SCAN, mock_app_state)
        mock_channel.set_qos.assert_called_once_with(prefetch_count=1)


@pytest.mark.asyncio
async def test_start_single_consumer_prefetch_env_override():
    # Test that it uses the PREFETCH_QUICK_SCAN env variable
    mock_conn = MagicMock()
    mock_channel = AsyncMock()
    mock_conn.channel = AsyncMock(return_value=mock_channel)

    mock_app_state = MagicMock()
    mock_env = {"PREFETCH_QUICK_SCAN": "10"}

    with patch(
        "core.queue.consumer.get_connection", return_value=mock_conn
    ), patch.dict(os.environ, mock_env):
        await _start_single_consumer(QUICK_SCAN, mock_app_state)
        mock_channel.set_qos.assert_called_once_with(prefetch_count=10)


@pytest.mark.asyncio
async def test_start_single_consumer_prefetch_legacy_env_override():
    # Test that it falls back to MAX_QUICK_SCAN_WORKERS
    mock_conn = MagicMock()
    mock_channel = AsyncMock()
    mock_conn.channel = AsyncMock(return_value=mock_channel)

    mock_app_state = MagicMock()
    mock_env = {"MAX_QUICK_SCAN_WORKERS": "7"}

    with patch(
        "core.queue.consumer.get_connection", return_value=mock_conn
    ), patch.dict(os.environ, mock_env):
        await _start_single_consumer(QUICK_SCAN, mock_app_state)
        mock_channel.set_qos.assert_called_once_with(prefetch_count=7)
