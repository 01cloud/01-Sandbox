# 01Sandbox: Single-Cluster Architecture & Complete Working Principle

## 1. Executive Summary

**01Sandbox** (incorporating **CodeInspector** and **OpenSandbox**) is an enterprise-grade, Kubernetes-native sandbox code execution and security scanning platform. It provides a hardened, auditable, and horizontally scalable environment for:
1. **Executing Untrusted Code Snippets:** Running polyglot code (Python, JavaScript, Bash, etc.) inside isolated containerized runtimes with strict resource and network controls.
2. **Asynchronous Repository Scanning:** Performing deep Abstract Syntax Tree (AST) static code analysis and vulnerability scanning on full Git repositories without risking host node security or persisting client credentials.

In a **Single-Cluster Deployment**, all control plane services, ingress proxies, message brokers, caching nodes, and sandbox execution workers co-exist within a single Kubernetes cluster operating across dedicated namespaces (`agentgateway-system`, `opensandbox-system`, and `codeinspector`).

---

## 2. Platform Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       SINGLE-CLUSTER ARCHITECTURE                                      │
├────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                        │
│  [ External Clients / Web UI (z1sandbox-website) ]                                                      │
│                         │                                                                              │
│                         ▼ (HTTPS / HTTP)                                                               │
│ ┌───────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│ │ Namespace: agentgateway-system                                                                    │  │
│ │   • AgentGateway (Envoy Ingress Proxy)                                                            │  │
│ │   • Edge Auth (CEL Cookie-to-JWT conversion)                                                      │  │
│ │   • Local Rate Limiting & DoS Protection                                                          │  │
│ │   • Gateway API HTTPRoute & AgentgatewayPolicy                                                    │  │
│ └─────────────────────────────────┬─────────────────────────────────────────────────────────────────┘  │
│                                   │ Cross-Namespace Forwarding (ReferenceGrant)                        │
│                                   ▼                                                                    │
│ ┌───────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│ │ Namespace: opensandbox-system                                                                     │  │
│ │                                                                                                   │  │
│ │  ┌─────────────────────────────────────────────────────────────────────────────────────────────┐  │  │
│ │  │ API Gateway / Control Plane (apiServer / FastAPI)                                           │  │  │
│ │  │   • Auth0 JWT Validator    • Job Tracker             • Prometheus Dual-Registry (/metrics)   │  │  │
│ │  │   • Backend Router         • Local Background Tasks  • API Key Expiry Checker                  │  │  │
│ │  └─────────────┬──────────────────────────────┬──────────────────────────────┬─────────────────┘  │  │
│ │                │                              │                              │                    │  │
│ │                ▼                              ▼                              ▼                    │  │
│ │   ┌──────────────────────────┐   ┌──────────────────────────┐   ┌──────────────────────────┐      │  │
│ │   │ RabbitMQ Broker          │   │ Redis Cluster            │   │ PostgreSQL Database      │      │  │
│ │   │  • scan.repo             │   │  • Key-Value Status      │   │  • User Accounts         │      │  │
│ │   │  • scan.delete           │   │  • Pub/Sub ("Megaphone") │   │  • API Keys Metadata     │      │  │
│ │   │  • notification.email    │   │  • Event Logs Cache      │   │  • Audit Logs            │      │  │
│ │   └────────────┬─────────────┘   └────────────┬─────────────┘   └──────────────────────────┘      │  │
│ │                │                              │                                                   │  │
│ │                ▼                              │                                                   │  │
│ │   ┌──────────────────────────┐                │                                                   │  │
│ │   │ Background Worker Pods   │◄───────────────┘                                                   │  │
│ │   │  • Repo Scanner Consumer │                                                                    │  │
│ │   │  • Delete Handler        │                                                                    │  │
│ │   │  • Notification Worker   │                                                                    │  │
│ │   └────────────┬─────────────┘                                                                    │  │
│ │                │ HTTP API Call                                                                    │  │
│ │                ▼                                                                                  │  │
│ │   ┌────────────────────────────────────────────────────────────────────────────────────────────┐  │  │
│ │   │ OpenSandbox Server & Controller (opensandbox-server & opensandbox-controller)              │  │  │
│ │   │   • K8s Pod Lifecycle Manager   • PVC Workspace Provisioner   • Warm Pool Manager          │  │  │
│ │   └────────────┬───────────────────────────────────────────────────────────────────────────────┘  │  │
│ │                │ Spawns Pods & Mounts PVCs                                                        │  │
│ │                ▼                                                                                  │  │
│ │   ┌────────────────────────────────────────────────────────────────────────────────────────────┐  │  │
│ │   │ Sandbox Execution Layer (code-interpreter pods)                                            │  │  │
│ │   │   • Parent Workspace (Shallow Git Clone)                                                   │  │  │
│ │   │   • Child Scanner Pods (Bandit, Semgrep, ESLint, Trivy)                                    │  │  │
│ │   │   • Isolated Code Runtimes (Python, Node.js, Bash under gVisor / Kata / Firecracker)        │  │  │
│ │   └────────────────────────────────────────────────────────────────────────────────────────────┘  │  │
│ └───────────────────────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Core Component Breakdown

