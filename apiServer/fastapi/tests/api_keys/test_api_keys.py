"""
test_api_keys.py
================
Unit tests for api_keys/router.py

This file tests two critical security requirements:
  1. API Key Sanitization: Stripping HTML/Script tags and special characters.
  2. Quota Management: Enforcing a hard limit of 5 active keys per user.

HOW TO RUN (from /apiServer/fastapi):
--------------------------------------
  # Run this file only:
  PYTHONPATH=. pytest tests/api_keys/test_api_keys.py -v

  # Run with coverage report:
  PYTHONPATH=. pytest tests/api_keys/test_api_keys.py --cov=api_keys --cov-report=term-missing -v
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

# Inject current directory into python path to load core modules correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.token_validator import validate_token
from core.app_state import state
from main import app

# ===========================================================================
# SETUP & FIXTURES
# ===========================================================================


@pytest.fixture(autouse=True)
def setup_db():
    """
    Fixture to initialize and migrate the SQLite database schema, and then
    clear the api_keys table before every single test case to ensure test isolation.
    """
    # Initialize schema
    state.init_db()

    # Clean up api_keys for a fresh state
    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM api_keys")
    conn.commit()
    conn.close()


@pytest.fixture
def mock_user_token():
    """
    Fixture to mock the `validate_token` dependency override in FastAPI.
    Injects a dummy user payload containing 'sub' and 'email' claims.
    """
    payload = {"sub": "auth0|user123", "email": "user@example.com"}

    async def override_validate_token():
        return payload

    app.dependency_overrides[validate_token] = override_validate_token
    yield payload
    app.dependency_overrides.pop(validate_token, None)


# ===========================================================================
# PHASE 1 — API Key Name Sanitization
# ===========================================================================


def test_create_api_key_sanitizes_html_tags(mock_user_token):
    """
    SCENARIO: User creates an API key with HTML tags embedded in the name.
    EXPECTATION: HTML tags (like <script> and <div>) are stripped out, but the inner text remains.

    HOW: We use bleach.clean() in the router. We make a POST request and query
    the database to ensure only 'alert1Prod-Key' was persisted.
    """
    # Arrange: Setup TestClient and payload containing HTML tags
    client = TestClient(app)
    payload = {
        "name": "<script>alert(1)</script>Prod-Key<div>",
        "backend": "Z1_SANDBOX",
        "ttl_hours": 1.0,
        "user_email": "user@example.com",
    }

    # Act: Perform the POST request to generate the API key
    response = client.post("/v1/api-keys", json=payload)

    # Assert: Verify 200 OK and check database record matches expected sanitization
    assert response.status_code == 200
    data = response.json()
    assert "api_key" in data

    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM api_keys WHERE id = %s", (data["api_key_id"],))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "alert1Prod-Key"


def test_create_api_key_sanitizes_special_characters(mock_user_token):
    """
    SCENARIO: User creates an API key with forbidden symbols in the name.
    EXPECTATION: Only alphanumeric characters, spaces, dashes, and underscores are kept.
                 Amperstands are converted to HTML entities (& -> &amp;) and then kept as text.

    HOW: We verify the regex substitution cleans "Test Key @#$ %^&*()_+!" into "Test Key  amp_".
    """
    # Arrange: Setup TestClient and payload containing special characters
    client = TestClient(app)
    payload = {
        "name": "Test Key @#$ %^&*()_+!",
        "backend": "Z1_SANDBOX",
        "ttl_hours": 1.0,
        "user_email": "user@example.com",
    }

    # Act: Perform the POST request
    response = client.post("/v1/api-keys", json=payload)

    # Assert: Verify 200 OK and check that the name was sanitized correctly in DB
    assert response.status_code == 200
    data = response.json()

    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM api_keys WHERE id = %s", (data["api_key_id"],))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "Test Key  amp_"


def test_create_api_key_empty_name_fallback(mock_user_token):
    """
    SCENARIO: User creates a key with a name containing ONLY tags and symbols (which sanitizes to empty).
    EXPECTATION: The router falls back to "Untitled Key" as the default name.

    HOW: We send a name like "<script></script>@#$" which gets completely stripped.
    """
    # Arrange: Setup TestClient and completely un-savable name
    client = TestClient(app)
    payload = {
        "name": "<script></script>@#$",
        "backend": "Z1_SANDBOX",
        "ttl_hours": 1.0,
        "user_email": "user@example.com",
    }

    # Act: Perform the POST request
    response = client.post("/v1/api-keys", json=payload)

    # Assert: Verify fallback default name in the database
    assert response.status_code == 200
    data = response.json()

    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM api_keys WHERE id = %s", (data["api_key_id"],))
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "Untitled Key"


# ===========================================================================
# PHASE 2 — API Key Quotas
# ===========================================================================


def test_create_api_key_quota_enforced(mock_user_token):
    """
    SCENARIO: User attempts to create a new API key when they already have 5 active keys.
    EXPECTATION: Creation fails with HTTP 403 Forbidden and the quota limit message.

    HOW: We seed 5 keys under the user's ID into the SQLite database, then try to POST.
    """
    # Arrange: Seed 5 keys in the SQLite database
    client = TestClient(app)
    conn = state.get_db_conn()
    cursor = conn.cursor()
    for i in range(5):
        cursor.execute(
            """
            INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"key-id-{i}",
                f"Key {i}",
                "Z1_SANDBOX",
                "auth0|user123",
                "user@example.com",
                "2026-07-07T12:00:00Z",
                "2026-07-08T12:00:00Z",
                f"ci_{i}",
            ),
        )
    conn.commit()
    conn.close()

    payload = {
        "name": "Excess Key",
        "backend": "Z1_SANDBOX",
        "ttl_hours": 1.0,
        "user_email": "user@example.com",
    }

    # Act: Request creation of 6th key
    response = client.post("/v1/api-keys", json=payload)

    # Assert: Verify 403 Forbidden is returned with quota limit message
    assert response.status_code == 403
    assert (
        response.json()["detail"]
        == "API Key limit reached (Max 5). Please delete an existing key to create a new one."
    )


def test_create_api_key_quota_under_limit(mock_user_token):
    """
    SCENARIO: User has 3 active keys and requests a new one (under the quota of 5).
    EXPECTATION: Request succeeds and new key is created, bringing total keys to 4.

    HOW: Seed 3 keys in DB, issue POST request, then assert count is 4.
    """
    # Arrange: Seed 3 keys in the database
    client = TestClient(app)
    conn = state.get_db_conn()
    cursor = conn.cursor()
    for i in range(3):
        cursor.execute(
            """
            INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                f"key-id-{i}",
                f"Key {i}",
                "Z1_SANDBOX",
                "auth0|user123",
                "user@example.com",
                "2026-07-07T12:00:00Z",
                "2026-07-08T12:00:00Z",
                f"ci_{i}",
            ),
        )
    conn.commit()
    conn.close()

    payload = {
        "name": "Valid Key",
        "backend": "Z1_SANDBOX",
        "ttl_hours": 1.0,
        "user_email": "user@example.com",
    }

    # Act: Create the 4th key
    response = client.post("/v1/api-keys", json=payload)

    # Assert: Verify success and database key count
    assert response.status_code == 200

    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM api_keys WHERE user_id = %s", ("auth0|user123",)
    )
    count = cursor.fetchone()[0]
    conn.close()
    assert count == 4
