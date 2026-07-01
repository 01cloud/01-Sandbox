from unittest.mock import MagicMock

import pytest
from health.router import check_postgresql_health


def test_check_postgresql_health_success():
    """Verify that when the database connection and query succeed, it returns healthy."""
    # Arrange: Build nested mock objects
    mock_state = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()

    # Link the mock objects:
    # 1. state.get_db_conn() returns our mock connection
    mock_state.get_db_conn.return_value = mock_conn
    # 2. conn.cursor() returns our mock cursor
    mock_conn.cursor.return_value = mock_cursor

    # Act: Call the function under test
    healthy, message = check_postgresql_health(mock_state)

    # Assert: Verify the output matches success conditions
    assert healthy is True
    assert message == "PostgreSQL Connected"

    # Assert: Verify the execution path
    mock_state.get_db_conn.assert_called_once()
    mock_conn.cursor.assert_called_once()
    mock_cursor.execute.assert_called_once_with("SELECT 1;")
    mock_cursor.fetchone.assert_called_once()
    mock_conn.close.assert_called_once()


def test_check_postgresql_health_connection_error():
    """Verify that if getting a DB connection raises an error, it returns unhealthy."""
    # Arrange: Create mock state where get_db_conn() raises an exception
    mock_state = MagicMock()
    mock_state.get_db_conn.side_effect = Exception("DB Connection Timeout")

    # Act: Call the function under test
    healthy, message = check_postgresql_health(mock_state)

    # Assert: Verify the output is unhealthy and reports the exception
    assert healthy is False
    assert "Database error: DB Connection Timeout" in message


def test_check_postgresql_health_query_error():
    """Verify that if the test query raises an exception, the function catches it and returns unhealthy."""
    # Arrange: Set up mock state/connection/cursor, but make query execution fail
    mock_state = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()

    mock_state.get_db_conn.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor
    mock_cursor.execute.side_effect = Exception("Connection refused on query")

    # Act: Call the function under test
    healthy, message = check_postgresql_health(mock_state)

    # Assert: Verify output status is unhealthy
    assert healthy is False
    assert "Database error: Connection refused on query" in message
    # Assert: The exception causes the function to skip conn.close()
    mock_conn.close.assert_not_called()