| Component | Location in Codebase | Role & Responsibility |
|---|---|---|
| **AgentGateway** | [`codeInspector/charts/agentgateway`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway) | Hardened Envoy edge proxy handling rate-limiting, edge Auth0 CEL cookie parsing, and ingress routing. |
| **API Gateway / Control Plane** | [`apiServer/fastapi`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi) | FastAPI application handling JWT auth, job tracking, Prometheus metrics, and backend service dispatching. |
| **OpenSandbox Server** | [`opensandbox-server`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server) | Internal service that directly calls K8s APIs to spawn, scale, inspect, and destroy sandbox pods and PVCs. |
| **OpenSandbox Controller** | [`opensandbox-controller`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-controller) | Kubernetes CRD operator managing sandbox custom resources and custom execution policies. |
| **Resource Pool** | [`opensandboxResourcePool`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandboxResourcePool) | Pre-allocates warm sandbox containers to provide sub-millisecond start times for untrusted code execution. |
| **Code Interpreter** | [`code-interpreter`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/code-interpreter) | Hardened container runtime containing security static analyzers (Bandit, Semgrep, ESLint) and language runtimes. |
| **Frontend Web App** | [`z1sandbox-website`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/z1sandbox-website) | Next.js/React user interface supporting live Server-Sent Events (SSE) progress streaming, job cancellation, and management. |
| **Helm Infrastructure** | [`codeInspector`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector) | Umbrella Helm chart deploying RabbitMQ, Redis, PostgreSQL, Prometheus, Grafana, and Bitnami Sealed Secrets. |

---

## 4. End-to-End Working Principles

### A. Edge Ingress & Zero-Trust Security Tier

1. **Edge Entry Point:** All external HTTP/HTTPS traffic hits **AgentGateway** (running in `agentgateway-system`).
2. **CEL Cookie-to-JWT Transformation:** AgentGateway executes a Common Expression Language (CEL) script on incoming requests. If a client presents Auth0 session cookies, AgentGateway extracts and converts them into `Authorization: Bearer <JWT>` headers at the proxy edge before forwarding requests.
3. **Data-Plane Rate Limiting:** Enforces request quotas directly on the Envoy data-plane via `AgentgatewayPolicy` custom resources, stopping malicious floods at the network perimeter.
4. **Cross-Namespace Routing:** AgentGateway uses Kubernetes Gateway API (`HTTPRoute`) coupled with a `ReferenceGrant` in `opensandbox-system` to securely cross namespace boundaries and route requests to `sandbox-api-service`.

---

### B. Asynchronous Repository Scanning Pipeline (RabbitMQ + Workers + SSE)

Repository scanning involves cloning source code, determining language types, provisioning Kubernetes PVC storage, and executing AST parsers.

#### Workflow Breakdown:

