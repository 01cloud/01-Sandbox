from unittest.mock import MagicMock

import pytest
from health.router import check_redis_health


def test_check_redis_health_disabled():
    """Verify that when Redis is disabled, it returns healthy and 'Disabled'."""
    # Arrange: Create a mock state with use_redis set to False
    mock_state = MagicMock()
    mock_state.use_redis = False

    # Act: Call check_redis_health with our mock state
    healthy, message = check_redis_health(mock_state)

    # Assert: Verify the output matches expectations
    assert healthy is True
    assert message == "Disabled"


def test_check_redis_health_success():
    """Verify that when Redis is enabled and responds to a ping, it returns healthy."""
    # Arrange: Create a mock state with use_redis set to True
    mock_state = MagicMock()
    mock_state.use_redis = True

    # Mock redis_client and configure the ping() method to return True
    mock_state.redis_client = MagicMock()
    mock_state.redis_client.ping.return_value = True

    # Act: Call check_redis_health
    healthy, message = check_redis_health(mock_state)

    # Assert: Verify success status and response message
    assert healthy is True
    assert message == "Redis Connected"

    # Assert: Verify that the ping() method was actually called
    mock_state.redis_client.ping.assert_called_once()


def test_check_redis_health_ping_failed():
    """Verify that when Redis ping returns False, it returns unhealthy."""
    # Arrange: Create a mock state with use_redis set to True, but ping returns False
    mock_state = MagicMock()
    mock_state.use_redis = True
    mock_state.redis_client = MagicMock()
    mock_state.redis_client.ping.return_value = False

    # Act: Call check_redis_health
    healthy, message = check_redis_health(mock_state)

    # Assert: Verify unhealthy status and correct warning message
    assert healthy is False
    assert message == "Redis client connection failed"


def test_check_redis_health_exception():
    """Verify that when Redis raises an exception on ping, it is handled and returns unhealthy."""
    # Arrange: Create a mock state where ping raises an exception
    mock_state = MagicMock()
    mock_state.use_redis = True
    mock_state.redis_client = MagicMock()
    mock_state.redis_client.ping.side_effect = Exception("Connection refused")

    # Act: Call check_redis_health
    healthy, message = check_redis_health(mock_state)

    # Assert: Verify unhealthy status and error description
    assert healthy is False
    assert "Redis error: Connection refused" in message
