# CodeInspector & Z1 Sandbox Platform

## Overview

CodeInspector (commercially integrated as Z1 Sandbox) is an enterprise-grade Kubernetes-based sandbox code execution platform. It provides a highly stable, hardened, and auditable HTTP API for executing arbitrary untrusted code in isolated distributed environments.

The system supports multiple backend providers (Mock, Subprocess, Docker, E2B, OpenSandbox), enabling runtime-adaptive execution layers that are hot-swappable strictly without modifying client ingress routing.

## Complete Platform Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           External Web Traffic                              │
│  (Z1 Sandbox Frontend / Dashboard / CI Pipelines -> api-sandbox.01security.com)
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Agent Gateway (API Gateway)                          │
│                                                                             │
│  Gateway: agentgateway-proxy (Port: 80 / 443)                               │
│  HTTPRoute: Matches api-sandbox.01security.com -> sandbox-api-service       │
│                                                                             │
│  AgentgatewayPolicy (Authentication): Strict JWT validation                 │
│  - Blocks any requests missing or possessing invalid RS256 JWTs.            │
│  - Decodes inline public JWKS configuration natively at edge layer.         │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Sandbox Core API (Python 3.12)                        │
│                                                                             │
│  1. Identity Bridge & API Key Management (/v1/api-keys)                     │
│     - Maps Auth0 user IDs to Developer Keys stored in Postgres / Redis.     │
│  2. Dynamic Proxy Catch-all (/api/{backend_id}/*)                           │
│     - Forwards traffic transparently to distinct execution clusters.        │
│  3. Async Job Tracking & Telemetry                                          │
│     - Tracks /v1/scan-jobs, /v1/job-id, /v1/scan-status.                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│               Execution Backends (OpenSandbox Controller)                   │
│                                                                             │
│  - Receives executed payloads, configures bounded sandbox pod (gVisor).     │
│  - Allocates shared Longhorn PVC storage for asynchronous job reporting.    │
│  - Auto-scales pre-warmed sandbox buffers using opensandboxResourcePool.    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Technical Deep-Dive

### 1. User Authentication (Auth0 + Z1 Dashboard Frontend)

The platform includes a sleek, developer-first dashboard (found in `dashboard/index.html` and bundled into `z1sandbox-website`). When developers visit the site:
- **Auth0 SDK Initialization**: The frontend (`assets/js/auth.js`) automatically initializes Auth0 via `auth0-spa-js` pointing to the API audience `https://code-inspector-api`.
- **Login Session & Cookies**: Upon successful Auth0 login, the client receives a token which is saved as a strict cookie `inspector_auth`.
- **Identity Presentation**: All subsequent dashboard routing passes this cookie. The backend `apiServer` relies heavily on determining whether the token issuer came from Auth0 (remote) or internally (local).

### 2. API Key Generation & Verification (Identity Bridge)

Managing long-lived API keys is handled by a sophisticated API server subsystem (`codeinspectior_api.py`):
- **Generation Logic**: A user posts to `/v1/api-keys`. The system relies on its internal private RSA key to generate a proprietary RS256 JWT representing their new Developer API Key.
- **Persistent Storage**:
  - **PostgreSQL**: Stores robust metadata (`jti`, `name`, `backend`, `user_email`, `expires_at`, `is_revoked`, and `last_used_at`).
  - **Redis Drop-in**: Copies the active `jti` to an `active_api_keys` set providing sub-millisecond line-rate request acceptance at massive scale.
- **Background Janitor Task**: A background asynchronous worker (`cleanup_expired_keys_task`) loops every 60 seconds to reap keys that have expired, deleting them structurally from both DB logs and Redis cache preventing stale "back doors".
- **The Identity Bridge**: If a developer accesses a management route using their Auth0 dashboard cookie, the backend detects the Auth0 issuer and dynamically bridges their identity (via Auth0 `sub` ID) to discover their latest active Developer Key to fetch their unique runtime metrics.

### 3. AgentGateway Authorization Rules

To prevent DDoS and unauthorized executions on the costly Kubernetes pods, routing edge traffic relies on the Kubernetes Gateway API:
- `agentgateway-httproute.yaml` catches hostname `api-sandbox.01security.com` and directs traffic internally specifically to `sandbox-api-service`.
- `agentgatewaypolicy.yaml` binds directly to this route, setting `AuthenticationType: jwt` with mode `Strict`.
- The Gateway intercepts traffic directly rejecting it if the `authorization` header is invalid against the internally signed JWKS matrix baked directly into the policy text. **No unauthorized payload even touches the internal API.**

### 4. Code Auditing & Scan Job Sandboxes

The core responsibility of the platform is dispatching and tracking security audit scripts:
- **Scan Initialization**: A client invokes `POST /v1/scan-jobs` providing source code maps.
- **UUID Traceability**: The `apiServer` immediately associates a global `job_id` (UUID), stores it to local state mappings (`state.latest_job_id`), and delegates it to the OpenSandbox backend.
- **Status Polling**: The frontend subsequently calls `/v1/job-id` to identify the blocking session's ID from alternate tabs, and polls `/v1/scan-status/{job_id}` to retrieve active provision states (e.g., Image Pulling, Container Creating, Auditing).
- **Execution**: The `opensandbox-server` instructs the `opensandbox-controller` to spin up an isolated `codeinterpreter` container under the `gVisor` RuntimeClass. The execution utilizes an isolated pod network and is heavily bounded via CPU/ Memory limits.
- **Reporting**: The execution results are streamed out via JSON format and cached on a shared volume, ultimately pulled by the frontend securely through `GET /v1/scan-jobs/{job_id}/report`.

---

## Helm Chart Deployment Options

The whole solution is natively wrapped in an omni-chart located in `codeInspector/`. Deployment relies on comprehensive templating via `codeInspector/values.yaml` offering configurable topologies:

### MetalLB (`metallb`)
Configures Layer 2 routing bridging bare-metal/Kind setups globally.
- **IP Address Pool**: Exposes external ranges natively (e.g., `148.113.4.247/32`).

### Agent Gateway (`agentgateway`)
- **Gateway Config**: Sets the `agentgateway` GatewayClass.
- **TLS Configuration**: Implements references terminating SSL across incoming 443.

### Core Sandbox API (`apiServer`)
- **Resources**: Auto-scaling deployed with HPA (targets CPU at 70%, Memory at 80%). Contains strict configurations running as non-root (uid: 1000).
- **Datastores Configuration**: Contains passwords internally wired linking PostgreSQL and Redis databases for API key state handling.
- **Crypto Settings**: Central configuration mapping `JWT_PUBLIC_JWKS` and `GATEWAY_SECRET` structures.

### Distributed Runtime Setup (`kindCluster`)
- Hardwires standard cluster runtime to route Pod creations toward `gvisor` strictly ensuring kernel-layer separation.

### Scaling & Buffering (`opensandboxResourcePool`)
- For instant evaluation latency, the code defines active pool buffering metrics.
- Default settings specify a `bufferMin: 3` and `bufferMax: 10` (creating warmed containers anticipating code) that can dynamically scale out to a `poolMax: 50` hard ceiling under heavy user pressure.

## Quick Start Configuration

Deploy the entire architecture locally or against any bare-metal solution instantly via Helm.

1. **Install CRDs**:
    Ensure Gateway API CRDs (`v1.5.0` standard) and `metallb` CRDs are installed on your cluster first.

2. **Deploy Omni-Chart**:
   ```bash
   helm install codeInspector ./codeInspector -n opensandbox-system --create-namespace
   ```

3. **Validate Architecture Services**:
   ```bash
   kubectl get pods -n opensandbox-system
   kubectl get gateway -n agentgateway-system
   ```

4. **Using the Gateway directly via curl**:
    Generate an API key securely via Auth0 Dashboard routing, then POST directly:
    ```bash
    curl -X POST https://api-sandbox.01security.com/v1/scan-jobs \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer <API-KEY>" \
      -d '{
        "files": {
           "input.py": "print(\"Executing verified Code!\")"
        }
      }'
    ```

---
*Powered by 01 Security Advanced Architecture.*
