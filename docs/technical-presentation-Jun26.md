# Technical Presentation Guide: Platform Engineering & Architectural Milestones
**Date:** June 26, 2026
**Project:** CodeInspector & OpenSandbox Microservices Platform

---

## Executive Summary

This guide outlines the six foundational pillars implemented to elevate the **CodeInspector & OpenSandbox** platforms to a highly secure, observable, fault-tolerant, and horizontally scalable microservices architecture.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        PLATFORM CORE SYNERGY                            │
├───────────────────┬───────────────────┬─────────────────────────────────┤
│    SECURITY       │   OBSERVABILITY   │          SCALABILITY            │
├───────────────────┼───────────────────┼─────────────────────────────────┤
│  • Sealed Secrets │ • Prometheus      │ • RabbitMQ Async Execution      │
│  • Zero-Db Private│   Telemetry       │ • RabbitMQ Async Deletion       │
│    Repo Scanning  │ • Ingress Policies│ • Redis Pub/Sub Coordination    │
│                   │                   │ • Durable Expiry Notification   │
└───────────────────┴───────────────────┴─────────────────────────────────┘
```

---

## 1. Asynchronous Scan Execution Pipeline (RabbitMQ & Server-Sent Events)

### 💡 The Problem
Scanning a repository involves resource-intensive tasks: cloning Git repositories, provisioning isolated containers, running AST parsers, and processing static analysis. If these operations run synchronously within the web request context, HTTP clients will time out, server threads will be exhausted, and the gateway will crash under load.

### ⚙️ How It Works (The Core Principle)
We built an event-driven, decoupled scan execution pipeline. When a user requests a repository scan, the request is validated, enqueued onto a durable message broker, and processed asynchronously by background worker pods. Real-time feedback is streamed back to the client via Server-Sent Events (SSE).

```mermaid
graph TD
    Client[Web Client] -->|1. POST /v1/repo-scan| API[FastAPI Gateway]
    API -->|2. Writes initial state| DB[(PostgreSQL / SQLite)]
    API -->|3. Publishes scan job| RMQ[RabbitMQ Exchange: scan_jobs]
    API -.->|"4. Returns 200 OK (job_id, status: QUEUED)"| Client
    RMQ -->|5. Delivers message| Worker[Background Worker Pod]
    Worker -->|6. Performs Git clone & AST scans| Sandbox[K8s Sandbox Pod]
    Worker -->|7. Pushes progress updates| SSE[SSE Manager]
    SSE -->|8. Streams live status| Client