1. **Ingestion (`POST /v1/repo-scan`):**
   - The user submits a target repository URL along with optional ephemeral access credentials (Git PAT or SSH Key) to [`scan_repository.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/scan_repository/scan_repository.py#L599).
   - The router validates the payload, generates a unique UUID `job_id`, and registers an initial state record.

2. **Durable Queuing & Resilient Fallback:**
   - The API Gateway checks RabbitMQ health via `is_available()`.
   - **Primary Path:** The task is published to the durable `scan.repo` RabbitMQ exchange. The gateway returns `200 OK` (`status: "QUEUED"`) to the client in under 1ms.
   - **Fallback Path:** If RabbitMQ is offline, the gateway registers the scan directly with FastAPI `BackgroundTasks`.

3. **Background Consumption & Sandbox Workspace Creation:**
   - A background consumer worker ([`consumer.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/core/queue/consumer.py#L95)) picks up the task from `scan.repo`.
   - The worker calls `opensandbox-server` to provision a clean workspace with a dedicated Persistent Volume Claim (PVC).

4. **Zero-Persistence In-Memory Cloning:**
   - The worker executes [`private_clone.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/scan_repository/private_clone.py). Ephemeral SSH keys are written to temporary files with restricted `0600` permissions, or PAT tokens rewrite the HTTPS target URL in RAM.
   - A shallow clone (`git clone --depth=1`) is executed directly into the mounted sandbox PVC workspace.
   - Temporary credential files are destroyed immediately within an unconditional `finally` block, leaving zero persistent credentials on disk or in database logs.

5. **Parallel Child AST Component Scanning:**
   - The worker scans the repository structure to identify active programming languages.
   - Using Python `asyncio.gather`, the worker spawns child language-specific scanner pods (e.g., Bandit for Python, Semgrep for generic rules, ESLint for JS) inside `code-interpreter` containers.
   - Scans run concurrently in isolated child pods, writing report artifacts back to the workspace.

6. **Real-Time Progress Streaming (SSE):**
   - Progress markers (`CLONING`, `SCANNING_PYTHON`, `COMPLETED`) are pushed to Redis and the job tracker.
   - The frontend connects to `/v1/repo-scan/{job_id}/status`, receiving live updates via Server-Sent Events (SSE).

---

### C. Two-Tier Job Hierarchy & Cluster Deletion Architecture

Scan execution creates a **Two-Tier Job Hierarchy**:
- **Parent Job:** The top-level repo scan session holding overall status and the primary cloned repository PVC.
- **Child Jobs:** Individual language-specific AST scan tasks executing inside child container pods.

```
Parent Job (UUID) ──► Cloned Workspace PVC (reposcanner_<rand>/repo/)
      │
      ├── Child Job 1 (Python AST Scanner Pod)
      ├── Child Job 2 (JavaScript ESLint Pod)
      └── Child Job 3 (Security Semgrep Pod)
```

#### Synchronous + Asynchronous Deletion Flow (`DELETE /v1/jobs/{job_id}?purge=true`):

Deleting or cancelling running jobs across multiple API replica pods requires coordination:

1. **Step 1: Eager Synchronous Router Purge ([`scan_jobs/router.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/scan_jobs/router.py)):**
   When `purge=true` is requested, the HTTP handler immediately purges the job from local memory (`job_tracker`) and wipes all related keys from Redis before returning `200 OK` (`DELETE_QUEUED`). This prevents race conditions where frontend 5-second polling cycles might temporarily re-fetch a deleting job.

2. **Step 2: RabbitMQ Dispatch:**
   The deletion task `{"job_id": job_id, "purge": true}` is published to the durable `scan.delete` RabbitMQ queue.

