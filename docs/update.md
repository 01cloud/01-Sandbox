# Daily Update: Sandbox Queue & Scanning Reliability Fixes (date: June 23 2026)

Here is the summary of the key issues resolved and tasks completed today:

### 1. Fixed Private Repository Scanning Prompt Failure
* **Issue**:
  - Checking a private repository (e.g. `enzokamal/kamalopensandbox`) returned `remote: Write access to repository not granted. fatal: unable to access ... returned error: 403` when run unauthenticated.
  - This 403 error did not match the existing list of auth-related keywords in the backend. As a result, the backend returned `requires_auth: false`, the frontend did not display the PAT/SSH key inputs, and scans were submitted unauthenticated and failed.
* **Fix**:
  - Expanded `auth_indicators` in `private_clone.py` to match `"403"`, `"forbidden"`, `"write access"`, and `"unauthorized"` errors.
  - The UI now correctly prompts for credentials, allowing successful scans of private repositories using Personal Access Tokens (PATs) or SSH Deploy Keys.

### 2. Implemented Dynamic Warning Lead Times for API Key Expiration
* **Issue**:
  - The email notification system used a static warning threshold, which failed to adapt to very short test keys (e.g., a 4-minute key TTL) or very long production keys.
* **Fix**:
  - Replaced the static threshold with a dynamic, tiered warning logic in `expiry_checker.py`. Short-lived test keys (TTL <= 10m) trigger alerts with a 3-minute warning lead time, scaling up to 24 hours for keys with a TTL greater than 7 days.
  - Added timezone parsing typeguards to safely support both string and datetime formats containing timezone offsets (`Z`, `+00:00`).

### 3. Diagnosed "Broker Offline" Dashboard Alert
* **Issue**:
  - The developer dashboard displayed a generic `Broker Offline` message on the Queue Monitor tab.
* **Diagnosis**:
  - The RabbitMQ broker was fully functional, but the API endpoint `/v1/queue/stats` returned a `403 Forbidden` error because the logged-in Auth0 user had zero active API keys registered in the database, triggering the Identity Bridge's security validation.
  - Generating an API key in the **API Management** tab resolves the error and restores stats visibility.

---

# Daily Update: Sandbox Queue & Scanning Reliability Fixes (date: June 22 2026)

Here is the summary of the key issues resolved and tasks completed today:

### 1. Resolved Stuck Scan Queues, Pod Restarts, and CORS Errors
* **Issue**:
  - The RabbitMQ consumer crashed instantly on startup with an `ImportError` due to a missing import reference in `observability/__init__.py`. This caused all newly submitted scan jobs to get stuck in the `QUEUED` state indefinitely.
  - The API's single-threaded FastAPI event loop was blocked by synchronous PostgreSQL connection calls on every authenticated request and by sequential, synchronous Redis scans (`keys("job:*:status")` and 4 individual calls per job) when listing jobs.
  - Due to these blockages, pods failed Kubernetes liveness probes (timing out after 5 seconds), leading to continuous container restarts and resulting in `503 Service Unavailable` CORS policy failures in the UI.
* **Fix**:
  - Fixed the missing import in the `observability` package to restore the queue consumer.
  - Implemented a 60-second Redis cache (`user_keys:{user_id}`) for developer key queries to bypass PostgreSQL lookup overhead.
  - Refactored `list_repo_scan_jobs` using a Redis Pipeline to retrieve all job details in a single batch round-trip instead of serial requests.
  - Added unit tests to verify the cache and DB fallback behaviors.

---

## Daily Update: Sandbox Queue & Scanning Reliability Fixes (date: June 11 2026)

Here is the summary of the key issues resolved and tasks completed on June 11:

### 1. Fixed "Broker Offline" Bug (Startup Race Condition)
* **Issue**: During Helm upgrades and restarts, the `sandbox-api` container starts in parallel with the `rabbitmq` container. Since RabbitMQ takes ~15 seconds to fully boot and listen on port `5672`, the API's initial connection attempt failed. The API server caught the exception and locked itself into a fallback offline mode permanently, causing the UI dashboard to display `Broker Offline`.
* **Fix**: Implemented a connection retry loop in `connect_rabbitmq()` that attempts connection up to 10 times with a 3-second delay, waiting up to 30 seconds for RabbitMQ to become ready.
* **Release Config**: Bumped the API image tag in Helm values to `v0.5.67`.

