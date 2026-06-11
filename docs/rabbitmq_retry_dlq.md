# RabbitMQ Retry with Exponential Backoff & Dead Letter Queue (DLQ)

This document explains the architecture and working principles of the RabbitMQ queue topology, retry/backoff mechanism, and Dead Letter Queue (DLQ) implemented in the Sandbox application.

---

## 1. Queue & Exchange Topology

The system defines three direct exchanges and their corresponding queues to isolate active job execution, retry delays, and permanent failures:

```
                  ┌────────────────────────┐
                  │       Publisher        │
                  └───────────┬────────────┘
                              │ Route Job
                              ▼
                  ┌────────────────────────┐
                  │     Main Exchange      │
                  │      (scan_jobs)       │
                  └──────┬──────────┬──────┘
            scan.quick   │          │   scan.repo
                         ▼          ▼
                  ┌──────────┐  ┌──────────┐
                  │  Active  │  │  Active  │
                  │  Queue   │  │  Queue   │
                  │(scan.qui)│  │(scan.rep)│
                  └────┬─────┘  └────┬─────┘
                       │ Consume     │ Consume
                       ▼             ▼
                  ┌────────────────────────┐
                  │     Worker Process     │
                  └────┬────────────┬──────┘
          1. Fail      │            │ 4. Exceeds Max
          (under limit)│            │ (Attempt > 3)
                       ▼            ▼
   ┌────────────────────────┐  ┌────────────────────────┐
   │     Retry Exchange     │  │  Dead Letter Exchange  │
   │   (scan_jobs.retry)    │  │   (scan_jobs.dlx)      │
   └───────────┬────────────┘  └───────────┬────────────┘
               │                           │
  scan.quick.5s│ (based on retry count)    │ scan.failed
               ▼                           ▼
   ┌────────────────────────┐  ┌────────────────────────┐
   │     Delay Queues       │  │   Dead Letter Queue    │
   │ scan.retry.5s (5s TTL) │  │     (scan.failed)      │
   │ scan.retry.30s(30s TTL)│  └────────────────────────┘
   │ scan.retry.2m (2m TTL) │
   └───────────┬────────────┘
               │ TTL Expires
               │ (Dead-Letter back)
               ▼
      (Main Exchange)
```

### Exchanges:
* **`scan_jobs`** (Main Exchange): A `direct` exchange that routes new active job payloads to `scan.quick` (Quick Scans) or `scan.repo` (Repository Scans).
* **`scan_jobs.retry`** (Retry Exchange): A `direct` exchange that routes failed jobs into intermediate delay queues based on the retry attempt.
* **`scan_jobs.dlx`** (Dead Letter Exchange): A `direct` exchange that captures permanently rejected messages and routes them to the DLQ (`scan.failed`).

### Queues:
* **Active Queues (`scan.quick`, `scan.repo`)**: Configured with the arguments:
  * `"x-dead-letter-exchange": "scan_jobs.dlx"`
  * `"x-dead-letter-routing-key": "scan.failed"`
* **Delay Queues (`scan.retry.5s`, `scan.retry.30s`, `scan.retry.2m`)**: Queues with **no active consumers**. They hold messages temporarily and are configured with:
  * `"x-message-ttl"`: The delay duration in milliseconds (5,000 / 30,000 / 120,000 ms).
  * `"x-dead-letter-exchange"`: Pointing back to the main exchange (`scan_jobs`).
* **Dead Letter Queue (`scan.failed`)**: A durable queue where failed or unprocessable messages reside permanently for manual diagnostics.

---

## 2. Detailed Message Lifecycle on Failure

### Phase 1: Execution Failure
When a worker consumes a job from `scan.quick` or `scan.repo` and encounters an exception during execution:
1. The exception is caught and passed to the `handle_worker_failure(msg, payload, error, routing_key, app_state)` handler.
2. The `retry_count` in the message payload is incremented.

### Phase 2: Backoff & Re-routing (Attempts 1 - 3)
If the `retry_count` is **3 or less**:
1. The backoff duration is determined based on the current retry count:
   * **Attempt 1**: 5-second backoff. Routing key = `{routing_key}.5s` (e.g., `scan.quick.5s`).
   * **Attempt 2**: 30-second backoff. Routing key = `{routing_key}.30s` (e.g., `scan.quick.30s`).
   * **Attempt 3**: 2-minute backoff. Routing key = `{routing_key}.2m` (e.g., `scan.quick.2m`).
2. The UI state for the job is updated to `RETRYING` along with the current attempt details.
3. The modified payload is published to the **Retry Exchange (`scan_jobs.retry`)** using the designated routing key.
4. The worker acknowledges the original message (`msg.ack()`), cleanly removing it from the active queue.

