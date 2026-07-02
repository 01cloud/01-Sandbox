# Pytest Tutorial - Health Check Testing Plan

This document outlines the testing strategy for the health check functions in `health/router.py`. We will create unit tests inside the `tests/redis/` and `tests/postgresql/` folders to demonstrate standard testing and mocking patterns in pytest.

---

## 1. Redis Health Test

### File Location
`apiServer/fastapi/tests/redis/test_redis_health.py`

### Target Function
`check_redis_health(state)` in `health/router.py`

### Test Cases
1. **test_check_redis_health_disabled**:
   - **Scenario**: Redis is disabled (`state.use_redis = False`).
   - **Expectation**: Returns `(True, "Disabled")`.
2. **test_check_redis_health_success**:
   - **Scenario**: Redis is enabled and ping succeeds (`state.redis_client.ping() -> True`).
   - **Expectation**: Returns `(True, "Redis Connected")`.
3. **test_check_redis_health_ping_failed**:
   - **Scenario**: Redis is enabled but ping fails (`state.redis_client.ping() -> False` or is missing).
   - **Expectation**: Returns `(False, "Redis client connection failed")`.
4. **test_check_redis_health_exception**:
   - **Scenario**: Redis ping raises a connection exception.
   - **Expectation**: Returns `(False, "Redis error: <error details>")`.

---

## 2. PostgreSQL Health Test

### File Location
`apiServer/fastapi/tests/postgresql/test_postgresql_health.py`

### Target Function
`check_postgresql_health(state)` in `health/router.py`

### Test Cases
1. **test_check_postgresql_health_success**:
   - **Scenario**: Database connection opens, query executes `SELECT 1;`, fetch succeeds, and connection closes cleanly.
   - **Expectation**: Returns `(True, "PostgreSQL Connected")`.
2. **test_check_postgresql_health_failure**:
   - **Scenario**: Database connection or execution raises an Exception.
   - **Expectation**: Returns `(False, "Database error: <error details>")`.

---

## 3. How to Run the Tests

To run these tests, ensure you are in the `/apiServer/fastapi` directory and set the `PYTHONPATH` variable so Python can find your module code:

### Run Redis Tests Only
```bash
PYTHONPATH=. pytest tests/redis/test_redis_health.py -v
```

### Run PostgreSQL Tests Only
```bash
PYTHONPATH=. pytest tests/postgresql/test_postgresql_health.py -v
```

### Run Both Directories Together
```bash
PYTHONPATH=. pytest tests/redis/ tests/postgresql/ -v
```
