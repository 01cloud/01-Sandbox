# RabbitMQ Central Scan Job Queue — Technical Reference

**Branch:** `feat/rabbitmq`
**Author:** Antigravity (AI Pair Programmer)
**Scope:** `apiServer/fastapi/` + `codeInspector/charts/apiServer/`

---

## 1. Why This Was Implemented

The original implementation used FastAPI's `BackgroundTasks.add_task()` to run scan pipelines directly in the server process. This creates an unbounded concurrency problem:

| Scenario | Old Behaviour | New Behaviour |
|---|---|---|
| 10 simultaneous repo scans | 10 `_run_scan_pipeline()` coroutines run immediately, each cloning a Git repo and spinning a sandbox — server memory spikes | Max 3 pipelines run; 7 wait durably in the RabbitMQ queue |
| 20 quick scans at once | 20 concurrent file submissions to the scan-jobs endpoint | Max 5 run; 15 sit in `scan.quick` queue, processed as slots free |
| RabbitMQ unavailable / not configured | N/A | Automatic silent fallback to existing `BackgroundTasks` behaviour — zero downtime |

---

## 2. What Was Built

### New Module: `core/queue/`

A fully self-contained package. **No other existing module imports from it except the three integration points listed below.**

```
core/queue/
├── __init__.py       ← public API re-exports
├── job_types.py      ← central scan type registry (QUICK_SCAN, REPO_SCAN)
├── connection.py     ← RabbitMQ connection lifecycle (connect / close / is_available)
├── publisher.py      ← single publish() function used by all submission handlers
└── consumer.py       ← consumer factory: one bounded worker per scan type
```

---

## 3. File-by-File Technical Reference

### `core/queue/job_types.py`

**Purpose:** Single source of truth for all scan queue definitions.

```python
@dataclass(frozen=True)
class ScanJobType:
    job_type: str        # used for log prefixes and env var naming
    queue_name: str      # RabbitMQ durable queue name
    routing_key: str     # AMQP routing key bound to the exchange
    prefetch_count: int  # default max concurrent workers for this type

QUICK_SCAN = ScanJobType("quick-scan", "scan.quick", "scan.quick", 5)
REPO_SCAN  = ScanJobType("repo-scan",  "scan.repo",  "scan.repo",  3)

ALL_SCAN_JOB_TYPES = [QUICK_SCAN, REPO_SCAN]
```

**Key Design Decision:** `ALL_SCAN_JOB_TYPES` is the only list that `consumer.py` iterates over at startup. Adding any future scan type (e.g., `BULK_SCAN`) requires adding one entry here only — no changes to the consumer, connection, publisher, lifespan, or Helm chart.

---

### `core/queue/connection.py`

**Purpose:** Manages the single shared `aio_pika.RobustConnection` for the entire application process. Both the publisher and all consumers share this one connection through separate channels.

```python
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "")
```

- If `RABBITMQ_URL` is **not set** (empty string), `connect_rabbitmq()` returns `None` and prints a fallback warning. The server continues starting normally.
- If set, establishes a `connect_robust()` connection — this auto-reconnects on network drops without crashing the server.

**Public functions:**

| Function | Description |
|---|---|
| `connect_rabbitmq()` | Called once in `lifespan.py` on startup |
| `close_rabbitmq()` | Called once in `lifespan.py` on shutdown |
| `get_connection()` | Returns the current connection object |
| `is_available()` | Returns `True` only if connection is established and not closed |

---

### `core/queue/publisher.py`

**Purpose:** One function, `publish(routing_key, payload)`, used by every scan submission handler. Routers have zero knowledge of AMQP internals.

```python
async def publish(routing_key: str, payload: dict) -> None
```

- Opens a **new channel per publish call** (channels are lightweight; the connection is shared).
- Declares the exchange as `DIRECT` + `durable=True` (survives broker restarts).
- Sets `DeliveryMode.PERSISTENT` on every message (survives broker restarts).
- Raises `RuntimeError` if called when `is_available()` is `False` — this is caught by the `try/except` in each submission handler and triggers the fallback path.

---

### `core/queue/consumer.py`

**Purpose:** Consumer factory that binds one worker listener per `ScanJobType` at startup.

**Key mechanic — `prefetch_count`:**

```python
await channel.set_qos(prefetch_count=prefetch)
```

This is the single knob that prevents server overload. RabbitMQ will never deliver more than `prefetch_count` unacknowledged messages to a consumer. If all slots are busy, new messages stay in the queue until a scan completes and the message is acknowledged (`async with message.process()`).

**Environment variable override (per scan type):**

The consumer resolves the prefetch count at startup using:
```
MAX_{JOB_TYPE_UPPER}_WORKERS
e.g.:
  MAX_QUICK_SCAN_WORKERS=5    (default)
  MAX_REPO_SCAN_WORKERS=3     (default)
```

**Handler dispatch:**

