# Pipeline Framework — Component Reference for Teams

> This document explains each component in the modular async scan pipeline system.
> Use this as a reference when onboarding teammates or reviewing the design before implementation.

---

## Overview: Why We Modularized

Previously, every scan feature (Quick Scan, Repo Scanner, Bulk Scan) had its own disconnected state management, its own ad-hoc SSE wiring, and no persistence across page refreshes. This caused:

- ❌ State lost on browser refresh
- ❌ One scan blocking or replacing another
- ❌ Duplicated logic between Quick Scan and Repo Scanner
- ❌ No way to track completed scans historically

The modular pipeline framework solves all of this by **separating concerns** into small, focused, reusable components that any scan type can plug into.

---

## Component Map

```text
                        ┌──────────────────────────────────────┐
                        │          Browser (Frontend)           │
                        │                                       │
                        │   ┌─────────────────────────────┐    │
                        │   │      useJobStore (hook)      │    │
                        │   │  - Owns EventSource streams  │    │
                        │   │  - Drives localStorage sync  │    │
                        │   └────────────┬────────────────┘    │
                        │                │                      │
                        │      ┌─────────┴──────────┐          │
                        │      ▼                    ▼          │
                        │  JobsPanel          UnifiedPipelineView│
                        │  (left sidebar)     (right detail pane)│
                        │      │                    │           │
                        │      └──────── jobStore ──┘           │
                        │              (localStorage)           │
                        └──────────────┬───────────────────────┘
                                       │  SSE / REST
                        ┌──────────────▼───────────────────────┐
                        │          FastAPI Backend              │
                        │                                       │
                        │  ┌─────────────────────────────┐     │
                        │  │     ReusableJobTracker       │     │
                        │  │  - In-memory GenericJobRecord│     │
                        │  │  - push_event() → Redis      │     │
                        │  │  - stream() → EventSource    │     │
                        │  └──────────────────────────────┘    │
                        │                                       │
                        │  ┌─────────────────────────────┐     │
                        │  │    Generic Job Router        │     │
                        │  │  GET /v1/jobs                │     │
                        │  │  GET /v1/jobs/{id}/status    │     │
                        │  │  GET /v1/jobs/{id}/events    │     │
                        │  │  GET /v1/jobs/{id}/result    │     │
                        │  └──────────────────────────────┘    │
                        └──────────────────────────────────────┘
```

---

## Backend Components

### 1. `GenericJobRecord`
**File**: `core/jobs/tracker.py`

**What it is**: An in-memory data structure representing a single active scan job of any type.

**Key fields**:
| Field | Type | Purpose |
|---|---|---|
| `job_id` | `str` | UUID that uniquely identifies this job |
| `job_type` | `str` | `"repo-scan"` or `"quick-scan"` — used to filter in list views |
| `metadata` | `dict` | Arbitrary data specific to the job type (e.g. `repo_url`) |
| `queue` | `asyncio.Queue` | Live event delivery channel for connected SSE clients |
| `event_log` | `deque(maxlen=200)` | Rolling buffer of all events — used to replay missed events on reconnect |
| `step` | `str` | Current pipeline step name e.g. `SCANNING` |
| `result` | `dict` | Final scan output, populated on `DONE` |
| `finished_at` | `float` | Monotonic timestamp — signals stream() to stop waiting |

**Benefits**:
- `event_log` is the key to **crash/refresh recovery** — any reconnecting client can ask for events `since=N` and catch up instantly.
- Keeping `queue` and `event_log` separate means a slow client consuming the queue doesn't prevent a fast one from reading the log.
- `metadata` is intentionally `dict` — no schema change needed when a new scan type is added.

---

### 2. `ReusableJobTracker`
**File**: `core/jobs/tracker.py`

**What it is**: A singleton manager that owns all active `GenericJobRecord` instances and handles event broadcasting to both in-memory SSE queues and Redis.

**Key methods**:
| Method | Purpose |
|---|---|
| `create_job(job_id, job_type, metadata)` | Registers a new job record in memory |
| `get_job(job_id)` | Retrieves a job record by ID |
| `push_event(job_id, step, message, progress, detail)` | Updates job state, appends to `event_log`, puts into `queue`, and writes to Redis |
| `stream(job_id, since_index)` | Async generator: replays buffered events first, then streams live queue events |

**Benefits**:
- **Single write path**: Every scan — regardless of type — calls `push_event()`. There is only one code path to maintain.
- **Redis durability**: Every event is also `RPUSH`-ed into Redis with a 24-hour TTL, so the event history survives pod restarts and serves cross-pod clients.
- **Reconnect-safe `stream()`**: The `since_index` parameter lets the frontend resume exactly where it left off after a dropped connection, without replaying all events from scratch.

