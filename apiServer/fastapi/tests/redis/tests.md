# Redis Testing Components Guide

This document explains the technical components and mocking features used in `tests/redis/test_redis_health.py` to test the Redis health check logic.

---

## 1. `MagicMock` (from `unittest.mock`)
- **What it is**: `MagicMock` is a class from Python's standard library used to create fake objects.
- **How it works**: By default, a mock object will dynamically create any attribute or method you access on it, returning another mock object.
- **Why we used it**:
  - `mock_state = MagicMock()` behaves like our application's `state` object.
  - We can attach attributes directly like `mock_state.use_redis = True` without needing to instantiate the real, heavy `AppState` class.

---

## 2. Dynamic Attribute Stubbing
- **What it is**: Setting attributes on the mock object.
- **Why we used it**:
  - `mock_state.use_redis = False`
  - This allows us to force the health check code (`if not state.use_redis:`) down specific branches of execution.

---

## 3. `.return_value`
- **What it is**: Configures what the mock returns when it is called as a function/method.
- **Why we used it**:
  - `mock_state.redis_client.ping.return_value = True`
  - In `health/router.py`, the code calls `state.redis_client.ping()`. Since `redis_client` is a mock and `ping` is a mock, configuring the `return_value` ensures that calling `ping()` returns `True` instead of returning another mock object.

---

## 4. `.side_effect`
- **What it is**: Configures a mock to raise an exception or run a custom function when called, instead of returning a static value.
- **Why we used it**:
  - `mock_state.redis_client.ping.side_effect = Exception("Connection refused")`
  - This simulates a network failure when pinging Redis. It allows us to verify that our `try-except` block in `health/router.py` successfully catches the exception and returns the correct failure response message.

---

## 5. `.assert_called_once()`
- **What it is**: A verification method built into mock objects.
- **How it works**: It raises an `AssertionError` if the method was not called exactly once during the execution of the test.
- **Why we used it**:
  - `mock_state.redis_client.ping.assert_called_once()`
  - This guarantees that our code is actually pinging Redis, rather than hardcoding a success response or bypassing the check.

---

## 6. Pytest Assertions (`assert`)
- **What it is**: Standard Python `assert` statement.
- **How it works**: Pytest overrides standard Python assertions to provide highly detailed messages showing exactly what failed (e.g., showing the actual value vs. the expected value).
- **Why we used it**:
  - `assert healthy is True`
  - `assert message == "Redis Connected"`
  - Checks the output variables returned by the function.

---

## 7. Running Tests & Checking Coverage

To execute the tests and measure code coverage, make sure you are in the `/apiServer/fastapi` directory.

> [!NOTE]
> Since we added `pytest.ini` with `pythonpath = .`, you can use the `pytest` command directly without prefixing it with `python3 -m` or setting `PYTHONPATH`.

### Run Tests with Coverage Report in Console
```bash
pytest tests/redis/test_redis_health.py --cov=health -v
```
- **`pytest`**: Runs the pytest framework directly.
- **`tests/redis/test_redis_health.py`**: Runs only the test functions inside this file.
- **`--cov=health`**: Tracks and calculates code coverage specifically for the files in the `health/` directory.
- **`-v`**: Verbose mode (displays the exact test function name and its success status).

### Understanding the Coverage Report Output
When you run the command above, you will see a table like this:
```text
Name                 Stmts   Miss  Cover
----------------------------------------
health/__init__.py       1      0   100%
health/models.py        14      0   100%
health/router.py        72     56    22%
----------------------------------------
TOTAL                   87     56    36%
```
- **`health/router.py (22%)`**: This file contains multiple functions (PostgreSQL checks, OpenSandbox checks, and endpoint routers). Since our Redis tests only call the `check_redis_health` function, the other lines are marked as "Missed", resulting in a lower coverage percentage.
- Writing the PostgreSQL tests in the next step will cover more lines, raising this percentage!

### Run Coverage and Generate an HTML Report
```bash
pytest tests/redis/test_redis_health.py --cov=health --cov-report=html
```
- **`--cov-report=html`**: Generates an interactive visual folder called `htmlcov/`.
- Open **`htmlcov/index.html`** in a browser to see a line-by-line highlight of what code was executed (green) and what was missed (red).
