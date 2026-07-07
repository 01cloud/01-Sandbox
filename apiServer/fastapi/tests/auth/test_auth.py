"""
test_auth.py
============
Unit tests for auth/token_validator.py

This file tests JWT verification policies, path exclusions, and identity verification checks:
  1. Options & Public Route Bypass: CORS and documentation endpoints.
  2. Signature & Token Format Verification: Expired, missing, and malformed JWTs.
  3. Session Identity Lockdown: Preventing cross-account API key usage.
  4. Revocation Lists: Ensuring keys marked as revoked in Postgres/Redis are rejected.

HOW TO RUN (from /apiServer/fastapi):
--------------------------------------
  # Run this file only:
  PYTHONPATH=. pytest tests/auth/test_auth.py -v

  # Run with coverage report:
  PYTHONPATH=. pytest tests/auth/test_auth.py --cov=auth --cov-report=term-missing -v
"""

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

# Inject current directory into python path to load core modules correctly
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auth.token_validator import validate_token
from core.app_state import state

# ===========================================================================
# SETUP & HELPER FUNCTIONS
# ===========================================================================


@pytest.fixture(autouse=True)
def setup_db():
    """
    Fixture to initialize the SQLite database schema and clear the api_keys
    table before every test case to ensure test isolation.
    """
    # Initialize schema
    state.init_db()

    # Clean up api_keys for a fresh state
    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM api_keys")
    conn.commit()
    conn.close()


def make_mock_request(
    method="GET",
    path="/v1/api-keys",
    headers=None,
    cookies=None,
    query_params=None,
    client_host=None,
):
    """
    Helper function to construct a Starlette/FastAPI Request object.
    It builds the underlying ASGI scope mapping keys, headers, query params,
    and cookies, simulating a real incoming client request.
    """
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
    }

    # Process headers
    if headers:
        for k, v in headers.items():
            scope["headers"].append((k.encode("utf-8"), v.encode("utf-8")))

    # Process query parameters
    if query_params:
        from urllib.parse import urlencode

        scope["query_string"] = urlencode(query_params).encode("utf-8")
    else:
        scope["query_string"] = b""

    # Process cookies
    if cookies:
        cookie_parts = [f"{k}={v}" for k, v in cookies.items()]
        scope["headers"].append((b"cookie", "; ".join(cookie_parts).encode("utf-8")))

    # Process client info
    if client_host:
        scope["client"] = (client_host, 80)

    return Request(scope)


# ===========================================================================
# PHASE 1 — Request Bypass Rules (OPTIONS & Public Routes)
# ===========================================================================


@pytest.mark.asyncio
async def test_validate_token_options_request():
    """
    SCENARIO: An OPTIONS (preflight) request is received.
    EXPECTATION: Token validation is bypassed immediately and returns an empty dictionary.

    HOW: CORS preflight options do not contain credentials, so we return `{}` to
    allow browser security headers validation to continue without hitting the rate limiter.
    """
    # Arrange: Build an OPTIONS request targeting an execution path
    req = make_mock_request(method="OPTIONS", path="/v1/run")

    # Act: Call the token validation function
    res = await validate_token(req)

    # Assert: Verify that the function bypassed checks and returned empty dictionary
    assert res == {}


@pytest.mark.asyncio
async def test_validate_token_public_routes():
    """
    SCENARIO: Request points to a public API router (e.g. '/status').
    EXPECTATION: Token validation is bypassed immediately.

    HOW: Routes like `/status`, `/openapi.json`, and `/report` are public
    and do not require active JWT tokens.
    """
    # Arrange: Build a GET request for the public status endpoint
    req = make_mock_request(method="GET", path="/status")

    # Act: Call the token validation function
    res = await validate_token(req)

    # Assert: Verify that the check was bypassed
    assert res == {}


# ===========================================================================
# PHASE 2 — Signature & Format Validation (Missing, Expired, Malformed)
# ===========================================================================


@pytest.mark.asyncio
async def test_validate_token_missing_token():
    """
    SCENARIO: Client requests a restricted route with no credentials provided.
    EXPECTATION: Validation fails with 401 Unauthorized.

    HOW: We test execution routes (requiring explicit padlocked Authorization header)
    and management routes (allowing session cookie fallbacks). Both must return 401.
    """
    # Arrange: 1. Execution path request with no headers or cookies
    req_exec = make_mock_request(method="GET", path="/v1/run")

    # Act & Assert: Verify execution route raises custom padlock instruction
    with pytest.raises(HTTPException) as exc_info:
        await validate_token(req_exec)
    assert exc_info.value.status_code == 401
    assert "Execution required an explicit API Key" in exc_info.value.detail

    # Arrange: 2. Management path request with no credentials
    req_non_exec = make_mock_request(method="GET", path="/v1/api-keys")

    # Act & Assert: Verify management route raises generic credentials missing
    with pytest.raises(HTTPException) as exc_info:
        await validate_token(req_non_exec)
    assert exc_info.value.status_code == 401
    assert "Authentication required" in exc_info.value.detail