---

### 3. `JobEvent`
**File**: `core/jobs/tracker.py`

**What it is**: A typed Pydantic model representing a single pipeline event that is broadcast to all listeners.

```python
class JobEvent(BaseModel):
    job_id: str
    job_type: str       # "repo-scan" | "quick-scan"
    step: str           # "QUEUED" | "PROVISIONING" | "SCANNING" | "DONE" | "ERROR"
    message: str        # Human-readable status message
    progress: int       # 0–100 percentage
    detail: Optional[dict]  # Final result payload on DONE, error info on ERROR
```

**Benefits**:
- Pydantic validation guarantees every event is well-formed before it hits the SSE stream.
- `.json()` serialization is called once at push time and reused for both the `event_log` buffer and the Redis `RPUSH` — no double serialization.
- The `detail` field doubles as the result carrier, eliminating a separate result-fetch round trip for the frontend.

---

### 4. Generic Job Router
**File**: `core/jobs/router.py`

**What it is**: A FastAPI `APIRouter` mounted at `/v1/jobs` that provides three endpoints consumed by all scan types.

| Endpoint | Purpose |
|---|---|
| `GET /v1/jobs?job_type=repo-scan` | Lists all jobs of a given type from memory + Redis |
| `GET /v1/jobs/{id}/events?since=N` | Returns missed event history for reconnect/hydration |
| `GET /v1/jobs/{id}/status?since=N` | Streams live SSE events (with initial replay from `since`) |
| `GET /v1/jobs/{id}/result` | Retrieves the completed JSON scan report |

**Benefits**:
- **One router serves all scan types** — the existing `scan_repository.py` router and future scan types simply call `tracker.push_event()` and the client connects to the same generic SSE endpoint.
- `GET /events` enables the frontend to hydrate a completed job instantly (no SSE needed), reducing unnecessary long-lived connections.
- `?since=N` on both endpoints means reconnect logic is trivial: just pass `eventIndex` from localStorage.

---

## Frontend Components

### 5. `jobStore`
**File**: `src/lib/jobStore.ts`

**What it is**: A thin, typed `localStorage` adapter that persists all job records under a unified key (`unified_jobs_v1`).

**Key functions**:
| Function | Purpose |
|---|---|
| `getAll(type?)` | Returns all jobs, optionally filtered by `job_type` |
| `upsert(job)` | Inserts or updates a job by `job_id` (newest first) |
| `get(id)` | Retrieves a single job record |
| `remove(id)` | Deletes a job from the store |

**Benefits**:
- **Survives page refresh, tab close, and browser restart** — jobs are always recoverable.
- Filtering by `job_type` in `getAll()` means `RepoScanner` and `SecurityScanner` see only their own jobs, with zero overlap.
- The `unified_jobs_v1` key name is versioned — if the schema changes, bumping the version prevents stale-data conflicts without a migration step.

---

### 6. `useJobStore` Hook
**File**: `src/hooks/useJobStore.ts`

**What it is**: A React hook that any scan component mounts to gain full async pipeline capabilities: localStorage persistence, live SSE streaming, automatic reconnect on failure, and mount-time recovery of in-progress jobs.

**Signature**:
```typescript
const { jobs, addJob, openStream, refresh } = useJobStore(
  jobType,   // "repo-scan" | "quick-scan"
  apiBase,   // e.g. "https://api.z1sandbox.com"
  apiKey     // Bearer token from localStorage
);
```

**Lifecycle**:
1. On mount — reads jobs from `jobStore` filtered by `jobType`.
2. For any job not in a terminal state (`DONE`/`ERROR`) — calls `openStream()` from its last `eventIndex`.
3. On each SSE message — upserts the updated job into `jobStore` and triggers a React re-render.
4. On SSE error — closes the broken `EventSource` and schedules a reconnect after 2 seconds.
5. On unmount — closes all open `EventSource` connections to prevent memory leaks.

**Benefits**:
- **Zero boilerplate for new scan types**: Any component passes its `jobType` and immediately gets multi-job tracking, reconnect handling, and localStorage sync for free.
- **Independent streams**: Multiple `EventSource` connections are managed in a `useRef` map — jobs never interfere with each other's streams.
- **Reconnect is automatic and stateful**: Uses `eventIndex` from localStorage as the `?since=N` parameter, so no events are replayed unnecessarily.

---

### 7. `UnifiedPipelineView`
**File**: `src/components/dashboard/UnifiedPipelineView.tsx`

**What it is**: A purely presentational React component that renders a GitHub Actions-style step list for any job. It does not know what scan type it is displaying — it receives a `steps` configuration array and renders accordingly.

