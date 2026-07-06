"""
test_rate_limit.py
==================

Unit tests for ratelimit/rate_limiter.py

This file is organised into 3 phases that match the implementation plan:

  Phase 1 → rate_limit_config()       (3 tests)  New concept: monkeypatch.setenv
  Phase 2 → is_key_rate_limited()     (6 tests)  New concept: monkeypatch.setattr (time)
  Phase 3 → check_rate_limit()        (5 tests)  New concept: pytest.mark.asyncio + pytest.raises

HOW TO RUN (from /apiServer/fastapi):
--------------------------------------
  # Run this file only:
  PYTHONPATH=. pytest tests/ratelimit/test_rate_limit.py -v

  # Run with coverage report:
  PYTHONPATH=. pytest tests/ratelimit/test_rate_limit.py --cov=ratelimit --cov-report=term-missing -v
"""

# STEP 2 — Import MagicMock so we can fake the Redis state object (Phase 2 & 3)
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# IMPORTS
# ---------------------------------------------------------------------------
# STEP 1 — Import pytest (always needed)
import pytest

# STEP 4 — Import HTTPException so we can check that the right exception is raised (Phase 3)
from fastapi import HTTPException

# STEP 3 — Import the three real functions we want to test from your application
from ratelimit.rate_limiter import (
    check_rate_limit,
    is_key_rate_limited,
    rate_limit_config,
)

# ===========================================================================
# PHASE 1 — Testing rate_limit_config()
# ===========================================================================
#
# WHAT THIS FUNCTION DOES (rate_limiter.py lines 8-23):
#   1. Reads RATE_LIMIT_REQUESTS from environment variables (default: 7)
#   2. Reads RATE_LIMIT_WINDOW_SECS from environment variables (default: 60)
#   3. Returns them as a dict: {"requests": ..., "window_secs": ...}
#
# NEW CONCEPT — monkeypatch:
#   pytest automatically gives every test function a special object called
#   `monkeypatch` if you add it as a parameter. You never import it — pytest
#   injects it automatically (this is called a "fixture").
#
#   monkeypatch.setenv("VAR", "value")  → sets an environment variable
#   monkeypatch.delenv("VAR")           → removes an environment variable
#
#   IMPORTANT: These changes are TEMPORARY. When the test function ends,
#   pytest automatically undoes everything monkeypatch changed. Each test
#   always starts with a clean slate.
# ===========================================================================


def test_default_values(monkeypatch):
    """
    SCENARIO: Neither RATE_LIMIT_REQUESTS nor RATE_LIMIT_WINDOW_SECS is set.
    EXPECTATION: The function returns the hardcoded defaults: requests=7, window_secs=60.

    HOW: We use monkeypatch.delenv() to make sure these variables do NOT exist
    in the environment when this test runs (in case they were set elsewhere).
    `raising=False` means: if the variable doesn't exist at all, don't raise an
    error — just do nothing.
    """
    # Arrange: Ensure neither env var is set
    monkeypatch.delenv("RATE_LIMIT_REQUESTS", raising=False)
    monkeypatch.delenv("RATE_LIMIT_WINDOW_SECS", raising=False)

    # Act: Call the real function
    result = rate_limit_config()

    # Assert: Verify defaults are returned
    assert result["requests"] == 7
    assert result["window_secs"] == 60


def test_custom_env_values(monkeypatch):
    """
    SCENARIO: Both env vars are set to valid integer strings.
    EXPECTATION: The function reads and returns those custom values.

    HOW: monkeypatch.setenv() temporarily sets an environment variable.
    Note: environment variables are always strings, so we pass "10", not 10.
    The function converts them with int(), which is exactly what we're testing.
    """
    # Arrange: Set both env vars to custom values
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "10")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECS", "30")

    # Act: Call the real function
    result = rate_limit_config()

    # Assert: Verify the custom values were picked up
    assert result["requests"] == 10
    assert result["window_secs"] == 30


def test_invalid_env_fallback(monkeypatch):
    """
    SCENARIO: RATE_LIMIT_REQUESTS is set to a non-integer string ("abc").
    EXPECTATION: int("abc") raises ValueError, so the function falls back to 7.
    RATE_LIMIT_WINDOW_SECS is not set, so it falls back to 60.

    WHY THIS MATTERS: This tests the `except ValueError` branch in the function,
    ensuring the app doesn't crash if someone misconfigures the environment.
    """
    # Arrange: Set requests to an invalid (non-numeric) string
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "abc")
    monkeypatch.delenv("RATE_LIMIT_WINDOW_SECS", raising=False)

    # Act: Call the real function
    result = rate_limit_config()

    # Assert: requests fell back to 7, window_secs fell back to 60
    assert result["requests"] == 7
    assert result["window_secs"] == 60