| `job_type` | Calls | Function Location |
|---|---|---|
| `quick-scan` | `run_scan_in_background(job_id, req_dict)` | `sandboxes/router.py` — **unchanged** |
| `repo-scan` | `_run_scan_pipeline(job_id, repo_url, owner, repo, app_state)` | `scan_repository/scan_repository.py` — **unchanged** |

The underlying pipeline functions are called exactly as `BackgroundTasks` called them. No arguments changed.

**`start_all_consumers(app_state)`:**

Called once from `lifespan.py`. Uses `asyncio.gather()` to spin up all consumers concurrently. Adding a new `ScanJobType` to `ALL_SCAN_JOB_TYPES` automatically spins up its consumer here without any code change in this file.

---

## 4. Integration Points (Modified Files)

### `core/lifespan.py`

Three lines added to the startup block:

```python
conn = await connect_rabbitmq()
if conn:
    asyncio.create_task(start_all_consumers(state))
```

Wrapped in `try/except` so a misconfigured `RABBITMQ_URL` cannot crash the server at startup — it logs a warning and continues in fallback mode.

On shutdown (`after yield`):
```python
await close_rabbitmq()
```

---

### `sandboxes/router.py` — Quick Scan (`POST /v1/scan-jobs`)

Only the `create_scan_job` endpoint handler was modified. `run_scan_in_background()` is completely untouched.

**Logic after the QUEUED event is pushed:**

```python
from core.queue import is_available, publish, QUICK_SCAN

if is_available():
    await publish(
        QUICK_SCAN.routing_key,
        {"job_id": job_id, "req_dict": req.dict(exclude_none=True)},
    )
    return ScanJobResponse(job_id=job_id, status="PROCESSING")

# Fallback: existing BackgroundTasks behaviour
if is_async:
    background_tasks.add_task(run_scan_in_background, job_id, req.dict(exclude_none=True))
    return ScanJobResponse(job_id=job_id, status="PROCESSING")
else:
    # ... existing synchronous execution path — completely unchanged
```

**Fallback behaviour:**
- RabbitMQ active → published to `scan.quick` queue → consumer calls `run_scan_in_background()`
- RabbitMQ inactive → existing `?async=true` / sync path — identical to before this change

---

### `scan_repository/scan_repository.py` — Repo Scan (`POST /v1/repo-scan`)

Only the `submit_repo_scan` endpoint handler was modified. `_run_scan_pipeline()` is completely untouched.

**Logic after the QUEUED event is pushed:**

```python
from core.queue import is_available, publish, REPO_SCAN

if is_available():
    await publish(
        REPO_SCAN.routing_key,
        {"job_id": job_id, "repo_url": req.repo_url, "owner": owner, "repo": repo},
    )
else:
    background_tasks.add_task(
        _run_scan_pipeline,
        job_id, req.repo_url, owner, repo, app_state,
    )
```

**Fallback behaviour:**
- RabbitMQ active → published to `scan.repo` queue → consumer calls `_run_scan_pipeline()`
- RabbitMQ inactive → `BackgroundTasks` call — identical to before this change

---

### `requirements.txt`

```
aio-pika==9.4.3
```

`aio-pika` is the official async Python client for RabbitMQ over AMQP 0-9-1. It wraps `aiormq` (which wraps `asyncio` TCP streams directly). No other new dependencies.

---

## 5. Helm Chart Changes

### New file: `codeInspector/charts/apiServer/templates/rabbitmq.yaml`

Follows the exact same pattern as `redis.yaml`. Conditionally rendered via `{{- if .Values.rabbitmq.enabled }}`.

Creates two Kubernetes resources in `opensandbox-system` namespace:
- `Deployment/rabbitmq` — using `rabbitmq:3.13-management-alpine` image
- `Service/rabbitmq-service` — ClusterIP exposing port `5672` (AMQP) and `15672` (Management UI)

---

### Modified: `codeInspector/charts/apiServer/templates/deployment.yaml`

Three environment variables conditionally injected into the `sandbox-api` container:

```yaml
{{- if .Values.rabbitmq.enabled }}
- name: RABBITMQ_URL
  value: "amqp://{{ .Values.rabbitmq.user }}:{{ .Values.rabbitmq.password }}@{{ .Values.rabbitmq.host }}:5672/"
- name: MAX_QUICK_SCAN_WORKERS
  value: {{ .Values.rabbitmq.maxQuickScanWorkers | quote }}
- name: MAX_REPO_SCAN_WORKERS
  value: {{ .Values.rabbitmq.maxRepoScanWorkers | quote }}
{{- end }}
```

---

### Modified: `codeInspector/charts/apiServer/values.yaml`

Default values added (all features default to `enabled: false` for standalone chart usage):

```yaml
rabbitmq:
  enabled: false
  host: "rabbitmq-service"
  port: 5672
  user: "admin"
  password: "changeme"
  maxQuickScanWorkers: "5"
  maxRepoScanWorkers: "3"
```

---

### Modified: `codeInspector/values.yaml`

Production override added under the `apiServer:` block (set to `enabled: true` for the production umbrella chart):