**Props**:
```typescript
interface UnifiedPipelineViewProps {
  job: GenericJob;                            // Active job record
  steps: { key: string; label: string }[];    // Step schema for this job type
  onResultRender: (result: any) => ReactNode; // Injected result renderer
}
```

**Usage for Repo Scanner**:
```tsx
<UnifiedPipelineView
  job={selectedJob}
  steps={REPO_STEPS}
  onResultRender={(result) => <RepoScanResultPanel result={result} />}
/>
```

**Usage for Quick Scan**:
```tsx
<UnifiedPipelineView
  job={selectedJob}
  steps={QUICK_STEPS}
  onResultRender={(result) => <SecurityScanResultPanel result={result} />}
/>
```

**Step Rendering Logic**:
| Condition | Visual State |
|---|---|
| `idx < currentStepIdx` | ✅ Green checkmark — completed |
| `step.key === job.status` | ⟳ Pulsing blue spinner — active |
| `job.status === "ERROR" && idx >= currentIdx` | ❌ Red — failed |
| `idx > currentStepIdx` | ○ Grey — not yet reached |

**Benefits**:
- **Reusable without modification**: Adding a new scan type only requires defining a new `steps` array — the rendering logic, status calculation, and progress bar are inherited.
- **Result rendering is injected**: The component stays generic and presentation-only. The parent controls what the final result looks like, keeping concerns cleanly separated.
- **Schema-driven step visibility**: Steps not included in a scan type's schema simply don't appear — no conditional rendering needed inside the component.

---

### 8. `JobsPanel`
**File**: `src/components/dashboard/JobsPanel.tsx`

**What it is**: The left sidebar that lists all concurrent and historical jobs returned by `useJobStore`. Clicking a row sets the `selectedJobId` in the parent, updating the `UnifiedPipelineView` on the right.

**Behavior**:
- Jobs are listed newest-first (based on `submittedAt`).
- Status badges are rendered dynamically based on `job.status`:
  - `DONE` → green checkmark
  - `ERROR` → red X
  - Active steps → blue pulsing dot
  - `QUEUED` → yellow dot
- The selected job row is highlighted.

**Benefits**:
- **Does not own state** — receives `jobs[]` and `onSelect()` from the parent, making it trivially testable and reusable across the Repo Scanner page and any future scan dashboard.
- **Decoupled from scan type** — works identically for `repo-scan` and `quick-scan` jobs because it only reads `GenericJob` fields common to all types (`job_id`, `status`, `submittedAt`, `metadata`).

---

## How a New Scan Type Plugs In

Adding a third scan type (e.g. `container-scan`) requires **no changes to shared components**. Only three things need to be written:

1. **Backend pipeline** (`container_scan.py`): Call `tracker.create_job(id, "container-scan", {...})`, then call `tracker.push_event(...)` at each step.

2. **Step configuration** (in the new page component):
   ```typescript
   const CONTAINER_STEPS = [
     { key: "QUEUED",       label: "Job Queued" },
     { key: "PULLING",      label: "Pull Container Image" },
     { key: "SCANNING",     label: "Vulnerability Scan" },
     { key: "DONE",         label: "Complete" },
   ];
   ```

3. **Page component**:
   ```tsx
   const { jobs, addJob, openStream } = useJobStore("container-scan", API_BASE, apiKey);

   <UnifiedPipelineView
     job={selectedJob}
     steps={CONTAINER_STEPS}
     onResultRender={(r) => <ContainerScanResult result={r} />}
   />
   ```

That is the entire integration. The reconnect logic, localStorage persistence, concurrent job tracking, and GitHub Actions-style UI are all inherited automatically.

---

## Component Dependency Summary

```text
RepoScanner.tsx / SecurityScanner.tsx
    │
    ├── useJobStore (hook)
    │       ├── jobStore (lib)  ←→  localStorage
    │       └── EventSource    ←→  GET /v1/jobs/{id}/status
    │
    ├── JobsPanel
    │       └── (reads from useJobStore.jobs)
    │
    └── UnifiedPipelineView
            ├── steps[]           (scan-type config)
            └── onResultRender()  (scan-type renderer)


FastAPI Application
    │
    ├── scan_repository.py  ──┐
    ├── sandboxes/router.py ──┤──► ReusableJobTracker.push_event()
    └── (future: container_scan.py) ┘
                                       │
                              ┌────────┴──────────┐
                              ▼                   ▼
                         event_log             Redis
                         (deque)           (RPUSH/EXPIRE)
                              │                   │
                    stream() SSE              GET /events
                         replay              replay
```

---

## Before vs After: What Changed

