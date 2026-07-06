# Ratelimit Unit Test — Implementation Plan

**Target file:** `ratelimit/rate_limiter.py`
**Test directory:** `tests/ratelimit/`
**Functions to test (in order):**

```
Phase 1 → rate_limit_config()        ← New concept: monkeypatch.setenv
Phase 2 → is_key_rate_limited()      ← Revision: MagicMock + new: monkeypatch.setattr (time)
Phase 3 → check_rate_limit()         ← New concept: pytest.mark.asyncio (async functions)
```

---

## Phase 1 — `rate_limit_config()`

### Why start here
This is the simplest function in the whole module. It reads two environment variables and returns a dict. No mocks, no async, no state — just environment variables.

### New concept: `monkeypatch`
`monkeypatch` is a **built-in pytest fixture**. You don't install anything — just add it as a parameter to your test function and pytest injects it automatically. It lets you temporarily change environment variables, attributes, or even functions for the duration of a single test, then automatically undoes the change when the test finishes.

```python
def test_example(monkeypatch):          # <-- pytest injects monkeypatch for free
    monkeypatch.setenv("MY_VAR", "42")  # Set env var for this test only
    # ... test runs ...
    # After test completes, MY_VAR is automatically removed/restored
```

### Test file location
```
tests/ratelimit/test_rate_limit_config.py
```

### Test cases (3 tests)

| # | Test name | What you do | Expected result |
|---|---|---|---|
| 1 | `test_default_values` | Don't set any env vars | `{"requests": 7, "window_secs": 60}` |
| 2 | `test_custom_env_values` | `monkeypatch.setenv("RATE_LIMIT_REQUESTS", "10")` and `setenv("RATE_LIMIT_WINDOW_SECS", "30")` | `{"requests": 10, "window_secs": 30}` |
| 3 | `test_invalid_env_fallback` | `monkeypatch.setenv("RATE_LIMIT_REQUESTS", "abc")` | Falls back to default `{"requests": 7, "window_secs": 60}` |

### How to run
```bash
# From: /home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi
PYTHONPATH=. pytest tests/ratelimit/test_rate_limit_config.py -v
```

---

## Phase 2 — `is_key_rate_limited()`

### Why this second
This is a synchronous function (no `async`) with two code paths: Redis mode and local in-memory mode. You already know `MagicMock` from the health tests — here you'll use it again for the Redis path.

### New concept: `monkeypatch.setattr` on `time.time`
`is_key_rate_limited()` calls `time.time()` to check if a window has expired. To test time-sensitive logic, you need to **freeze** or **control** time. You do that by replacing `time.time` with a function that returns a value you choose:

```python
# In the test, freeze time at a specific timestamp
monkeypatch.setattr("ratelimit.rate_limiter.time.time", lambda: 1000.0)
```

### Test file location
```
tests/ratelimit/test_is_key_rate_limited.py
```

### Test cases (6 tests)

| # | Test name | Scenario | Expected |
|---|---|---|---|
| 1 | `test_empty_jti_returns_false` | `jti = ""` | `False` immediately |
| 2 | `test_local_no_history_returns_false` | No `local_rate_limits` attr on state | `False` |
| 3 | `test_local_under_limit_returns_false` | count = 3, limit = 7, window active | `False` |
| 4 | `test_local_at_limit_returns_true` | count = 7, limit = 7, window active | `True` |
| 5 | `test_local_window_expired_returns_false` | count = 10, but `expires_at` is in the past | `False` |
| 6 | `test_redis_at_limit_returns_true` | `use_redis=True`, `redis_client.get()` returns `"7"`, limit = 7 | `True` |

### How to run
```bash
PYTHONPATH=. pytest tests/ratelimit/test_is_key_rate_limited.py -v
```

---

## Phase 3 — `check_rate_limit()`

### Why this last
`check_rate_limit()` is an `async` function — it must be `await`-ed. That requires a new pytest plugin: `pytest-asyncio`. This adds one extra setup step but the test structure is very similar to what you already know.

### New concept: `pytest.mark.asyncio`
You mark your test function as `async` and add a decorator so pytest knows to run it in an async event loop:

```python
import pytest

@pytest.mark.asyncio
async def test_something():
    await check_rate_limit(mock_state, "some-jti")
```

### Setup step (one-time)
```bash
# Install the plugin
pip install pytest-asyncio

# Add this to your pytest.ini
asyncio_mode = auto
```

### New concept: `pytest.raises`
Since `check_rate_limit` raises `HTTPException` when the limit is exceeded, you need a way to assert that an exception was raised. `pytest.raises` is the tool:

```python
import pytest
from fastapi import HTTPException

with pytest.raises(HTTPException) as exc_info:
    await check_rate_limit(mock_state, "some-jti")

assert exc_info.value.status_code == 429
```

### Test file location
```
tests/ratelimit/test_check_rate_limit.py
```

### Test cases (5 tests)

| # | Test name | Scenario | Expected |
|---|---|---|---|
| 1 | `test_empty_jti_returns_none` | `jti = ""` | Returns immediately, no exception |
| 2 | `test_local_under_limit_passes` | count is 3, limit is 7 | No exception raised |
| 3 | `test_local_over_limit_raises_429` | count exceeds limit | `HTTPException` with `status_code=429` |
| 4 | `test_redis_failure_does_not_crash` | `use_redis=True`, `pipeline.execute()` raises a generic `Exception` | No crash, function returns silently |
| 5 | `test_redis_over_limit_raises_429` | `use_redis=True`, pipeline returns count > limit | `HTTPException` with `status_code=429` |

### How to run
```bash
PYTHONPATH=. pytest tests/ratelimit/test_check_rate_limit.py -v
```

---

## Full test directory structure (after all 3 phases)

```
tests/
├── conftest.py
├── testPlan.md
├── redis/
│   └── test_redis_health.py         ✅ Done
├── postgresql/
│   └── test_postgresql_health.py    ✅ Done
└── ratelimit/                       ← New folder
    ├── test_rate_limit_config.py    ← Phase 1
    ├── test_is_key_rate_limited.py  ← Phase 2
    └── test_check_rate_limit.py     ← Phase 3
```

---

## Run all ratelimit tests together (with coverage)
```bash
PYTHONPATH=. pytest tests/ratelimit/ --cov=ratelimit --cov-report=term-missing -v
```

---

## Concepts summary

| Phase | New concept | What it does |
|---|---|---|
| 1 | `monkeypatch.setenv` | Temporarily sets an env variable for one test |
| 2 | `monkeypatch.setattr` on `time.time` | Freezes time to a fixed value |
| 2 | `MagicMock` (revision) | Fakes the Redis client |
| 3 | `@pytest.mark.asyncio` | Allows `await` inside a test function |
| 3 | `pytest.raises` | Asserts that an exception was raised |