```

### 🛠️ Detailed Scan Flow for GitHub Repositories
Here is the step-by-step breakdown of how a repository (such as a private or public GitHub repo) is scanned asynchronously:

1. **Ingestion & Validation:**
   * The client submits a `POST /v1/repo-scan` request with the target `repo_url` (and optional credentials like `git_token` or `ssh_key`).
   * The API router parses the URL to extract the `owner` and `repo` names.
   * A unique UUID `job_id` is generated, and a job record is created in the global `job_tracker`.

2. **Durable RabbitMQ Queuing & Resilience Fallback:**
   * The gateway checks broker availability using `is_available()`.
   * **RabbitMQ Online (Primary):** The gateway publishes a message to the `scan.repo` queue containing the job metadata and ephemeral credentials. It immediately returns a `200 OK` response with `status: "QUEUED"` in less than 1 millisecond.
   * **RabbitMQ Offline (Resilient Fallback):** If RabbitMQ is down, the system automatically falls back to registering the job as a FastAPI local `BackgroundTasks` thread, ensuring the system remains functional.

3. **Background Consumption & Execution:**
   * A background consumer worker pod pulls the message from the `scan.repo` queue.
   * The worker first triggers **repository accessibility prechecks** using `validate_github_repo()`.
   * The worker invokes the remote `opensandbox-server` to provision a clean, isolated **workspace directory (sandbox)** with a Persistent Volume Claim (PVC).

4. **Secure In-Memory Cloning:**
   * The worker runs `clone_repo()`. If a private key or token is supplied, it writes the SSH key to a temporary file (restricted with `0600` permissions) or rewrites the git target URL with the PAT token in-memory.
   * It performs a shallow git clone (`--depth=1`) of the GitHub repository directly into the sandbox workspace and deletes all temporary credential files immediately in an unconditional `finally` block.

5. **Parallel Component Scanning:**
   * The worker detects the languages present in the repository.
   * Using Python's `asyncio.gather`, it concurrently triggers language-specific AST tools (e.g. bandit, semgrep, eslint) in separate code-interpreter sandbox pods, processing scans in parallel rather than sequentially.

6. **Progress Streaming (SSE):**
   * Throughout the scan, the worker updates the `job_tracker` with progress increments (`CLONING`, `SCANNING_PYTHON`, `COMPLETED`).
   * The frontend client subscribes to the `/v1/repo-scan/{job_id}/status` endpoint, which streams these progress events live.

---

## 2. Asynchronous Scan Deletion & Cancellation Pipeline

### 💡 The Problem
In a clustered environment, terminating running scan jobs synchronously is slow and unreliable. Directly invoking Kubernetes APIs from HTTP threads blocks the event loop. Furthermore, in a horizontally scaled deployment with multiple replica pods, a cancel request sent to one pod cannot easily stop a scanning thread running on a different pod.

### ⚙️ How It Works (The Core Principle)
We decoupled deletion by routing requests through a durable **RabbitMQ** queue and utilizing **Redis Pub/Sub** for cross-pod coordination and task cancellation.

```mermaid
sequenceDiagram
    autonumber
    actor Client as Client / Script
    participant API as API Server (sandbox-api Router)
    participant RMQ as RabbitMQ (scan.delete Queue)
    participant Handler as delete_handler (Consumer)
    participant Redis as Redis (Pub/Sub + KV)
    participant K8s as Kubernetes (opensandbox-server)

    Client->>API: DELETE /v1/jobs/{job_id}?purge=true
    Note over API: Verifies JWT token & checks RabbitMQ health
    API->>RMQ: Publish message: {"job_id": job_id, "purge": true}
    API-->>Client: Return 200 OK {"status": "DELETE_QUEUED"}

    Note over Handler: Asynchronously consumes from scan.delete
    RMQ->>Handler: Deliver delete task

    rect rgb(240, 245, 255)
        Note over Handler, Redis: Phase 1: Cross-Pod Coordination
        Handler->>Redis: Set key "job:{job_id}:cancelled" = "true"
        Handler->>Redis: Publish to channel "job:deletions" (job_id)
        Redis-->>API: Notify other API pod replicas via Pub/Sub
        Note over API: Cancel active python tasks & purge in-memory tracker
    end

    rect rgb(255, 240, 240)
        Note over Handler, K8s: Phase 2: Kubernetes Sandbox & PVC Cleanup
        Handler->>K8s: DELETE /api/v1/01sbx/scan-jobs/{job_id}?terminate=true
        Note over K8s: opensandbox-server kills sandbox pods and purges PVC workspaces
        Handler->>Handler: Call cleanup_child_jobs() to terminate all child language pods
        Handler->>K8s: DELETE /api/v1/01sbx/scan-jobs/{child_job_id}?terminate=true
    end

    rect rgb(240, 255, 240)
        Note over Handler, Redis: Phase 3: Final State Purge
        Handler->>Redis: Delete all Redis keys: job:{job_id}:status, job:{job_id}:events, etc.
        Handler->>Handler: Purge from local job tracker
    end