This table shows exactly what the old approach did vs what the new modular framework does.

| Concern | ❌ Old Approach | ✅ New Approach |
|---|---|---|
| **State storage** | `useState` in the component — lost on refresh | `localStorage` via `jobStore` — persists forever |
| **Concurrent jobs** | One at a time; submitting a new job replaced the old one | Independent `EventSource` per job; multiple run simultaneously |
| **Reconnect after refresh** | Impossible — state was gone | `useJobStore` reads `eventIndex` and reconnects with `?since=N` |
| **Backend event log** | `asyncio.Queue` only — drained once, no replay | `deque` buffer + Redis `RPUSH` — replayable from any index |
| **New scan types** | Copy-paste the entire scanner component and SSE logic | Pass a `steps[]` config and `jobType` to shared hook and view |
| **Job history** | Disappeared the moment you navigated away | Redis stores 24h of events; localStorage holds job metadata |
| **Error recovery** | Page refresh = complete restart from zero | `GET /events?since=N` fast-forwards to last known state |
| **Cross-pod visibility** | Only the pod that ran the job could stream it | Any pod reads Redis event log and streams it to the client |

---

## Full Data Flow Walkthrough

### Scenario 1: User Submits a New Repo Scan

```text
1. User types a GitHub URL and clicks "Scan"
   └─ RepoScanner.tsx → handleScan()

2. POST /v1/repo-scan { repo_url }
   └─ FastAPI creates job_id (UUID)
   └─ tracker.create_job(job_id, "repo-scan", { repo_url })
   └─ tracker.push_event(job_id, "QUEUED", "Job queued", 0)
      ├─ Appends JobEvent to in-memory event_log deque
      ├─ Puts JobEvent into asyncio.Queue
      ├─ RPUSH job:{id}:events → Redis (TTL 24h)
      └─ SET  job:{id}:status=QUEUED → Redis (TTL 24h)
   └─ Returns { job_id, repo_url, submitted_at, status_url }

3. Frontend receives response
   └─ addJob({ job_id, job_type: "repo-scan", status: "QUEUED", eventIndex: 0, ... })
      └─ jobStore.upsert() → localStorage["unified_jobs_v1"]
   └─ openStream(job_id, since=0)
      └─ new EventSource("/v1/jobs/{id}/status?since=0")

4. Background task starts: _run_scan_pipeline()
   ├─ push_event(PROVISIONING, "Creating sandbox", 10)
   ├─ push_event(CLONING,      "Cloning repo",    30)
   ├─ push_event(DETECTING,    "Found Python, Go", 50)
   ├─ push_event(SCANNING,     "Running Semgrep",  70)
   └─ push_event(DONE,         "Scan complete",   100, result_dict)

5. Each push_event() flows to the open EventSource:
   └─ es.onmessage fires
      └─ jobStore.upsert({ ...stored, status: ev.step, progress: ev.progress,
                            eventIndex: stored.eventIndex + 1 })
      └─ React re-renders JobsPanel (badge updates) + UnifiedPipelineView (step advances)

6. When step == "DONE":
   └─ jobStore.upsert({ result: ev.detail, completedAt: now })
   └─ EventSource.close()
   └─ UnifiedPipelineView renders the result panel via onResultRender()
```

---

### Scenario 2: User Refreshes the Page Mid-Scan

```text
1. Page mounts
   └─ useJobStore() initializes from localStorage
   └─ jobs = [{ job_id: "abc", status: "SCANNING", eventIndex: 14 }]

2. Job "abc" is not terminal → openStream("abc", since=14)
   └─ GET /v1/jobs/abc/status?since=14

3. FastAPI stream() handler:
   └─ Checks in-memory event_log
      ├─ If job exists: replay event_log[14:] immediately, then stream live queue
      └─ If job not in memory (pod restart):
         └─ LRANGE job:abc:events 14 -1 from Redis
         └─ Replay those events as SSE, then subscribe to Redis Pub/Sub channel

4. Frontend receives replayed events
   └─ Each replayed event calls jobStore.upsert() → updates eventIndex
   └─ If last replayed event is DONE → render result, close stream
   └─ If not done → stream continues live
```

---

### Scenario 3: User Triggers Quick Scan While Repo Scan is Running

