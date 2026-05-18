# Technical Specification: Dependency Health Checking Architecture

This document specifies the design, technical execution flow, and security layers of the health monitoring suite implemented inside the 01 Sandbox API Gateway/Server.

---

## 1. Architectural Overview

The health checking subsystem provides a modular, lightweight, and production-safe validation pipeline. It ensures all critical dependencies in the `opensandbox-system` namespace are actively monitored without generating artificial load or risking connection pooling issues.

```mermaid
graph TD
    Client[Client / Ingress Monitor] -->|1. Public Route /health| API_Server[sandbox-api Pod]
    Client -->|2. Secure Route /api/v1/01sbx/...| Auth_Guard{FastAPI validate_token}
    
    Auth_Guard -->|Unauthorized / Invalid Key| Blocked[401 Unauthorized]
    Auth_Guard -->|Authorized API Key| API_Server
    
    API_Server -->|PostgreSQL Query| DB[(postgresql-service:5432)]
    API_Server -->|Redis ping/pong| Cache[(redis-service:6379)]
    API_Server -->|HTTP GET /health| Core[opensandbox-server:80]
```

---

## 2. Health Endpoint Specifications

### A. Aggregate Checks (Liveness & Readiness Probes)
*   **Public Route**: `/health`
*   **Protected Route**: `/v1/health` (requires Authorization JWT header)
*   **Behavior**: Evaluates all dependencies sequentially. If any dependency is offline, the endpoint returns `500 Internal Server Error` and sets the global state to `unhealthy`.

### B. Secured Individual Sub-Dependency Checks
*   **Required Header**: `Authorization: Bearer <DEVELOPER_API_KEY>`
*   **Endpoints**:
    *   **PostgreSQL**: `GET /api/v1/01sbx/postgresql/health`
    *   **Redis (Cache & Queue)**: `GET /api/v1/01sbx/redis/health`
    *   **01Sandbox Core Engine**: `GET /api/v1/01sbx/01sandbox/health`

---

## 3. Step-by-Step Technical Flow

### A. PostgreSQL Dependency Validation
1.  **Connection**: The API Server opens a database connection session using the configured connection pool details (`PG_HOST: postgresql-service`).
2.  **Execution**: It executes a lightweight, read-only validation statement:
    ```sql
    SELECT 1;
    ```
3.  **Clean up**: It immediately closes the cursor and releases the connection session back to the pool to prevent thread/socket exhaustion.
4.  **Error Handling**: If the database is locked, out of disk space, or offline, the driver throws a connection exception. The API Server catches this exception, marks the dependency state to `"unhealthy"`, and forwards the specific database driver error string in the `"details"` field.

---

### B. Redis Cache & Queue Validation
1.  **Connection**: The API Server references the shared connection client (`state.redis_client`).
2.  **Ping Command**: It issues a low-overhead network check:
    ```python
    state.redis_client.ping()
    ```
3.  **Evaluation**: 
    *   If Redis replies with a literal **`PONG`**, the check passes as `"healthy"`.
    *   If the request times out or throws an error (e.g. connection refused or Out Of Memory state), the check is marked `"unhealthy"`.

---

### C. 01Sandbox Core Engine Validation
1.  **Network Request**: The API Server issues a synchronous HTTP GET request targeting the upstream service's liveness endpoint:
    ```
    GET http://opensandbox-server.opensandbox-system.svc.cluster.local/health
    ```
2.  **Timeout Guard**: The request is configured with a tight **3-second timeout limit** (`timeout=3`) using the `httpx` client. This ensures that a frozen or sluggish upstream runner cannot hang the API Server's threads.
3.  **Status Code Check**: If the core engine returns an HTTP status code `< 500` within 3 seconds, it is marked `"healthy"`. Otherwise, it falls back to `"unhealthy"`.

---

## 4. Sequence & Security Execution Flow

When a client queries one of the individual endpoints, the following step-by-step process is executed:   