# ===========================================================================
# PHASE 2 — Testing is_key_rate_limited()
# ===========================================================================
#
# WHAT THIS FUNCTION DOES (rate_limiter.py lines 131-156):
#   1. Returns False immediately if jti (Jason Web Token ID) is empty
#   2. If Redis is enabled → reads the current count from Redis and compares
#      to the limit
#   3. If Redis is disabled → reads from state.local_rate_limits (a dict) and
#      checks if the window has expired
#
# REVISION — MagicMock (you already know this from health tests):
#   MagicMock() creates a fake object that behaves like any real object.
#   We use it to fake the `state` object so we don't need real Redis/DB.
#
# NEW CONCEPT — monkeypatch.setattr to freeze time.time():
#   is_key_rate_limited() calls time.time() to check if a window has expired.
#   In tests, we don't want tests to depend on the real clock. So we REPLACE
#   time.time with a lambda that always returns a fixed number.
#
#   monkeypatch.setattr("ratelimit.rate_limiter.time.time", lambda: 1000.0)
#
#   After this line, any call to time.time() inside rate_limiter.py will
#   return 1000.0 instead of the real current timestamp.
# ===========================================================================


def test_empty_jti_returns_false():
    """
    SCENARIO: jti(Jason Web Token ID) is an empty string.
    EXPECTATION: The function returns False immediately (first line guard check).

    NOTE: No monkeypatch or MagicMock needed here — the function exits before
    it ever touches state or time. We just pass a simple MagicMock as state.
    """
    # Arrange
    mock_state = MagicMock()

    # Act
    result = is_key_rate_limited(mock_state, "")

    # Assert
    assert result is False


def test_local_no_history_returns_false():
    """
    SCENARIO: Redis is disabled. The state object has no local_rate_limits attribute.
    EXPECTATION: The function returns False (no history = not rate limited).

    HOW: We use a plain MagicMock() as state. MagicMock() does NOT have a real
    `local_rate_limits` attribute, so `hasattr(state, "local_rate_limits")`
    returns... actually MagicMock auto-creates attributes! So we use
    spec=object to prevent this, OR we explicitly set use_redis = False and
    ensure local_rate_limits is absent by using a real plain object.

    Simpler approach: use MagicMock but configure use_redis=False, and do NOT
    set local_rate_limits, so the `hasattr` check fails.
    """
    # Arrange: State with Redis disabled and no local_rate_limits
    mock_state = MagicMock(spec=[])  # spec=[] means: this mock has NO attributes
    mock_state.use_redis = False

    # Act
    result = is_key_rate_limited(mock_state, "test-jti-abc")

    # Assert
    assert result is False


def test_local_under_limit_returns_false(monkeypatch):
    """
    SCENARIO: Redis is disabled. Local window shows count=3, limit=7, window is active.
    EXPECTATION: 3 < 7, so the key is NOT rate limited → returns False.

    HOW TO FREEZE TIME:
      We set a fixed "current time" of 1000.0.
      The window's expires_at is 1060.0 (60 seconds in the future).
      So time.time() (1000.0) <= expires_at (1060.0) → window is still active.
    """
    # Arrange: Freeze time at 1000.0
    monkeypatch.setattr("ratelimit.rate_limiter.time.time", lambda: 1000.0)

    # Arrange: Build a fake state with local rate limit data
    mock_state = MagicMock()
    mock_state.use_redis = False
    mock_state.local_rate_limits = {
        "test-jti-abc": {
            "count": 3,  # 3 requests so far
            "expires_at": 1060.0,  # window expires at t=1060 (future, since now=1000)
        }
    }

    # Act
    result = is_key_rate_limited(mock_state, "test-jti-abc")

    # Assert: 3 < 7, so not limited
    assert result is False


def test_local_at_limit_returns_true(monkeypatch):
    """
    SCENARIO: Redis is disabled. Local window shows count=7, limit=7, window is active.
    EXPECTATION: 7 >= 7, so the key IS rate limited → returns True.
    """
    # Arrange: Freeze time at 1000.0
    monkeypatch.setattr("ratelimit.rate_limiter.time.time", lambda: 1000.0)

    # Arrange: Build state where count == the limit (7)
    mock_state = MagicMock()
    mock_state.use_redis = False
    mock_state.local_rate_limits = {
        "test-jti-abc": {
            "count": 7,  # Exactly at the limit
            "expires_at": 1060.0,  # Window is still active
        }
    }

    # Act
    result = is_key_rate_limited(mock_state, "test-jti-abc")

    # Assert: 7 >= 7, so it IS limited
    assert result is True