```text
1. Repo scan "aaa" is already streaming (EventSource open)

2. User clicks "Quick Scan" in the Dashboard
   └─ POST /api/v1/{backend_id}/scan-jobs?async=true { files }
   └─ tracker.create_job("bbb", "quick-scan", { files })
   └─ Returns { job_id: "bbb" }

3. useJobStore("quick-scan") in SecurityScanner:
   └─ addJob({ job_id: "bbb", job_type: "quick-scan", ... })
   └─ openStream("bbb", 0)
      └─ New EventSource("/v1/jobs/bbb/status?since=0")
         (completely separate from "aaa"'s stream)

4. Both EventSources run independently:
   └─ "aaa" stream → updates JobsPanel row for repo-A
   └─ "bbb" stream → updates SecurityScanner dialog progress

5. localStorage now holds both:
   unified_jobs_v1 = [
     { job_id: "aaa", job_type: "repo-scan",  status: "SCANNING", ... },
     { job_id: "bbb", job_type: "quick-scan", status: "SCANNING", ... }
   ]
```

---

## Common Gotchas

### ❗ `eventIndex` must increment on every event, not just on state changes
The `eventIndex` in localStorage tracks how many SSE messages the client has consumed — it must be incremented for every `onmessage` event, including intermediate steps. Skipping increments causes the `?since=N` reconnect to replay already-seen events.

```typescript
// ✅ Correct
eventIndex: stored.eventIndex + 1

// ❌ Wrong — only bumps on certain steps
eventIndex: ev.step === "DONE" ? stored.eventIndex + 1 : stored.eventIndex
```

---

### ❗ `deque(maxlen=200)` is a rolling window, not a full log
The in-memory `event_log` holds only the last 200 events. For very long scans, early events will be evicted. Redis is the authoritative full log — for `GET /events` replay, always prefer Redis over `event_log` when the job is old or the deque might be short.

```python
# Always check Redis for events beyond what deque holds
raw = redis_client.lrange(f"job:{job_id}:events", since, -1)
```

---

### ❗ Do not call `openStream()` before `addJob()`
`openStream()` sets up an `EventSource` that fires `onmessage`, which immediately calls `jobStore.get(job_id)`. If the job hasn't been `addJob()`-ed yet, `get()` returns `null` and the upsert fails silently.

```typescript
// ✅ Correct order
addJob({ job_id, ... });
openStream(job_id, 0);

// ❌ Wrong order
openStream(job_id, 0);   // onmessage fires before job exists in store
addJob({ job_id, ... });
```

---

### ❗ Redis TTL is 24 hours — plan accordingly
Job history and event logs expire from Redis after 24 hours. If users need longer history, increase `ex=86400` in `push_event()`. For compliance or audit use cases, consider writing final results to a database (`api_keys.db` / Postgres) in addition to Redis.

---

### ❗ `UnifiedPipelineView` does not animate transitions automatically
The step list re-renders when `job.status` changes, but there are no CSS transition classes by default. Add `transition-all duration-300` to the step row `className` to get smooth visual step advances.

```tsx
<div className={cn("step-row transition-all duration-300", ...)}>
```

---

## FAQ for Teammates

**Q: Do I need to change the existing `scan_repository.py` to work with the new tracker?**

A: Yes, but minimally. The `push_event()` local helper in `_run_scan_pipeline` should be changed to call `tracker.push_event()` instead of `sse_manager.push()`. The `SSEManager` is then replaced by `ReusableJobTracker`. The pipeline business logic (sandbox provisioning, cloning, scanning) is untouched.

---

**Q: Can two browser tabs both watch the same job?**

A: Yes. Each tab opens its own `EventSource` to `/v1/jobs/{id}/status`. The `asyncio.Queue` in `GenericJobRecord` is a fan-out queue — multiple consumers can read from it concurrently. Both tabs will receive every event.

---

**Q: What happens if the backend pod restarts while a scan is running?**

A: The scan itself dies (it was a `BackgroundTask` in that pod). The frontend will detect the dropped SSE connection via `es.onerror` and attempt to reconnect. On reconnect, `?since=N` is sent, and the backend will read the event history from Redis. Since the scan died, no new events will arrive and the UI will remain frozen at the last known step until a timeout or manual intervention. Future work: add a watchdog that marks orphaned jobs as `ERROR` after a timeout.

---

**Q: Why `localStorage` and not `sessionStorage` or React Context?**

A:
- `sessionStorage` is cleared when the tab closes — not persistent enough.
- React Context is lost on refresh — same problem as `useState`.
- `localStorage` survives refresh, tab close, and (with the same origin) even browser restarts. It also works across tabs on the same domain, meaning if you open the dashboard in two tabs, both tabs share job state.

---

**Q: How do I clear all job history?**

A: Call `localStorage.removeItem("unified_jobs_v1")` from the browser console, or provide a "Clear History" button that calls `jobStore.getAll().forEach(j => jobStore.remove(j.job_id))`.

---

**Q: How does `UnifiedPipelineView` know which step is "active" vs "done"?**