```yaml
apiServer:
  ...
  rabbitmq:
    enabled: true
    host: "rabbitmq-service"
    port: 5672
    user: "admin"
    password: "changeme"
    maxQuickScanWorkers: "5"
    maxRepoScanWorkers: "3"
```

---

## 6. Full Data Flow (With RabbitMQ Active)

```
POST /v1/scan-jobs  OR  POST /v1/repo-scan
         │
         ├── Auth ✓ (validate_token — unchanged)
         ├── Rate limit ✓ (rate_limiter.py — unchanged)
         ├── job_tracker.create_job() (tracker.py — unchanged)
         ├── job_tracker.push_event("QUEUED") (tracker.py — unchanged)
         │
         ▼
  core.queue.publisher.publish(routing_key, payload)
         │
         ▼
  ┌──────────────────────────────────────────┐
  │           RabbitMQ Broker                │
  │  Exchange: "scan_jobs" (DIRECT, durable) │
  │                                          │
  │  scan.quick ──► prefetch=5 consumers     │
  │  scan.repo  ──► prefetch=3 consumers     │
  └────────────────────┬─────────────────────┘
                       │  (worker slot becomes free)
                       ▼
  consumer.on_message() — async with message.process()
         │
         ├── [quick-scan] run_scan_in_background(job_id, req_dict)
         │       → POST /scan-jobs → opensandbox-server
         │       → job_tracker.push_event() → Redis Pub/Sub
         │
         └── [repo-scan] _run_scan_pipeline(job_id, repo_url, owner, repo, app_state)
                 → clone repo → detect languages → scan per language
                 → job_tracker.push_event() → Redis Pub/Sub
                 → SSE stream to browser client
```

---

## 7. What Was NOT Changed

Every item in this list is guaranteed identical to before this implementation:

- `core/app_state.py` — Redis connection, API key registry, rate limit state
- `ratelimit/rate_limiter.py` — Fixed-window rate limiting
- `auth/token_validator.py` — JWT validation, Auth0 JWKS, Identity Bridge
- `api_keys/` — Key create, list, revoke endpoints
- `core/jobs/tracker.py` — Job creation, SSE event streaming, Redis Pub/Sub
- `core/jobs/router.py` — `/v1/jobs` listing and result endpoints
- `scan_repository/_run_scan_pipeline()` — The actual repo scan pipeline logic
- `sandboxes/router.py run_scan_in_background()` — The actual quick scan logic
- `scan_repository/file_scanner.py` — Per-language scanner dispatch
- `scan_repository/language_detector.py` — Language detection
- `scan_repository/sandbox_provisioner.py` — Local sandbox provisioning
- `proxy/router.py` — Transparent HTTP proxy
- `health/` — Health check endpoints
- `codeinspectior_api.py` — Main application entrypoint
- `config.py` — Configuration helpers
- `backends.py` — HTTP backend abstraction

---

## 8. Local Testing Guide

```bash
# 1. Start RabbitMQ locally
docker run -d \
  -p 5672:5672 \
  -p 15672:15672 \
  -e RABBITMQ_DEFAULT_USER=admin \
  -e RABBITMQ_DEFAULT_PASS=changeme \
  rabbitmq:3.13-management-alpine

# 2. Run the apiServer with RabbitMQ enabled
export RABBITMQ_URL=amqp://admin:changeme@localhost:5672/
export MAX_QUICK_SCAN_WORKERS=2
export MAX_REPO_SCAN_WORKERS=1
cd apiServer/fastapi
uvicorn codeinspectior_api:app --reload --port 8000

# 3. Expected startup logs
# [RabbitMQ] Connected: localhost:5672/
# [RabbitMQ] Consumer ready: queue=scan.quick prefetch=2
# [RabbitMQ] Consumer ready: queue=scan.repo prefetch=1
# [RabbitMQ] All 2 consumers active.

# 4. Verify queue depth at
# http://localhost:15672  (user: admin / changeme)
```

**Fallback test:**
```bash
# Unset RABBITMQ_URL — server must start and behave identically to before
unset RABBITMQ_URL
uvicorn codeinspectior_api:app --reload --port 8000

# Expected startup log:
# [RabbitMQ] RABBITMQ_URL not set — fallback mode active.
```

---

## 9. Adding a Future Scan Type

When a new scan endpoint is added (e.g., a Bulk Scan):

**Step 1 — `core/queue/job_types.py`** (only file that changes in the queue module):
```python
BULK_SCAN = ScanJobType("bulk-scan", "scan.bulk", "scan.bulk", 2)
ALL_SCAN_JOB_TYPES = [QUICK_SCAN, REPO_SCAN, BULK_SCAN]  # add here
```

**Step 2 — `core/queue/consumer.py`**: Add one `elif jt.job_type == "bulk-scan":` block pointing to the new pipeline function.

**Step 3 — New router**: Call `publish(BULK_SCAN.routing_key, payload)` in the submission handler.

**No changes needed** to: connection, publisher, lifespan, Helm chart, Redis, auth, rate limiter, or any other existing file.