3. **Step 3: Redis Cross-Pod Coordination ("Whiteboard" & "Megaphone") ([`cancellation.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/core/queue/cancellation.py)):**
   - **Whiteboard (Redis KV):** Sets `job:{job_id}:cancelled = "true"`. Any replica pod attempting to start this job inspects this key and immediately aborts.
   - **Megaphone (Redis Pub/Sub):** Publishes the `job_id` to the `job:deletions` channel. All API replica pods listen on this channel; whichever pod is currently executing the target job receives the signal and calls `task.cancel()` to abort the Python task.

4. **Step 4: Kubernetes Sandbox & PVC Teardown ([`delete_handler.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/core/queue/delete_handler.py) & [`delete_job.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/core/delete_job.py)):**
   - The deletion worker collects all child job IDs from in-memory tracking maps and Redis sets (`SMEMBERS job:{parent_id}:child_jobs`).
   - It issues HTTP DELETE requests to `opensandbox-server` to kill all active child sandbox pods and parent containers in parallel, completely deleting the underlying PVC storage volumes.

---

### D. Untrusted Code Snippet Execution Engine

1. **Request Ingestion:** The client sends an execution request via `POST /v1/sandboxes/run` or initiates a session.
2. **Backend Dispatch ([`backends.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/backends.py)):** `GenericHTTPBackend` routes the code execution instructions to `opensandbox-server`.
3. **Warm Pool Allocation:** `opensandbox-server` claims an available pre-warmed container pod from [`opensandboxResourcePool`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandboxResourcePool), minimizing cold-start latency.
4. **Isolated Execution:** Code is executed inside restricted container runtimes (isolated via gVisor, Kata Containers, or Firecracker microVMs).
5. **Enforced Limits:** Strict CPU/memory cgroups limits and execution timeouts (e.g., 30s) are enforced. Output streams (`stdout` and `stderr`), exit status codes, and execution metrics are returned safely to the user.

---

### E. Telemetry & Observability Engine

1. **Dual-Registry Prometheus Metrics ([`apiServer/fastapi/observability`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/observability)):** Exposed at `/metrics` combining two patterns:
   - **Push-Style Accumulation:** Tracks total HTTP requests, Auth0 failures categorized by specific failure labels, active database connections, and scan execution latencies.
   - **Pull-Style Scrape Instrumentation:** Upon being scraped by Prometheus, the endpoint dynamically issues non-destructive passive queue declarations (`declare_queue(passive=True)`) to RabbitMQ to read exact real-time queue depths.
2. **Access Security:** Access to `/metrics` and Grafana routes is secured via `AgentgatewayPolicy` rules, restricting access to localhost and approved administrator subnets.

---

### F. API Key Lifecycle & Expiry Warnings

1. **Background Monitoring:** A background task periodically checks PostgreSQL for API keys approaching expiration.
2. **Notification Queue:** When an expiring key matches lead-time rules, an email alert payload is enqueued into the persistent `notification.email` RabbitMQ queue.
3. **Resilient Worker Consumer:** Worker processes consume messages, formatting warning emails and sending them via the SendGrid Web API.
4. **Fault Tolerance:** Uses explicit acknowledgments (`msg.ack()`). If email dispatch fails, messages transition through exponential backoff delay queues before landing in a Dead-Letter Queue (DLQ) for administrator review.

---

## 5. Security & Secrets Model (GitOps + Sealed Secrets)

1. **Zero-DB Credential Persistence:** Git personal access tokens (PATs) and SSH keys are passed strictly in-memory during HTTP requests and RabbitMQ messages. Ephemeral credential files are scrubbed immediately after cloning. Subprocess execution output is sanitized to ensure credentials are never leaked into stdout/stderr logs.
2. **Bitnami Sealed Secrets ([`codeInspector/pub-cert.pem`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/pub-cert.pem)):** All platform credentials (database passwords, SendGrid keys, JWT secrets) are encrypted using the cluster's public certificate into `SealedSecret` custom resources. These sealed manifests are safely committed to Git. Only the Sealed Secrets controller within the cluster holds the private key required for decryption back into standard Kubernetes `Secret` objects.

---

## 6. Summary of Component Communication

| Source Component | Destination Component | Protocol / Channel | Purpose |
|---|---|---|---|
| Client / UI | AgentGateway | HTTPS / WSS | User requests, SSE streaming connection |
| AgentGateway | API Server (`sandbox-api`) | HTTP (Gateway API) | Forwarding edge-authenticated API requests |
| API Server | PostgreSQL | TCP (SQLAlchemy) | Account, API key, and audit log persistence |
| API Server | Redis | TCP (Redis Protocol) | Key-Value status, SSE logs, Pub/Sub cancellation |
| API Server | RabbitMQ | AMQP 0-9-1 | Enqueuing scan, delete, and notification jobs |
| Consumer Worker | OpenSandbox Server | HTTP REST | Triggering pod allocation and PVC creation/deletion |
| OpenSandbox Server | Kubernetes API | HTTPS / TLS | Provisioning Pods, PVCs, and CRDs |