def test_local_window_expired_returns_false(monkeypatch):
    """
    SCENARIO: Redis is disabled. Count=10 (way over limit), but the window has EXPIRED.
    EXPECTATION: Because the window is expired, the function treats it as no data → False.

    HOW:
      Now time is 1000.0.
      The window's expires_at is 900.0 (already in the PAST).
      So time.time() (1000.0) > expires_at (900.0) → window has expired.
      The condition `time.time() <= window_data["expires_at"]` is False,
      so the function skips the check and falls through to `return False`.
    """
    # Arrange: Freeze time at 1000.0
    monkeypatch.setattr("ratelimit.rate_limiter.time.time", lambda: 1000.0)

    # Arrange: Build state where window has already expired
    mock_state = MagicMock()
    mock_state.use_redis = False
    mock_state.local_rate_limits = {
        "test-jti-abc": {
            "count": 10,  # Way over the limit...
            "expires_at": 900.0,  # ...but the window already expired (900 < 1000)
        }
    }

    # Act
    result = is_key_rate_limited(mock_state, "test-jti-abc")

    # Assert: Window expired, so not considered rate limited
    assert result is False


def test_redis_at_limit_returns_true(monkeypatch):
    """
    SCENARIO: Redis IS enabled. Redis reports the current count is 7 (at the limit).
    EXPECTATION: 7 >= 7 → returns True.

    HOW:
      We set use_redis=True on mock_state.
      state.redis_client.get(rl_key) is the Redis call.
      We configure its return_value to b"7" (Redis returns bytes).
      The function does int(current_count_raw) >= requests_limit → 7 >= 7 → True.

    NOTE: We also need to monkeypatch RATE_LIMIT_REQUESTS so the limit is
    predictably 7 regardless of any env var that might be set on your machine.
    """
    # Arrange: Fix the env var so limit is definitely 7
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "7")

    # Arrange: Build a mock state with Redis enabled
    mock_state = MagicMock()
    mock_state.use_redis = True
    # Configure redis_client.get() to return b"7" (Redis returns bytes)
    mock_state.redis_client.get.return_value = b"7"

    # Act
    result = is_key_rate_limited(mock_state, "test-jti-abc")

    # Assert
    assert result is True


# ===========================================================================
# PHASE 3 — Testing check_rate_limit()
# ===========================================================================
#
# WHAT THIS FUNCTION DOES (rate_limiter.py lines 26-128):
#   1. Returns immediately if jti is empty (no error)
#   2. If Redis enabled: uses a pipeline to atomically increment a counter,
#      raises HTTPException(429) if over limit, or fails silently on Redis error
#   3. If Redis disabled: uses local_rate_limits dict, increments counter,
#      raises HTTPException(429) if over limit
#
# NEW CONCEPT — async tests with pytest.mark.asyncio:
#   check_rate_limit is defined with `async def`, so calling it returns a
#   coroutine that must be awaited. Regular test functions can't use `await`.
#
#   Solution: mark your test as async and use @pytest.mark.asyncio:
#
#     @pytest.mark.asyncio
#     async def test_something():
#         await check_rate_limit(mock_state, "jti")
#
#   SETUP REQUIRED (one-time):
#     pip install pytest-asyncio
#     Add `asyncio_mode = auto` to your pytest.ini
#
# NEW CONCEPT — pytest.raises:
#   When a function is expected to RAISE an exception, use pytest.raises():
#
#     with pytest.raises(HTTPException) as exc_info:
#         await check_rate_limit(mock_state, "jti")
#
#     assert exc_info.value.status_code == 429
#
#   If the exception is NOT raised, the test FAILS. This is exactly what you
#   want — you're asserting "this code MUST raise an error here".
# ===========================================================================


@pytest.mark.asyncio
async def test_check_rate_limit_empty_jti():
    """
    SCENARIO: jti is an empty string.
    EXPECTATION: Function returns None immediately. No exception raised.

    HOW: We simply await the call and confirm nothing is raised.
    No special arrangement needed since the function exits on the first line.
    """
    # Arrange
    mock_state = MagicMock()

    # Act & Assert: No exception should be raised
    result = await check_rate_limit(mock_state, "")

    # The function returns None (implicitly) when jti is empty
    assert result is None