### Phase 3: Delay and Dead-Lettering (TTL)
1. The message lands in the corresponding delay queue (e.g., `scan.retry.5s`).
2. Since there are no consumers on this queue, the message remains there until the TTL expires.
3. Once the TTL is reached, RabbitMQ automatically dead-letters the message back to the exchange configured in `"x-dead-letter-exchange"` (the main `scan_jobs` exchange) using the message's original routing key.
4. The message enters the active queue (`scan.quick` or `scan.repo`) again, and a worker consumes it for a retry.

### Phase 4: Exhaustion & Routing to DLQ (Attempts > 3)
If the job fails for a 4th time:
1. The system determines that the retry limit has been exceeded.
2. The UI status is updated to `ERROR` with the final error message.
3. The worker rejects the message by calling `msg.reject(requeue=False)`.
4. RabbitMQ intercepts the rejected message and automatically forwards it to the **Dead Letter Exchange (`scan_jobs.dlx`)** which places it in the **Dead Letter Queue (`scan.failed`)**.
5. The message is stored in `scan.failed` indefinitely and does not clog active worker queues.

---

## 3. How to Test Retries & DLQ

You can test the retry and Dead Letter Queue (DLQ) topology in a live environment using the built-in simulation hook.

### Step 1: Submit a Scan Job with Simulation Keyword
Submit a scan request with a URL containing the string `simulate_retry` or `simulate_error`.

* **Via the React UI**: Enter `https://github.com/simulate/demo-repo` in the scan input.
* **Via cURL / REST API**:
  ```bash
  curl -X POST https://api-sandbox.01security.com/v1/repo-scan \
    -H "Authorization: Bearer <your_api_key>" \
    -H "Content-Type: application/json" \
    -d '{"repo_url": "https://github.com/simulate/demo-repo"}'
  ```

### Step 2: Monitor UI Progress
The job will fail immediately upon starting, initiating the retry sequence:
1. **First Failure**: Enters 5s delay queue. Status becomes `RETRYING` with message: `Retry 1/3 (backing off)`.
2. **Second Failure**: Enters 30s delay queue. Status becomes `RETRYING` with message: `Retry 2/3 (backing off)`.
3. **Third Failure**: Enters 2m delay queue. Status becomes `RETRYING` with message: `Retry 3/3 (backing off)`.
4. **Final Failure**: Exceeds limits. Status becomes `ERROR` with message: `Scan failed after 3 retries`.

### Step 3: Verify the Dead Letter Queue (DLQ)
Once the job transitions to `ERROR`, RabbitMQ rejects the message and places it in the DLQ (`scan.failed`). Run the following command inside your RabbitMQ container or service to verify the message count:
```bash
rabbitmqctl list_queues | grep scan.failed
```

---

## 4. Retriable vs. Non-Retriable Errors (Validation vs. Execution)

When testing retry behavior, it is important to distinguish between **transient execution failures** and **permanent input/validation failures**:

### A. Non-Retriable Errors (Permanent Validation Failures)
* **Example**: Submitting a private or non-existent repository like `https://github.com/agentgateway/kamal`.
* **Behavior**: The background worker contacts the GitHub API and receives a `404 Not Found` or `403 Forbidden` (private repo check).
* **Rationale**: Because a repository being private or non-existent is a permanent condition, retrying the check multiple times would waste worker queue resources and delay displaying the final status. Thus, the system is designed to **fail immediately** and transition the job status to `ERROR` without retrying.

### B. Retriable Errors (Transient/Simulation Failures)
* **Example**: Submitting a URL using the simulation keyword `https://github.com/simulate/kamal`.
* **Behavior**: The background worker bypasses the external GitHub accessibility check (preventing a 404 validation error) and goes straight into the scanning phase where it intentionally throws a simulated runtime error.
* **Rationale**: This simulates an infrastructure or transient runtime failure (e.g., database timeout, runner crash, or network glitch). Such failures are highly likely to resolve upon retry, triggering the exponential backoff sequence (`5s` -> `30s` -> `2m`).

### C. Real-World Transient/Execution Failures (Automatically Retried)
Apart from simulation keys, any unexpected error during the execution of a real repository scan will automatically trigger retries and the DLQ flow:
* **Sandbox Provisioning Timeout**: If the Kubernetes or docker sandbox environment fails to provision or times out (60-second limit).
* **Git Clone Failures**: If Git fails to clone the repository due to transient network drops or repository server issues.
* **Language Detection Failures**: If the language analyzer fails or crashes during profiling.
* **Overall Scan Timeout**: If the overall execution exceeds the 5-minute container/job processing limit.
* **Service Crash**: Any unhandled runtime error from the underlying scanning tools (Semgrep, Trivy, Enry).

In all these real-world failure cases, the worker will catch the error, log a execution failure, retry up to 3 times with exponential backoff, and ultimately route the message to the DLQ (`scan.failed`) if all attempts fail.
