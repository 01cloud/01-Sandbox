import datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from auth.token_validator import get_active_developer_keys


@pytest.mark.asyncio
async def test_get_active_developer_keys_cached():
    mock_state = MagicMock()
    mock_state.use_redis = True
    mock_state.redis_client = MagicMock()

    # Simulate Redis hit
    mock_state.redis_client.get.return_value = json.dumps(["key-1", "key-2"])

    keys = await get_active_developer_keys(mock_state, "user-123")
    assert keys == ["key-1", "key-2"]
    mock_state.redis_client.get.assert_called_once_with("user_keys:user-123")
    mock_state.get_db_conn.assert_not_called()


@pytest.mark.asyncio
async def test_get_active_developer_keys_db_fallback():
    mock_state = MagicMock()
    mock_state.use_redis = True
    mock_state.redis_client = MagicMock()
    mock_state.redis_client.get.return_value = None
    mock_state.use_postgres = True

    # Mock DB Connection
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    tomorrow = (
        datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1)
    ).isoformat()
    mock_cursor.fetchall.return_value = [("key-db-1", tomorrow)]
    mock_conn.cursor.return_value = mock_cursor
    mock_state.get_db_conn.return_value = mock_conn

    keys = await get_active_developer_keys(mock_state, "user-123")
    assert keys == ["key-db-1"]

    mock_state.redis_client.get.assert_called_once_with("user_keys:user-123")
    mock_state.get_db_conn.assert_called_once()
    mock_state.redis_client.set.assert_called_once_with(
        "user_keys:user-123", json.dumps(["key-db-1"]), ex=60
    )