```mermaid
sequenceDiagram
    participant Client as Client CLI / Monitor
    participant Guard as FastAPI validate_token Guard
    participant API as sandbox-api Pod (FastAPI)
    participant Svc as Target Kubernetes Service

    Client->>Guard: GET /api/v1/01sbx/01sandbox/health (with Header)
    alt No Token or Invalid Format
        Guard-->>Client: 401 Unauthorized (Blocked immediately)
    else Valid Token / API Key
        Guard->>API: Route execution authorized
        activate API
        Note over API: Execute lightweight check logic
        API->>Svc: Send low-overhead ping/query
        alt Service Responsive
            Svc-->>API: Success response (PONG / SELECT 1 / HTTP 200)
            API-->>Client: 200 OK {"status": "healthy", ...}
        else Service Unresponsive or Errored
            Svc-->>API: Timeout or connection error
            API-->>Client: 500 Internal Server Error {"status": "unhealthy", ...}
        end
        deactivate API
    end
```

## 5. Kubernetes Cluster Integration (How the Server Sees and Acts on Health Statuses)

Within your RKE2 cluster, the local **`kubelet`** agent on each node uses our `/health` endpoint to drive self-healing and load balancing. Kubernetes interacts with the API through **three automated Probes** defined inside your deployment configuration:

### A. The Liveness Probe (Self-Healing / Auto-Restart)
*   **Purpose**: Determines if the API Server pod is stuck, deadlocked, or running in an unrecoverable state.
*   **Polling Frequency**: Every 15 seconds.
*   **Server Logic**:
    *   If `/health` returns **`200 OK`**, the `kubelet` does nothing.
    *   If a critical service goes offline and returns **`500 Internal Server Error`** (or times out) three consecutive times:
*   **Automated Action**: The Kubernetes control plane **instantly kills the faulty pod and provisions a fresh, running container instance** automatically.

### B. The Readiness Probe (Traffic Control)
*   **Purpose**: Validates if the pod is ready to serve live incoming user requests (e.g. running scripts, scanning code).
*   **Polling Frequency**: Every 10 seconds.
*   **Server Logic**:
    *   If `/health` returns **`200 OK`**, the pod is marked as healthy in the load balancer backend pool.
    *   If `/health` returns **`500 Internal Server Error`**:
*   **Automated Action**: The Kubernetes proxy **instantly removes the unhealthy pod from the load balancer pool**. User requests are routed only to other healthy instances, ensuring zero-downtime client operations even when internal services are recovering.

### C. The Startup Probe (Boot Protection)
*   **Purpose**: Guards the container during its initial boot sequence (e.g., establishing database pools or initializing Redis).
*   **Automated Action**: Temporarily disables liveness and readiness probe evaluations until the container finishes booting up and returns its first successful `200 OK`, protecting the starting pod from being terminated prematurely.

---

## 6. Operations & Monitoring Best Practices

1.  **Deployment Configuration Reference**:
    Ensure the following configuration block is matched in [deployment.yaml](file:///home/berrybytes/Desktop/01-Sandbox/codeInspector/charts/apiServer/templates/deployment.yaml#L80-L103):
    ```yaml
              livenessProbe:
                httpGet:
                  path: /health
                  port: 8000
                initialDelaySeconds: 10
                periodSeconds: 15
                timeoutSeconds: 5
                failureThreshold: 3

              readinessProbe:
                httpGet:
                  path: /health
                  port: 8000
                initialDelaySeconds: 5
                periodSeconds: 10
                timeoutSeconds: 3
                failureThreshold: 3
    ```
2.  **External Monitoring Alerts**:
    *   **Aggregate Probe (`https://sandbox.01security.com/health`)**: Point external status checkers (e.g., UptimeRobot, Prometheus Blackbox) here to monitor high-level availability.
    *   **Service-Level Probes (`/api/v1/01sbx/[postgresql/redis/01sandbox]/health`)**: Map alerts from these secured endpoints directly to your engineering notification systems (Slack, PagerDuty) to pin down precisely which microservice went offline before the entire cluster is impacted.