@pytest.mark.asyncio
async def test_check_rate_limit_local_under_limit(monkeypatch):
    """
    SCENARIO: Redis disabled. count=3, limit=7, window is active.
    EXPECTATION: Under limit → no exception raised, function completes normally.

    NOTE: check_rate_limit MODIFIES the window_data (it increments the count).
    So even if we start at count=3, after the call count becomes 4. The key
    thing is: 4 is still below 7, so no exception.
    """
    # Arrange: Freeze time so the window is active
    monkeypatch.setattr("ratelimit.rate_limiter.time.time", lambda: 1000.0)
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "7")

    mock_state = MagicMock()
    mock_state.use_redis = False
    mock_state.local_rate_limits = {
        "test-jti-abc": {
            "count": 3,
            "expires_at": 1060.0,
        }
    }

    # Act & Assert: No exception should be raised
    await check_rate_limit(mock_state, "test-jti-abc")

    # Verify the count was incremented (3 → 4)
    assert mock_state.local_rate_limits["test-jti-abc"]["count"] == 4


@pytest.mark.asyncio
async def test_check_rate_limit_local_over_limit_raises_429(monkeypatch):
    """
    SCENARIO: Redis disabled. count=7, limit=7, window is active.
    The function increments count to 8, which is > 7, so it raises HTTPException(429).
    EXPECTATION: HTTPException with status_code=429 is raised.

    HOW: We use `with pytest.raises(HTTPException) as exc_info:` to "catch"
    the expected exception and then inspect it.
    """
    # Arrange: Freeze time so the window is active
    monkeypatch.setattr("ratelimit.rate_limiter.time.time", lambda: 1000.0)
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "7")

    mock_state = MagicMock()
    mock_state.use_redis = False
    mock_state.local_rate_limits = {
        "test-jti-abc": {
            "count": 7,  # At the limit; one more call → over limit
            "expires_at": 1060.0,
        }
    }

    # Act & Assert: Expect an HTTPException to be raised
    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(mock_state, "test-jti-abc")

    # Inspect the exception that was raised
    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["error"] == "Rate limit exceeded"
    assert exc_info.value.detail["jti"] == "test-jti-abc"


@pytest.mark.asyncio
async def test_check_rate_limit_redis_failure_does_not_crash():
    """
    SCENARIO: Redis IS enabled, but pipeline.execute() raises a generic Exception
    (simulating a Redis connection blip).
    EXPECTATION: The function catches the error silently and returns None —
    it does NOT crash the application. This is the "fail-open" safety net.

    HOW: We use side_effect to make pipeline().execute() raise an Exception.
    """
    # Arrange: Set up a mock state with Redis enabled
    mock_state = MagicMock()
    mock_state.use_redis = True

    # Make the pipeline's execute() raise a connection error
    mock_pipeline = MagicMock()
    mock_pipeline.execute.side_effect = Exception("Redis connection lost")
    mock_state.redis_client.pipeline.return_value = mock_pipeline

    # Act & Assert: No exception should propagate out of the function
    result = await check_rate_limit(mock_state, "test-jti-abc")

    # Function returns None silently (fail-open behaviour)
    assert result is None


@pytest.mark.asyncio
async def test_check_rate_limit_redis_over_limit_raises_429(monkeypatch):
    """
    SCENARIO: Redis IS enabled. Pipeline returns count=8, limit=7, ttl=45.
    8 > 7, so the function raises HTTPException(429).
    EXPECTATION: HTTPException with status_code=429 is raised.

    HOW:
      pipeline.execute() returns [count, ttl] as a list.
      We configure it to return [8, 45] — count=8, ttl=45 seconds remaining.
    """
    # Arrange: Fix the limit to 7
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "7")

    mock_state = MagicMock()
    mock_state.use_redis = True

    # Configure pipeline: incr returns 8, ttl returns 45
    mock_pipeline = MagicMock()
    mock_pipeline.execute.return_value = [8, 45]  # [current_count, ttl]
    mock_state.redis_client.pipeline.return_value = mock_pipeline

    # Act & Assert: Expect an HTTPException(429)
    with pytest.raises(HTTPException) as exc_info:
        await check_rate_limit(mock_state, "test-jti-abc")

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail["current_requests"] == 8
    assert exc_info.value.detail["retry_after"] == 45
