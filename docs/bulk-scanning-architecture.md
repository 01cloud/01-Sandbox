# Bulk Scanning Architecture: Asynchronous Polling

## Executive Summary
This document details the architectural shift and technical implementation for the Bulk Security Scanning feature within the 01 Sandbox platform.

Initially, the bulk scan engine relied on **synchronous, blocking HTTP requests**. This caused critical failures (`504 Gateway Timeout`) when processing large volumes of concurrent scans, as edge load balancers and proxy ingress controllers (like Cloudflare and Nginx) forcefully dropped connections that remained open longer than 60-120 seconds while waiting for backend Kubernetes pods to complete the scanning jobs.

To permanently resolve this while maintaining the ability to aggressively queue tasks every 2 seconds, the architecture was refactored into a **non-blocking asynchronous polling model**.

---

## 1. Architectural Changes

### 1.1 API Server Interception (`apiServer/fastapi/codeinspectior_api.py`)
To bypass the proxy timeouts, the API gateway was modified to intercept bulk scan requests and run them in a background thread.
- **Route Interception:** A new endpoint `create_scan_job_alias` was introduced explicitly to intercept requests matching `/api/{version}/{backend_id}/scan-jobs`. This prevents the generic `dynamic_versioned_proxy` from blindly passing the blocking request to the `opensandbox-server`.
- **Background Tasks:** FastAPIs `BackgroundTasks` module was implemented. When a client requests a scan with the query parameter `?async=true`, the API server immediately spawns a background coroutine (`run_scan_in_background`) that holds the connection to the internal `opensandbox-server` backend.
- **Instant Response:** The API instantly returns a HTTP 200 JSON payload: `{ "job_id": "<uuid>", "status": "PROCESSING" }`. This allows the client to close the HTTP connection within milliseconds, completely bypassing the load balancer timeout limitations.

### 1.2 Graceful Polling Backend (`apiServer/fastapi/backends.py`)
Because the scan runs in the background, the client must poll the backend for the generated report.
- **404 Handling:** The `get_scan_report` method was updated to gracefully handle `HTTP 404 Not Found` responses from the internal OpenSandbox PVC (which occur while the scan is still running and the report file is not yet written).
- Instead of crashing with an unhandled `HTTPStatusError` (resulting in a generic 500 error), it properly returns a `404 HTTPException`, signaling to the client that the report is legitimately "Not Ready Yet".

### 1.3 React Frontend Orchestrator (`z1sandbox-website/src/pages/Dashboard.tsx`)
The React dashboard orchestrator (`runBulkSecurityAudit`) was heavily refactored to support concurrent polling.
- **Async Dispatch:** Bulk queue items are now dispatched using `fetch(..., /scan-jobs?async=true)`. This guarantees an immediate response without hanging the UI.
- **Non-Blocking Polling Loop:** Upon receiving the `job_id`, the orchestrator spawns an isolated background Promise containing a `while(true)` loop.
- **5-Second Ping:** The loop hits the `/scan-jobs/{job_id}/report` endpoint every 5 seconds.
  - If it receives a `404`, it sleeps and retries.
  - If it receives a `200`, it parses the report, updates the UI (to `risks` or `clean`), and breaks the loop.
- **Concurrent Pacing:** The initial job submissions are still spaced precisely 2 seconds apart to pace backend resource utilization, but the subsequent polling loops run entirely concurrently using `Promise.all()`.

### 1.4 Rate Limit Adjustments (`codeInspector/charts/apiServer/values.yaml`)
Because the async architecture allows the frontend to successfully queue jobs at a rapid pace (1 job every 2 seconds = 30 jobs per minute), the previous sliding-window rate limit was consistently exceeded.
- **RATE_LIMIT_REQUESTS:** Increased the global rate limit threshold from `7` requests per minute to `100` requests per minute.
- **429 Handling:** The frontend was updated to dynamically read the `retry-after` header from the backend when a `429 Rate Limit` is hit, pausing the submission queue until the cooldown expires.

---

## 2. Sequence Flow

1. **Client** (Dashboard) triggers Bulk Scan.
2. **Client** executes POST `/api/v1/01sbx/scan-jobs?async=true`.
3. **apiServer** (FastAPI) intercepts the request via the alias route.
4. **apiServer** generates a `job_id` and adds `state.backend.create_scan_job` to a background thread.
5. **apiServer** immediately returns `{"status": "PROCESSING", "job_id": "..."}` to the Client (within <100ms).
6. **apiServer Background Thread** makes a blocking call to internal `opensandbox-server`.
7. **Client** sleeps for 5 seconds, then issues GET `/api/v1/01sbx/scan-jobs/{job_id}/report`.
8. **apiServer** checks PVC storage via `opensandbox-server`. If not found, returns `404 Not Found`.
9. **Client** receives 404, sleeps 5 seconds, and repeats (Step 7).
10. **apiServer Background Thread** finishes, and `opensandbox-server` writes the report to the PVC.
11. **Client** issues next GET request.
12. **apiServer** finds the report, returns `200 OK` with full JSON payload.
13. **Client** completes the task and updates the UI status.