```

### 📢 Simple Analogy: The "Megaphone" & "Whiteboard"
* **RabbitMQ Whispers to One Guard:** RabbitMQ is a dispatcher who whispers to **Guard A** (Pod A) only: *"Cancel and delete Job #123."* But **Guard B** (Pod B) is the one actually running it.
* **The Megaphone (Redis Pub/Sub):** Guard A picks up a **megaphone (Redis Pub/Sub)** and shouts: **"Attention all guards! Cancel and delete Job #123!"** Guard B hears this shout and terminates the scan immediately.
* **The Whiteboard (Redis Key-Value):** Simultaneously, Guard A writes *"Job #123 is cancelled"* on a **central whiteboard (Redis Key-Value store)**. If any guard is about to start Job #123 in the future, they check the whiteboard first and abort.

### 📁 Key Code References
* **Publisher:** [`scan_jobs/router.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/scan_jobs/router.py) -> `cancel_or_delete_job()` (Queues deletions and returns `DELETE_QUEUED` in <1ms).
* **Consumer Handler:** [`core/queue/delete_handler.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/core/queue/delete_handler.py) -> `handle_delete_job()` (Coordinates Redis cancellation, schedules Kubernetes teardown in thread pools via `asyncio.to_thread`, and cleans up child language sandbox pods).
* **Cross-Pod Thread Listener:** [`core/queue/cancellation.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/core/queue/cancellation.py) -> `setup_cancellation_listener()` & `cancel_active_task()` (Subscribes to channels, receives the broadcasted `job_id`, and stops the local asyncio Task).
* **Child Pod Cleanup:** [`scan_repository/file_scanner.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/scan_repository/file_scanner.py) -> `cleanup_child_jobs()` (Collects all spawned child language scan job IDs and terminates them concurrently on Kubernetes).

---

## 3. Prometheus Telemetry & Observability Engine

### 💡 The Problem
A production gateway requires deep operational visibility. Administrators need to track HTTP latencies, background worker queue congestion, active database connections, sandbox allocations, and authentication failures without degrading API performance.

### ⚙️ How It Works (The Core Principle)
We built a dual-registry Prometheus telemetry collector exposed at `/metrics` that combines **Push-Style Accumulation** (counters and histograms updated inline with requests) and **Pull-Style Dynamic Instrumentation** (gauges computed asynchronously right at scrape time).

```
[Prometheus Server]
      │ (Pulls every 15s)
      ▼
┌────────────────────────────────────────────────────────┐
│ /metrics Endpoint (FastAPI)                            │
├───────────────────────────┬────────────────────────────┤
│  In-Memory Accumulators   │   Dynamic Scrape Logic     │
├───────────────────────────┼────────────────────────────┤
│ • http_requests_total     │ • queue_depth_jobs         │
│ • auth_failures_total     │   (Ephemerally queries     │
│ • db_connections_active   │    RabbitMQ via passive    │
│ • repo_clone_duration     │    declarations)           │
└───────────────────────────┴────────────────────────────┘
```

### 🛠️ Key Architectural Details
* **Database Connection Tracking:** Utilizes a proxy wrapper pattern (`InstrumentedConnection` in `core/app_state.py`). On open, `db_connections_active.inc()` is called; on close, it decrements, preventing connection leaks.
* **Authentication Failure Classification (`auth_failures_total`):** Categorizes rejects into highly specific error labels (e.g., `expired_key`, `revoked_key`, `deactivated_key`, `missing_kid`, `identity_mismatch`), simplifying security auditing.
* **Live RabbitMQ Queue Monitoring:** Rather than maintaining active counts, the `/metrics` endpoint dynamically connects to RabbitMQ at scrape-time, issues a non-destructive **passive declaration** (`declare_queue(passive=True)`) against active queues, and extracts their exact queue depth on the fly.
* **Gatekeeper Ingress Security:** To prevent leaking internal infrastructure telemetry, `/metrics`, Grafana, and Prometheus routes are protected via custom `AgentgatewayPolicy` rules, restricting access to localhost, the VPN subnet (`10.x.x.x`), and approved administrator IPs defined in `values.yaml`.

---

## 4. Secure Private Repository Scanning (Zero-Db Persistence)

### 💡 The Problem
Scanning private Git repositories (GitHub, GitLab, Bitbucket) requires sensitive credentials (Personal Access Tokens or SSH Private Keys). Storing these credentials in a database creates a massive security vulnerability and increases compliance burdens.

### ⚙️ How It Works (The Core Principle)
We designed a **Zero-Persistence Credential Pipeline**. Credentials are never written to disk or Postgres. Instead, they flow ephemerally in-memory through the request payload, propagate inside the encrypted RabbitMQ message queue, are used in-memory / in temporary files during cloning, and are immediately discarded.

```
Request Payload (PAT / SSH Key) ──► FastAPI Request (RAM) ──► RabbitMQ (Encrypted Queue)
                                                                       │
┌─────────────────────────────── Temporary Files (RAM) ◄───────────────┘
│  • HTTPS Token rewrite
│  • Temp 0600 SSH key file
▼
[git clone inside Sandbox] ──► Immediate finally block cleanup (Zero Trace)
```

### 🛠️ Key Architectural Details
* **Modularization (`private_clone.py`):** Encapsulates all private credential handling, validated URL parsing, and authenticated git command execution.
* **Universal Git Validation:** Uses `git ls-remote` to validate credentials and repository accessibility. This bypasses the need to maintain provider-specific REST APIs (OAuth/GitHub APIs) and works universally for GitHub, GitLab, and Bitbucket.
* **Subprocess Log Scrubbing:** Implements string sanitizers that scrub raw PAT tokens or SSH keys from all subprocess standard outputs, standard errors, and exception tracebacks before logging to stdout or returning errors to the user.
* **Cloning Teardown:** Writes SSH private keys to ephemeral `0600` permission files, configures `GIT_SSH_COMMAND`, executes the clone directly into the isolated sandbox, and guarantees the deletion of the credential file via an unconditional `finally` execution block.

---

## 5. Asynchronous API Key Expiry Warning Pipeline

### 💡 The Problem
If a developer's API key expires without warning, automated CI/CD pipelines and scanning integrations fail suddenly, locking the team out of operations. We need a fault-tolerant, non-blocking warning system.

### ⚙️ How It Works (The Core Principle)
A lightweight background service continuously scans the database for expiring keys, publishes warnings to a durable RabbitMQ queue, and worker consumers dispatch warning emails via the SendGrid Web API with resilient retry backoffs.

```
┌──────────────────────────────────────┐
│  Background Expiry Checker (10s)     │
└──────────────────┬───────────────────┘
                   │ (Query DB for expiring keys)
                   ▼
┌──────────────────────────────────────┐
│  RabbitMQ Durable Exchange           │
└──────────────────┬───────────────────┘
                   │ (Publish persistent warning message)
                   ▼
┌──────────────────────────────────────┐
│  notification.email Queue            │
└──────────────────┬───────────────────┘
                   │ (Explicit Worker Acknowledgment)
                   ▼
┌──────────────────────────────────────┐
│  Worker Consumer -> SendGrid Web API │
└──────────────────┬───────────────────┘
                   ├───────────────────┐
         (Success) │         (Failure) │ (Retry Backoff: 5s -> 30s -> 2m)
                   ▼                   ▼
               [msg.ack()]    [Dead Letter Queue (DLQ)]
```

### 🛠️ Key Architectural Details
* **Resolved User Mapping:** During key generation, the API router extracts the user's email dynamically from Auth0 JWT claims (namespaced or standard) and stores it directly alongside the key in the database.
* **Lead Time Notification Logic:** Implements smart interval logic to prevent spamming users:
  * Key TTL ≤ 10 mins: Warns at **≤ 3 mins** remaining.
  * 10 mins < TTL ≤ 1 hr: Warns at **≤ 10 mins** remaining.
  * 1 hr < TTL ≤ 24 hrs: Warns at **≤ 1 hr** remaining.
  * 24 hrs < TTL ≤ 7 days: Warns at **≤ 12 hrs** remaining.
  * TTL > 7 days: Warns at **≤ 24 hrs** remaining.
* **At-Least-Once Delivery Guarantees:**
  * Messages are published as `PERSISTENT` so they survive RabbitMQ broker crashes.
  * Workers use **explicit acknowledgments (`msg.ack()`)**. If SendGrid is throttled or times out, the message is routed through exponential backoff delay queues. If all retries fail, it lands in a Dead Letter Queue (DLQ) for manual administrator recovery, ensuring no notifications are silently dropped.

---

## 6. Kubernetes GitOps Secrets Management (Sealed Secrets)

### 💡 The Problem
In a GitOps continuous deployment pipeline, all Kubernetes manifests are stored in a public or private Git repository. Committing plain-text `Secrets` (containing database passwords, SendGrid API keys, and private keys) to Git is a critical security violation.

### ⚙️ How It Works (The Core Principle)
We integrated **Bitnami Sealed Secrets**. Sensitive credentials are encrypted locally using the target cluster's public certificate. The resulting `SealedSecret` manifest is completely safe to commit to Git. Only the Sealed Secrets controller running inside the target cluster holds the private key required to decrypt it back into a standard Kubernetes `Secret`.

```
[Plain-Text Secret]
       │
       │ (Encrypted locally via kubeseal + pub-cert.pem)
       ▼
[SealedSecret Manifest] ──► (Committed safely to Git) ──► [K8s Target Cluster]
                                                                  │
┌─────────────────────────────────────────────────────────────────┘
│ (Decrypted on-cluster via Sealed Secrets Controller Private Key)
▼
[Standard Kubernetes Secret] ──► Mounted as Env Vars in Pods (API / Workers)
```

### 🛠️ Key Architectural Details & Resolutions
* **CRD Helm Hook Fix:** Helm by default does not upgrade Custom Resource Definitions (CRDs) during updates. We manually registered the `SealedSecret` CRD on the cluster and embedded it inside the `codeInspector/crds/` directory to guarantee seamless, out-of-the-box installations in new environments.
* **Empty Field Decryption Failures Resolved:** The Sealed Secrets controller fails and halts decryption if it encounters empty strings in `spec.encryptedData`. We refactored the Helm template to conditionally render keys only if they contain non-empty encrypted strings, preventing deployment timeouts.
* **Disaster Recovery Strategy:** Established a strict backup routine for the controller's active private key (`sealedsecrets.bitnami.com/sealed-secrets-key=active`). This key is backed up to a secure vault outside of Git. If the cluster is destroyed, restoring this key allows the new cluster to immediately decrypt the existing `SealedSecrets` committed in Git without re-encrypting them.

---

## 7. Summary: Unified Architectural Value

Together, these six pillars form a highly cohesive production platform:

1. **Scalable Execution:** The Asynchronous Scan Execution Pipeline runs jobs non-blocking, scaling horizontally via RabbitMQ and parallelizing tools using `asyncio.gather`.
2. **Cluster Cleanliness:** Asynchronous Scan Deletion tears down Kubernetes sandboxes and wipes database/cache history without lockups.
3. **Security:** Sealed Secrets secures the deployment configs, while the Zero-Db Private Scanner ensures credentials never linger on the server.
4. **Observability:** The Prometheus engine keeps administrators informed of API load, database health, and worker queue depths in real-time, secured behind gateway ingress policies.
5. **Fault Tolerance:** Asynchronous pipelines (cancellations and email notifications) ensure that slow operations or third-party API outages never block the core platform, with durable queues guaranteeing that no task or alert is ever lost.