### 2. Resolved Concurrency Failures under Parallel Bulk Scans (20+ repos)
* **Issue**: Launching many scans concurrently on single-node servers caused the Kubernetes scheduler to run out of allocatable CPU. Sandbox runner pods got stuck in `Pending` with scheduling warnings: `FailedScheduling: 0/1 nodes are available: 1 Insufficient cpu`.
* **Fix**: Disabled the Horizontal Pod Autoscaler (`hpa.enabled: false`) and capped replicas to `2` to align with physical CPU limits. Excess scan requests now wait securely inside RabbitMQ queue buffers rather than overloading the cluster, preventing false timeouts.

### 3. New Queue Telemetry Dashboard (UI Metrics Integration)
* **Feature**: Added a live RabbitMQ Queue Telemetry Dashboard directly into the web interface.
  * **Metrics Displayed**: Real-time tracking of queue depth, active consumer counts, and rolling throughput (processed messages/sec) for the `scan.quick`, `scan.repo`, and `scan.failed` (DLQ) queues.
  * **Routing**: Registered the public, unauthenticated frontend route `/queue-stats` in `App.tsx` matching the public backend stats endpoint, as well as the authenticated stats tab.
  * **Layout Padding Fixes**: Standardized top padding on the containers of both `QueueStatsPage.tsx` and `Health.tsx` (from `py-12` to `pt-32 pb-12`) to prevent the floating navigation header from overlapping dashboard cards.

### 4. Enterprise Client Presentation & Capacity Sizing Kit
* **Sizing Guide**: Created `docs/sizing/sizingformula.md` detailing the formula to calculate safe replica counts and prefetch limits based on available server specs ($R \times P \le N$).
* **Live Simulator**: Built a CLI simulator (`apiServer/fastapi/demo_rabbitmq.py`) to show client-facing animations of 20 concurrent jobs queueing up, worker prefetch throttling, and DLQ routing.
* **Demo Guide**: Created `docs/rabbitmq/client_demo_script.md` containing the client presentation pitch, walkthrough steps, and mermaid flow chart.

---

# Daily Update: Sandbox Queue & Scanning Reliability Fixes (date: June 10 2026)

Here is the summary of the key issues resolved and tasks completed today:

### 1. Fixed Deleted Jobs Reappearing in UI (Multi-Pod Desync)
* **Issue**: Purging a job from the UI removed it from Redis and one API pod, but other active pods in the cluster still kept the job in their local memory cache. This caused the job to reappear when the UI polled list endpoints hosted by those desynchronized pods.
* **Fix**: Implemented a Redis Pub/Sub broadcast channel (`job:deletions`). When a job is deleted, all pods receive the event and instantly evict the job from their in-memory tracker, ensuring consistent UI deletion.

### 2. Resolved GitHub API Rate Limit (403) Blockers
* **Issue**: Scan requests failed with `403 Forbidden` if the unauthenticated public GitHub API rate limit (60 requests/hour per IP) was exhausted.
* **Fix**: Updated validation logic to fallback to local regex-based URL validation if the GitHub API is rate-limited or temporarily unreachable, allowing the scan pipeline to continue.

### 3. Decoupled Validator to Prevent Submission Timeouts (30s timeouts under load)
* **Issue**: Parallel submissions (20+ concurrent scans) hit a client-side 30-second read timeout. This happened because the server made slow external GitHub API calls synchronously inside the HTTP request handler, bottlenecking the event loop.
* **Fix**: Separated validation into two phases:
  * **Request Time (Instant)**: Parses the URL structure locally and responds with `200 OK / QUEUED` immediately (in under 10ms).
  * **Background Time (Async)**: Executes the external GitHub API validation asynchronously in the worker queue, removing it entirely from the critical request path.

### 4. Technical Guide: RabbitMQ Job Retry and DLQ Mechanism
* **How it works**:
  * **Transient Failures (Retry Loop)**: If a scan fails due to a network glitch, timeout, or sandbox crash, the queue worker intercepts the error and routes the job to a delay queue with an increasing TTL (5s, 30s, 2m). Once the TTL expires, RabbitMQ's Dead-Letter Exchange (DLX) re-queues it back to the main queue for processing.
  * **Permanent Failures (No Retry)**: User input validation errors (e.g. non-existent/private repositories) fail immediately without retrying to avoid wasting server resources.
  * **Dead Letter Queue (DLQ)**: If a job fails all 3 retry attempts, it is permanently rejected and routed to the `scan.failed` queue (DLQ) for manual inspection and debugging.