@pytest.mark.asyncio
@patch("jwt.decode")
@patch("jwt.get_unverified_header")
async def test_validate_token_expired_signature(mock_get_header, mock_decode):
    """
    SCENARIO: Request contains an expired JWT token.
    EXPECTATION: Validation fails with 401 Unauthorized and details "Token has expired".

    HOW: We patch jwt.decode to raise ExpiredSignatureError to isolate signature expiration.
    """
    # Arrange: Mock get_unverified_header and setup jwt.decode to raise ExpiredSignatureError
    req = make_mock_request(
        method="GET", path="/v1/run", headers={"authorization": "Bearer expired-token"}
    )
    mock_get_header.return_value = {"kid": "code-inspector-key-01"}
    mock_decode.side_effect = jwt.ExpiredSignatureError("Signature has expired")

    # Act & Assert: Verify 401 is raised with appropriate detail message
    with pytest.raises(HTTPException) as exc_info:
        await validate_token(req)
    assert exc_info.value.status_code == 401
    assert "Token has expired" in exc_info.value.detail


@pytest.mark.asyncio
@patch("jwt.decode")
@patch("jwt.get_unverified_header")
async def test_validate_token_invalid_format(mock_get_header, mock_decode):
    """
    SCENARIO: Token is syntactically invalid or has missing cryptographic sections.
    EXPECTATION: Raises 401 Unauthorized with format warnings.

    HOW: We patch jwt.decode to raise InvalidTokenError.
    """
    # Arrange: Set up mock header response and mock jwt.decode to raise InvalidTokenError
    req = make_mock_request(
        method="GET",
        path="/v1/run",
        headers={"authorization": "Bearer malformed-token"},
    )
    mock_get_header.return_value = {"kid": "code-inspector-key-01"}
    mock_decode.side_effect = jwt.InvalidTokenError("Invalid token")

    # Act & Assert: Verify 401 is raised with a details hint
    with pytest.raises(HTTPException) as exc_info:
        await validate_token(req)
    assert exc_info.value.status_code == 401
    assert "Invalid token format" in exc_info.value.detail


# ===========================================================================
# PHASE 3 — Identity Verification Policies (Lockdown & Revocations)
# ===========================================================================


@pytest.mark.asyncio
@patch("jwt.decode")
@patch("jwt.get_unverified_header")
async def test_validate_token_identity_mismatch(mock_get_header, mock_decode):
    """
    SCENARIO: A user tries to run a sandbox execution using another user's API Key.
    EXPECTATION: Raises 403 Forbidden with Identity Lockdown violation details.

    HOW: The request carries an Authorization header belonging to 'user_a' but has an
         'inspector_auth' session cookie belonging to 'user_b'. The middle verification
         detects this mismatch and blocks the cross-account request.
    """
    # Arrange: 1. Setup request headers and mismatched session cookie
    req = make_mock_request(
        method="GET",
        path="/v1/run",
        headers={"authorization": "Bearer user-a-token"},
        cookies={"inspector_auth": "user-b-token"},
    )

    mock_get_header.return_value = {"kid": "code-inspector-key-01"}
    # Mock jwt.decode sequence:
    #   Call 1 (unverified header parsing) -> user_a payload
    #   Call 2 (signature validation) -> user_a payload
    #   Call 3 (cookie validation) -> user_b payload
    mock_decode.side_effect = [
        {"sub": "user_a", "jti": "key_123"},
        {"sub": "user_a", "jti": "key_123"},
        {"sub": "user_b"},
    ]

    # Seed active key in SQLite so validation passes the database state verification stage
    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            "key_123",
            "Key 123",
            "Z1_SANDBOX",
            "user_a",
            "user_a@example.com",
            "2026-07-07T12:00:00Z",
            "2026-07-08T12:00:00Z",
            "ci_123",
        ),
    )
    conn.commit()
    conn.close()

    # Act & Assert: Call validation (mocking the background update task) and verify 403 Forbidden is raised
    with patch("auth.token_validator.update_last_used", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as exc_info:
            await validate_token(req)

    assert exc_info.value.status_code == 403
    assert "Identity Lockdown" in exc_info.value.detail


@pytest.mark.asyncio
@patch("jwt.decode")
@patch("jwt.get_unverified_header")
async def test_validate_token_revoked_key(mock_get_header, mock_decode):
    """
    SCENARIO: Request contains a cryptographically valid JWT, but its JTI is revoked in DB.
    EXPECTATION: Raises 401 Unauthorized with "API Key has been revoked".

    HOW: We mock jwt.decode to return a valid payload containing 'revoked_key_123',
         and seed a database entry with 'is_revoked = 1'.
    """
    # Arrange: Build request and setup mocks
    req = make_mock_request(
        method="GET", path="/v1/run", headers={"authorization": "Bearer revoked-token"}
    )
    mock_get_header.return_value = {"kid": "code-inspector-key-01"}

    payload = {"sub": "user_a", "jti": "revoked_key_123"}
    mock_decode.return_value = payload

    # Seed key as revoked (is_revoked = 1) in SQLite
    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix, is_revoked)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
        """,
        (
            "revoked_key_123",
            "Revoked Key",
            "Z1_SANDBOX",
            "user_a",
            "user_a@example.com",
            "2026-07-07T12:00:00Z",
            "2026-07-08T12:00:00Z",
            "ci_rev",
        ),
    )
    conn.commit()
    conn.close()

    # Act & Assert: Call validation and verify 401 Unauthorized
    with pytest.raises(HTTPException) as exc_info:
        await validate_token(req)

    assert exc_info.value.status_code == 401
    assert "API Key has been revoked" in exc_info.value.detail
