# Modular Async Scan Pipeline — Architecture Flow

This document explains the end-to-end flow of the modular asynchronous scan pipeline, including job tracking, SSE streaming, persistence, and recovery after browser refresh.

## Overview

The system solves:
- Independent scans
- Real-time progress updates
- Persistence across refreshes
- Job recovery
- Server-side event tracking

## High-Level Sequence Diagram

```plaintext
 User/UI         useJobStore      localStorage      FastAPI Backend       Redis
    │                 │                 │                  │                │
    ├─ 1. Click Scan ─►                 │                  │                │
    │                 ├───────── 2. POST /v1/repo-scan ────►                │
    │                 │                                    ├─ 3. Init Job ─►
    │                 ◄───────── 4. Return job_id ─────────┤                │
    │                 ├─ 5. upsert() ──►│                  │                │
    │                 ├──── 6. SSE: GET /status?since=0 ───────────────────►

PHASE 2: LIVE STREAMING

    │                 │                 │                  ├─ 7. Pipeline ─►
    │                 │                 │                  │    Progress
    │                 │                 │                  ├─ 8. RPUSH log ─►
    │                 ◄───────── 9. SSE Message ───────────┤                │
    │                 ├─ 10. upsert() ─►│                  │                │
    ◄─ 11. Re-render ─┤

PHASE 3: BROWSER REFRESH (RECOVERY)

    ├─ 12. Refresh ──►│
    │                 ├─ 13. getAll() ─►│
    │                 │  (Index is 14)
    │                 ├──── 14. SSE: GET /status?since=14 ─────────────────►
    │                 │                 │                  ├─ 15. Fetch ───►
    │                 │                 │                  │    Log 14+
    │                 ◄──────── 16. Fast-Forward SSE ──────┤
```

## Phase 1: The Handshake

1. User clicks scan.
2. Frontend sends POST `/v1/repo-scan`.
3. Backend initializes job and Redis tracking.
4. Backend returns `job_id`.
5. Frontend saves via `upsert()` to localStorage.
6. Frontend opens SSE stream using `?since=0`.

## Phase 2: Live Streaming

- Background worker executes scanning pipeline.
- `tracker.push_event()` pushes updates.
- Redis stores logs using RPUSH.
- SSE streams updates to UI.
- Frontend updates local state and re-renders.

## Phase 3: Browser Refresh Recovery

1. Browser refresh resets React memory.
2. `localStorage` restores state.
3. Frontend reconnects using `?since=14`.
4. Backend fetches missing Redis logs.
5. SSE fast-forwards UI without restarting scan.

## Mental Model

Think of it like Netflix resume playback:

- Frontend = Video player
- Redis = Playback history
- SSE = Live stream
- eventIndex = Timestamp
- Refresh = Reopen app
- since=14 = Resume playback


```
User/UI         useJobStore      localStorage       FastAPI Backend     Ephemeral Pod        RWX PVC
    │                 │                 │                   │                   │                │
    ├─ 1. Click Scan ─►                 │                   │                   │                │
    │                 ├────── 2. POST /v1/repo-scan ────────►                   │                │
    │                 │                                     ├─ 3. Init Job      │                │
    │                 │                                     │  (Redis State)    │                │
    │                 │                                     │                   │                │
    │                 │                                     ├─ 4. Hand off to BackgroundTasks    │
    │                 │                                     │  (Fire-and-forget thread)          │
    │                 ◄────── 5. Return job_id (Immediate) ─┤                   │                │
    │                 │                 │                   │                   │                │
    │                 ├─ 6. upsert() ──►│                   │                   │                │
    │                 │  (~100 bytes)   │                   │                   │                │
    │                 │                 │                   │                   │                │
    │                 ├──────────────── 7. SSE: GET /status?since=0 ────────────►                │
    │                 │                 │                   │                   │                │
    │                 │                 │                   │  8. Dynamic Spawn │                │
    │                 │                 │                   ├─ (Kubernetes API) ─►               │
    │                 │                 │                   │                   │                │
    │                 │                 │                   │                   │ [Runs Scan]    │
    │                 │                 │                   │                   │ ───────────┐   │
    │                 │                 │                   │                   │ │ Semgrep  │   │
    │                 │                 │                   │                   │ ◄──────────┘   │
    │                 │                 │                   │                   ├─ 9. Save JSON ─►
    │                 │                 │                   │                   │  Report File   │
    │                 │                 │                   │                   │                │
    │                 │                 │                   ◄─ 10. push_event ──┤                │
    │                 │                 │                   │   (status="DONE") │                │
    │                 │                 │                   │                   │                │
    │                 │                 │                   │                   │ [Self-Destruct]│
    │                 │                 │                   │                   X (Max 5 mins)   │
    │                 │                 │                   │                                    │
    │                 ◄──────────────── 11. SSE: "DONE" (No heavy payload) ───────────────────────┤
    │                 ├─ 12. upsert() ─►│                   │                                    │
    ◄─ 13. Render Row ┤                 │                   │                                    │
    │                 │                 │                   │                                    │
    │                 │                 │                   │                                    │
    ─── USER CLICKS HISTORICAL ROW (LAZY LOAD FROM PVC) ──────────────────────────────────────────
    │                 │                 │                   │                                    │
    ├─ 14. Select Row ─►                 │                   │                                    │
    │                 ├─────────────── 15. GET /v1/jobs/{id}/result ────────────►                │
    │                 │                                                         ├─ 16. Read File ─►
    │                 │                                                         ◄─ 17. Payload ──┤
    │                 ◄─────────────── 18. Stream raw report JSON ──────────────┤                │
    │                 │                                                                          │
    ◄─ 19. Render UI ──┤ [Held in volatile browser RAM only - completely bypasses localStorage]   │
    │                 │                                                                          │
```
---

