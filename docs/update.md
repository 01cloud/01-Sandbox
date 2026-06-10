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
