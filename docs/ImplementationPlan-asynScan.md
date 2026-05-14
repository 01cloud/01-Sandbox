# Implementation Plan: Asynchronous Scan Pipeline Refactor

This document outlines the strategy for refactoring the synchronous, blocking scan pipeline into an asynchronous, non-blocking architecture using **Redis** for state tracking and **FastAPI BackgroundTasks** for execution.

## 1. Problem Statement
The current scanner implementation blocks the HTTP request for the duration of the scan (up to 5 minutes). This causes:
- **Gateway Timeouts**: Envoy or Nginx may kill the connection before the scan finishes.
- **Resource Exhaustion**: API workers are tied up waiting for I/O, reducing overall throughput.
- **Brittle UX**: Users must stay on the page and maintain a connection to receive results.

## 2. Proposed Architecture

### A. Asynchronous Lifecycle
Instead of a single blocking request, the lifecycle is split into three distinct phases:
1.  **Submission**: User submits code -> API returns `202 Accepted` + `job_id`.
2.  **Execution**: API triggers a background task -> Task calls OpenSandbox -> Results written to PVC.
3.  **Retrieval**: Frontend polls for status -> Once `COMPLETED`, frontend fetches the report.

### B. Redis State Machine
Redis will act as the "Source of Truth" for job lifecuycle states.

| State | Description |
| :--- | :--- |
| **PENDING** | Job created, but background task hasn't started yet. |
| **RUNNING** | Background task has initiated the scan in the gVisor sandbox. |
| **COMPLETED** | Scan report is successfully written to the PVC. |
| **FAILED** | Scan failed or timed out; error message stored in Redis. |

---

## 3. Technical Implementation

### Step 1: Model Updates
- Update `ScanJobResponse` to include `status` (Enum) and `error` fields.
- Define `ScanStatus` enum in `models.py`.

### Step 2: Backend Logic (`backends.py`)
- Increase the internal `httpx` timeout for the background worker to **600 seconds**.
- This ensures the background thread doesn't time out even if the script is very large.

### Step 3: API Server Refactor (`codeinspectior_api.py`)
- **Background Worker**: Implement `run_background_scan(job_id, payload)` to update Redis and call the backend.
- **Refactored `POST /v1/scan-jobs`**:
    - Generates `job_id`.
    - Sets initial Redis status.
    - Enqueues the worker using `background_tasks.add_task()`.
- **Status Endpoint**: Implement `GET /v1/scan-status/{job_id}` to query Redis.
- **Report Endpoint**: Refactor `GET /v1/scan-jobs/{job_id}/report` to only return data if the Redis state is `COMPLETED`.

---

## 4. Verification & Testing

### Test Flow:
1.  **Submit**: `curl -X POST /v1/scan-jobs` -> Check for `202 Accepted` and `job_id`.
2.  **Poll Status**: `curl -X GET /v1/scan-status/{job_id}` -> Verify transition from `PENDING` to `RUNNING`.
3.  **Fetch Result**: `curl -X GET /v1/scan-jobs/{job_id}/report` -> Verify JSON output once status is `COMPLETED`.

### Success Criteria:
- No `504 Gateway Timeout` errors from Envoy.
- Multiple large scans can be triggered simultaneously without blocking the API dashboard.
- Job status persists in Redis even if the browser is closed.