A: It finds the index of `job.status` in the `steps[]` array. All steps with an index lower than that are considered done. The step whose `key` matches `job.status` is active. Steps with a higher index are pending. This means the `steps[]` array must be ordered sequentially — the order of steps in the config array defines the pipeline order.

---

**Q: Does implementing this new modular pipeline router bypass or hinder our existing API key rate limiters?**

A: No. All endpoints inside `/v1/jobs` are registered with the standard `validate_token` dependency (`dependencies=[Depends(validate_token)]`). This dependency internally triggers `check_rate_limit(state, jti)` in `auth/token_validator.py`. As a result, the existing dynamic sliding-window and fixed-window rate limiters are completely active and enforced at line-rate on all new endpoints. No security logic is bypassed or hindered.

---

## Design Principles

These principles guided every decision in this architecture. Share them with the team to maintain consistency as the system grows:

| Principle | Application |
|---|---|
| **Single source of truth per layer** | Redis is the truth for the backend; `localStorage` is the truth for the browser. Neither duplicates the other. |
| **Push, don't poll** | SSE pushes events to the client in real time. The client never polls `/status` in a loop. |
| **Replay beats reconnect** | Instead of restarting a scan on disconnect, we replay missed events from the buffer. This makes the system resilient to flaky networks. |
| **Components receive data, not responsibilities** | `UnifiedPipelineView` renders — it never fetches. `useJobStore` fetches — it never renders. Separation keeps both testable. |
| **Config over code for new types** | Adding a new scan type should require a `steps[]` array and a `jobType` string — not a new component tree or new SSE router. |

---

## Testing Guide

### Backend: Testing `ReusableJobTracker`

Each method can be tested in isolation without a running FastAPI server.

**Test `push_event()` writes to Redis**:
```python
import asyncio
from unittest.mock import MagicMock, patch
from core.jobs.tracker import ReusableJobTracker

def test_push_event_writes_to_redis():
    mock_state = MagicMock()
    mock_state.use_redis = True
    mock_state.redis_client = MagicMock()

    tracker = ReusableJobTracker(mock_state)
    tracker.create_job("abc", "repo-scan", {"repo_url": "https://github.com/org/repo"})

    asyncio.run(tracker.push_event("abc", "CLONING", "Cloning repo", 30))

    mock_state.redis_client.rpush.assert_called_once()
    mock_state.redis_client.expire.assert_called_once()
    mock_state.redis_client.set.assert_called()
```

**Test `stream()` replays buffered events**:
```python
async def test_stream_replays_event_log():
    mock_state = MagicMock()
    mock_state.use_redis = False
    tracker = ReusableJobTracker(mock_state)
    tracker.create_job("abc", "quick-scan", {})

    await tracker.push_event("abc", "QUEUED",     "Queued",  0)
    await tracker.push_event("abc", "SCANNING",   "Running", 50)
    await tracker.push_event("abc", "DONE",       "Done",   100, {"findings": []})

    events = []
    async for chunk in tracker.stream("abc", since_index=1):
        if chunk.startswith("data:"):
            events.append(chunk)

    # since_index=1 means skip the first event (QUEUED)
    assert len(events) == 2   # SCANNING + DONE
```

---

### Frontend: Testing `jobStore`

`jobStore` reads and writes `localStorage`, so tests must either mock `localStorage` or run in a browser-like environment (jsdom).

**Test `upsert` inserts a new job**:
```typescript
import { jobStore } from "@/lib/jobStore";

beforeEach(() => localStorage.clear());

test("upsert inserts a new job at the front", () => {
  jobStore.upsert({ job_id: "aaa", job_type: "repo-scan", status: "QUEUED",
                    progress: 0, stepMessage: "", eventIndex: 0,
                    metadata: {}, result: null,
                    submittedAt: new Date().toISOString(), completedAt: null });

  const jobs = jobStore.getAll();
  expect(jobs).toHaveLength(1);
  expect(jobs[0].job_id).toBe("aaa");
});

test("upsert updates an existing job in place", () => {
  jobStore.upsert({ job_id: "aaa", job_type: "repo-scan", status: "QUEUED",
                    progress: 0, stepMessage: "", eventIndex: 0,
                    metadata: {}, result: null,
                    submittedAt: new Date().toISOString(), completedAt: null });

  jobStore.upsert({ job_id: "aaa", job_type: "repo-scan", status: "SCANNING",
                    progress: 70, stepMessage: "Running Semgrep", eventIndex: 3,
                    metadata: {}, result: null,
                    submittedAt: new Date().toISOString(), completedAt: null });

  const jobs = jobStore.getAll();
  expect(jobs).toHaveLength(1);           // no duplicate
  expect(jobs[0].status).toBe("SCANNING");
  expect(jobs[0].eventIndex).toBe(3);
});

test("getAll(type) filters by job_type", () => {
  jobStore.upsert({ job_id: "r1", job_type: "repo-scan",  status: "DONE", ... });
  jobStore.upsert({ job_id: "q1", job_type: "quick-scan", status: "DONE", ... });

  expect(jobStore.getAll("repo-scan")).toHaveLength(1);
  expect(jobStore.getAll("quick-scan")).toHaveLength(1);
  expect(jobStore.getAll()).toHaveLength(2);
});
```

