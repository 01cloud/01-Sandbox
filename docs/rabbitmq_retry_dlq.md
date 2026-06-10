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