## Technical Architecture & Code Linkages Walkthrough

To enable high-frequency, parallel, and stateful background scanning across repositories and individual files without bottlenecking server resources or crashing client browsers, we designed and implemented a **Stateful Asynchronous Job Tracking Framework**.

Below is the sequential breakdown of the system execution, linked directly to the newly established codebase.

### 1. Unified Job State & Redis Engine
The core abstraction that makes the entire pipeline possible is a thread-safe, polymorphic tracking engine. Instead of hardcoding unique routers or databases for every scan type, all jobs are managed through a unified state structure.

- **Source Code**: [tracker.py](file:///home/berrybytes/Desktop/01-Sandbox/apiServer/fastapi/core/jobs/tracker.py)
- **Key Details**:
  - `JobEvent`: A standardized Pydantic model containing `job_id`, `job_type`, `step`, `message`, `progress`, and an optional `detail` payload.
  - `GenericJobRecord`: An active job record representing an in-memory job. It uses an asynchronous event queue (`asyncio.Queue`) for live streaming and a `deque` ring-buffer to keep a rolling cache of the latest 200 events.
  - `ReusableJobTracker`: The coordinator class. It registers new tasks, updates statuses, pushes live data, and bridges state management between the in-memory FastAPI server and a Redis cluster.
- **System Integration**:
  - Registered as a global application singleton inside [app_state.py](file:///home/berrybytes/Desktop/01-Sandbox/apiServer/fastapi/core/app_state.py#L40-L60).
  - Main router routes are registered directly under the unified router inside [main.py](file:///home/berrybytes/Desktop/01-Sandbox/apiServer/fastapi/main.py).

---

### 2. Job Registration & Task Delegation (The Handshake)
When the user requests a new scan (such as scanning a GitHub Repository or submitting a file code snippet), the system avoids keeping the client waiting. It performs a "fire-and-forget" registration.

```mermaid
sequenceDiagram
    participant UI as React Client
    participant API as FastAPI Router
    participant Tracker as ReusableJobTracker
    participant Redis as Redis Cache
    participant Thread as Background Executor

    UI->>API: 1. POST /v1/repo-scan { repo_url }
    API->>Tracker: 2. create_job(job_id, type="repo-scan")
    Tracker->>Redis: 3. SET job:status = "QUEUED"
    API->>Thread: 4. start_background_task(pipeline)
    API-->>UI: 5. Return job_id (Immediate HTTP 202)
```

- **Source Code**:
  - Repository Scan Endpoint: [scan_repository.py](file:///home/berrybytes/Desktop/01-Sandbox/apiServer/fastapi/scan_repository/scan_repository.py#L440-L500)
  - Quick Inline Scan Endpoint: [router.py (sandboxes)](file:///home/berrybytes/Desktop/01-Sandbox/apiServer/fastapi/sandboxes/router.py#L80-L120)
- **Key Details**:
  - When the FastAPI router intercepts the request, it generates a unique `job_id` (UUID) or reads it from the gateway metadata.
  - It invokes `tracker.create_job(job_id, job_type="repo-scan", metadata={...})` to write initial metadata to Redis.
  - Using FastAPI's `BackgroundTasks`, it schedules `_run_scan_pipeline` on a background thread pool and immediately returns `HTTP 202` containing the `job_id` to the browser.

---

### 3. Asynchronous Pipeline Execution
Once the background thread fires, it executes the scan stages sequentially, computing progress percentage and publishing states.

- **Source Code**:
  - Full Scanning Sequence: [scan_repository.py](file:///home/berrybytes/Desktop/01-Sandbox/apiServer/fastapi/scan_repository/scan_repository.py#L71-L435)
  - Multi-Tool File Dispatcher: [file_scanner.py](file:///home/berrybytes/Desktop/01-Sandbox/apiServer/fastapi/scan_repository/file_scanner.py#L380-L545)
- **Technical Steps**:
  1. **Provisioning (10%)**: Spins up local temp directories. Calls `tracker.push_event(ScanStep.PROVISIONING, "Provisioning...", 10)`.
  2. **Cloning (25%)**: Fetches repository contents using standard git clone wrapper.
  3. **Detecting (45%)**: Runs language classification tools (`tokei` / `enry`) and returns language mapping.
  4. **Scanning (60% - 90%)**: Submits files categorized by languages in concurrent chunks to the `opensandbox-server` endpoint `/scan-jobs`. Progress dynamically increases per completed language.
  5. **Deduplication**: Gathers raw JSON outputs, normalizes, and filters findings to eliminate cross-language duplication.
  6. **Completion (100%)**: Clears workspace, aggregates severities (High, Medium, Low), and publishes the final `ScanStep.DONE` event alongside the complete scan result model.

---

### 4. Real-Time SSE Streams & Instant Reconnection
Instead of polling endpoints every second (which drains database connections), the system utilizes Server-Sent Events (SSE).

- **Source Code**:
  - SSE Streaming Endpoint: [router.py (jobs)](file:///home/berrybytes/Desktop/01-Sandbox/apiServer/fastapi/core/jobs/router.py#L70-L120)
  - Event Stream Manager: [tracker.py](file:///home/berrybytes/Desktop/01-Sandbox/apiServer/fastapi/core/jobs/tracker.py#L105-L175)
- **Key Details**:
  - The endpoint `GET /v1/jobs/{job_id}/status?since={index}` streams messages with the `text/event-stream` MIME type.
  - If a browser refreshes, it fetches its last known event index from localStorage and passes `since=14`.
  - The backend stream handler checks:
    - **In-Memory Cache**: If the server has the job in its active dictionary, it grabs the `event_log` deque and streams from index `14` to the end, then keeps the event queue open.
    - **Redis Hydration**: If the task was handled on a different pod replica (or the current pod restarted), the server queries the Redis list `job:{job_id}:events` via `LRANGE job_id 14 -1` to fast-forward replay the missed history. It then subscribes to the Redis Pub/Sub channel `job:{job_id}:chan` to seamlessly relay incoming live updates!

---

### 5. Frontend Local Storage & PVC Lazy-Loading
To keep the React client lightweight, the frontend divides data storage between persistent configurations and volatile RAM.

- **Source Code**:
  - Unified local store manager: [jobStore.ts](file:///home/berrybytes/Desktop/01-Sandbox/z1sandbox-website/src/lib/jobStore.ts)
  - Concurrent React SSE Hook: [useJobStore.ts](file:///home/berrybytes/Desktop/01-Sandbox/z1sandbox-website/src/hooks/useJobStore.ts)
  - Dark-mode Pipeline Visualizer: [UnifiedPipelineView.tsx](file:///home/berrybytes/Desktop/01-Sandbox/z1sandbox-website/src/components/dashboard/UnifiedPipelineView.tsx)
- **Technical Implementation**:
  - **Local Storage Optimization**: Storing raw SAST reports (which can exceed 10MB) in browser `localStorage` causes performance degradation. The `jobStore.ts` model solves this by explicitly setting `result: null` and saving only light-weight metadata (Job ID, Repo Name, Status, Timestamps, Severity Counters).
  - **Volatile React Cache**: The `useJobStore` hook holds the actual detailed scan findings in a React `volatileResults` state. This memory lives solely in volatile browser RAM.
  - **On-Demand Lazy Loading**: When a user selects a historical row in the dashboard, the system doesn't query a database. It invokes `lazyFetchResult(jobId)`, which makes an API request to `GET /v1/jobs/{job_id}/result`. The backend reads the persistent report file directly from the Read-Write-Many (RWX) PVC, returning the payload to React memory, bypassing local storage entirely.
  - **Responsive UI Actions**: The `UnifiedPipelineView.tsx` parses the job status and renders a beautiful, premium dark-mode sidebar, detailing step-by-step progress complete with animated indicators, matching the signature GitHub Actions execution aesthetic.