---

### Frontend: Testing `useJobStore` Hook

Use React Testing Library + `msw` (Mock Service Worker) to mock the SSE endpoint.

```typescript
import { renderHook, act } from "@testing-library/react";
import { useJobStore } from "@/hooks/useJobStore";

beforeEach(() => localStorage.clear());

test("addJob persists to localStorage and appears in jobs list", () => {
  const { result } = renderHook(() =>
    useJobStore("repo-scan", "http://localhost:8000", "test-key")
  );

  act(() => {
    result.current.addJob({
      job_id: "aaa", job_type: "repo-scan", status: "QUEUED",
      progress: 0, stepMessage: "", eventIndex: 0,
      metadata: { repo_url: "https://github.com/org/repo" },
      result: null, submittedAt: new Date().toISOString(), completedAt: null,
    });
  });

  expect(result.current.jobs).toHaveLength(1);
  expect(result.current.jobs[0].job_id).toBe("aaa");
});
```

---

### Manual End-to-End Test Checklist

Run through these scenarios manually before merging the feature branch:

| # | Scenario | Expected Result |
|---|---|---|
| 1 | Submit a repo scan → watch the step list advance | Steps turn green one by one; progress bar fills |
| 2 | Submit two repo scans back to back | Both appear in `JobsPanel`; each advances independently |
| 3 | Refresh the page while a scan is at SCANNING step | UI reconnects and fast-forwards to current step |
| 4 | Open the page after a DONE scan (within 24h) | Result loads from localStorage instantly; no SSE needed |
| 5 | Click "Quick Scan" while a repo scan is running | Both badges update independently; dialog shows Quick Scan progress |
| 6 | Kill network connection during a scan, restore it | EventSource reconnects automatically; resumes from `eventIndex` |
| 7 | Wait for scan to complete on one tab; open the same URL in a new tab | New tab shows DONE state with full result from localStorage |
| 8 | Call `GET /v1/jobs?job_type=quick-scan` via curl | Returns only quick-scan jobs; no repo-scan jobs in response |

---

## Implementation Checklist

Use this as a sequential task list when building out the pipeline framework:

### Phase 1 — Backend Infrastructure
- [ ] Create `core/jobs/` directory
- [ ] Implement `core/jobs/tracker.py` — `JobEvent`, `GenericJobRecord`, `ReusableJobTracker`
- [ ] Add `tracker` singleton instantiation in `core/app_state.py`
- [ ] Implement `core/jobs/router.py` — `GET /v1/jobs`, `GET /v1/jobs/{id}/events`, `GET /v1/jobs/{id}/status`, `GET /v1/jobs/{id}/result`
- [ ] Register `core/jobs/router.py` in the FastAPI app

### Phase 2 — Migrate Existing Scan Modules
- [ ] Update `scan_repository.py`:
  - [ ] Call `tracker.create_job()` at job submission
  - [ ] Replace `sse_manager.push()` calls with `tracker.push_event()`
  - [ ] Add `repo_url` and `submitted_at` to response model
- [ ] Update `sandboxes/router.py` (Quick Scan):
  - [ ] Call `tracker.create_job(type="quick-scan")` at submission
  - [ ] Replace polling/blocking logic with `tracker.push_event()` for each phase
- [ ] Update `models.py` — add `repo_url`, `submitted_at` to `RepoScanSubmitResponse`

### Phase 3 — Frontend Infrastructure
- [ ] Create `src/lib/jobStore.ts` — `GenericJob` type, `jobStore` CRUD
- [ ] Create `src/hooks/useJobStore.ts` — SSE management, reconnect, localStorage sync
- [ ] Create `src/components/dashboard/UnifiedPipelineView.tsx` — step list, progress bar, result injection
- [ ] Create `src/components/dashboard/JobsPanel.tsx` — sidebar job list with status badges

### Phase 4 — Migrate Existing UI Components
- [ ] Update `RepoScanner.tsx`:
  - [ ] Replace local `useState` with `useJobStore("repo-scan", ...)`
  - [ ] Replace inline pipeline view with `<UnifiedPipelineView steps={REPO_STEPS} .../>`
  - [ ] Add `<JobsPanel>` sidebar
- [ ] Update `SecurityScanner.tsx` (Quick Scan dialog):
  - [ ] Replace `isScanning` / `result` state with `useJobStore("quick-scan", ...)`
  - [ ] Replace status text with `<UnifiedPipelineView steps={QUICK_STEPS} .../>`

### Phase 5 — Verification
- [ ] Run manual end-to-end test checklist (see above)
- [ ] Verify `GET /v1/jobs?job_type=repo-scan` filters correctly
- [ ] Verify page refresh mid-scan reconnects from correct `eventIndex`
- [ ] Verify two concurrent scans do not share state
- [ ] Verify localStorage `unified_jobs_v1` structure is correct after each test

---

## Project File Structure

After implementation, the new and modified files are:

```text
apiServer/fastapi/
├── core/
│   ├── app_state.py              [MODIFY] — add tracker singleton
│   ├── jobs/                     [NEW DIRECTORY]
│   │   ├── __init__.py           [NEW]
│   │   ├── tracker.py            [NEW] — ReusableJobTracker, GenericJobRecord, JobEvent
│   │   └── router.py             [NEW] — /v1/jobs endpoints
│   └── ...
├── scan_repository/
│   ├── scan_repository.py        [MODIFY] — use tracker.push_event()
│   └── models.py                 [MODIFY] — add repo_url, submitted_at to response
└── sandboxes/
    └── router.py                 [MODIFY] — use tracker for quick-scan jobs

z1sandbox-website/src/
├── lib/
│   └── jobStore.ts               [NEW] — localStorage CRUD for GenericJob[]
├── hooks/
│   └── useJobStore.ts            [NEW] — SSE management + reconnect logic
├── components/dashboard/
│   ├── UnifiedPipelineView.tsx   [NEW] — step list + progress bar + result injection
│   ├── JobsPanel.tsx             [NEW] — left sidebar with concurrent job list
│   ├── SecurityScanner.tsx       [MODIFY] — plug into useJobStore("quick-scan")
│   └── RepoScannerWidget.tsx     [MODIFY] — plug into useJobStore("repo-scan")
└── pages/
    └── RepoScanner.tsx           [MODIFY] — split-pane layout with JobsPanel
```

---

## Glossary

| Term | Definition |
|---|---|
| **Job** | A single scan execution, identified by a `job_id` UUID. Has a type, status, event log, and optional result. |
| **Job Type** | A string identifier (`"repo-scan"`, `"quick-scan"`) that classifies which scanner produced the job and which step schema to use. |
| **SSE (Server-Sent Events)** | A browser API where the server pushes events over a persistent HTTP connection. Used here instead of WebSockets because it is unidirectional (server → client) and simpler. |
| **EventSource** | The browser object that opens and maintains an SSE connection. Each active job has one `EventSource` per open tab. |
| **event_log** | A Python `deque(maxlen=200)` in `GenericJobRecord` that stores the last 200 events in memory. Used for instant replay to reconnecting clients on the same pod. |
| **Redis RPUSH** | Appends a value to the right of a Redis list. Used to build an ordered event log that any pod can query. |
| **`?since=N`** | A query parameter on SSE and event endpoints. `N` is the number of events already consumed by the client. The server skips the first `N` events in its replay. |
| **eventIndex** | The frontend's counter of how many SSE messages it has received for a job. Stored in `localStorage` and used as the `since=N` value on reconnect. |
| **TTL (Time-To-Live)** | Redis expiry duration. Currently set to `86400` seconds (24 hours) for all job keys. |
| **`GenericJob`** | The TypeScript interface representing a persisted job record in `localStorage`. Contains `job_id`, `job_type`, `status`, `progress`, `eventIndex`, `result`, and timestamps. |
| **`useJobStore`** | The React hook that owns all SSE connections, localStorage syncing, and reconnect logic. The single integration point for any scan UI component. |
| **`UnifiedPipelineView`** | A stateless React component that renders a GitHub Actions-style step list for any job type, given a `steps[]` configuration array. |
| **`JobsPanel`** | The left sidebar component listing all concurrent and historical jobs with live status badges. |
| **`PipelineStepConfig`** | A TypeScript type `{ key: string; label: string }` defining one step in a scan pipeline. An array of these drives `UnifiedPipelineView`. |
| **Fan-out queue** | The `asyncio.Queue` inside `GenericJobRecord`. Multiple `EventSource` consumers can read from it simultaneously, each receiving the same events. |
| **Replay** | The process of sending a subset of the `event_log` or Redis list to a client that reconnected mid-job, so it catches up to the current state without re-running the scan. |
