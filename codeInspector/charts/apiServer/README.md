# API Server Helm Chart (`apiServer`)

## 1. Overview & Problem Statement

The `apiServer` is the core application backend and business logic orchestrator for the **01-Sandbox** platform. It exposes REST and WebSocket APIs used by developers, IDE plugins, and automated workflows to interact with secure execution sandboxes, manage user authentication, validate API keys, orchestrate quick/repo scans, and manage cluster resources.

Managing this microservice requires coordinating multiple backing services:
- **Relational Storage:** PostgreSQL (via CloudNativePG operator or standalone) for persistent API keys, user profiles, and audit records.
- **In-Memory Cache & Session State:** Redis / Valkey for token invalidation, short-lived rate-limiting counters, and session metadata.
- **Asynchronous Task Queue:** RabbitMQ for decoupled, high-throughput static analysis and repo scanning jobs.
- **Sandboxed Execution Backend:** OpenSandbox Server and Open Cluster Management (OCM) for multi-region sandbox dispatching.
- **High-Availability & Multi-Cluster Failover:** Automated failover controller to promote standby database replicas and manage hub failover across AWS regions.

```
                  ┌──────────────────────────────┐
                  │ Ingress / Gateway Controller │
                  └──────────────┬───────────────┘
                                 │
                                 ▼
                     ┌───────────────────────┐
                     │ sandbox-api Pod(s)    │  (FastAPI / Python 3.11)
                     │ Port: 8000            │  Probes: /health
                     └───────┬───────┬───────┘
           ┌─────────────────┼───────┴─────────────────┐
           ▼                 ▼                         ▼
┌──────────────────┐ ┌───────────────┐      ┌────────────────────┐
│ CloudNative-PG   │ │ Redis / Valkey│      │ RabbitMQ Message   │
│ Primary / Standby│ │ State & Cache │      │ Broker (Workers)   │
└──────────────────┘ └───────────────┘      └────────────────────┘
           │
           ▼
┌────────────────────────────────────────────────────────┐
│ OpenSandbox Server / OCM Multi-Cluster Placement       │
│ (Dispatches gVisor sandboxes to local & remote spokes) │
└────────────────────────────────────────────────────────┘
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

- **Deployments & ReplicaSets:** Manages stateless replicas of the Python API server. If a pod crashes or is OOM-killed, Kubernetes creates a replacement instantly. Rolling update strategy: `maxSurge: 1`, `maxUnavailable: 0` ensures zero-downtime deployments.
- **Three Probe Types in `deployment.yaml`:**
  - `startupProbe`: Polls `/health` every 5s for up to 300s (60 × 5s). Prevents Kubernetes from killing a pod during slow initialization.
  - `livenessProbe`: Polls `/health` every 15s. Restarts the container if it goes unhealthy after startup.
  - `readinessProbe`: Polls `/health` every 10s. Removes the pod from Service endpoints if it fails, preventing traffic routing to degraded instances.
- **ConfigMaps vs. Secrets:**
  - `ConfigMap` (`configmap.yaml`, `app-state-configmap.yaml`): Non-sensitive settings injected via `envFrom`.
  - `Secret` (`sandbox-api-secret`): Sensitive keys — E2B API key, JWT RSA private key, public JWKS, SendGrid token, database and cache passwords.
- **SealedSecrets (`bitnami.com/v1alpha1`):** When `.Values.sealedSecrets.enabled: true`, a `SealedSecret` is rendered instead of a plaintext `Secret`, allowing safe GitOps storage of encrypted credentials.
- **Container Security Hardening:** The API server pod enforces `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, and `capabilities.drop: ["ALL"]`. An `emptyDir` volume is mounted at `/tmp` to allow the app to write temporary files.
- **TopologySpreadConstraints:** Distributes replicas across nodes (`kubernetes.io/hostname`) with `maxSkew: 1` to avoid all pods landing on the same host.
- **Gateway API ReferenceGrant:** Allows routes in `agentgateway-system` to reference backend `Service` and `Secret` objects in the `sandbox` namespace.

---

## 3. Template-by-Template Manifest Breakdown

The templates in [codeInspector/charts/apiServer/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/) configure:

### 3.1. Workload & Compute
- [deployment.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/deployment.yaml):
  - Deployment name: `sandbox-api`. Image: `01community/01sandbox-api:1.0.1`.
  - Injects env from `ConfigMap/sandbox-api-config` via `envFrom`, and individual secret keys via `valueFrom.secretKeyRef`.
  - Mounts `app_state.py` from `ConfigMap/sandbox-api-app-state` into `/app/core/app_state.py` using `subPath` (the script patches internal app state at startup).
  - Three probes all targeting `/health` on port 8000.
- [hpa.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/hpa.yaml):
  - Conditionally renders HPA when `.Values.hpa.enabled: true`.
  - Scales between `minReplicas` and `maxReplicas` targeting CPU ≤ 70% and memory ≤ 80%.

### 3.2. Configuration & Injected Scripts
- [configmap.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/configmap.yaml):
  - ConfigMap name: `sandbox-api-config`. Stores `PORT`, `BACKEND_URL_OPENSANDBOX`, `WORKLOAD_PROVIDER`, `RATE_LIMIT_REQUESTS`, JWT settings, and SendGrid settings.
- [app-state-configmap.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/app-state-configmap.yaml):
  - ConfigMap name: `sandbox-api-app-state`. Inlines the full content of [files/app_state.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/files/app_state.py).

### 3.3. Identity, RBAC & Cross-Namespace Access
- [serviceaccount.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/serviceaccount.yaml):
  - ServiceAccount name: `sandbox-api` in `.Values.namespace`.
- [rbac.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/rbac.yaml):
  - `Role/gateway-secret-patcher` in `agentgateway-system` namespace: permits `get`, `patch`, `update` on the specific gateway Secret (`apikey` or `jwt-signing-key`).
  - `RoleBinding/sandbox-api-gateway-patcher-binding`: binds `sandbox-api` ServiceAccount to the above Role, enabling the API server to rotate gateway secrets.
- [referencegrant.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/referencegrant.yaml):
  - `ReferenceGrant/allow-agentgateway-route`: permits HTTPRoute and TCPRoute resources in `agentgateway-system` to reference Services in `.Values.namespace`.

### 3.4. Secret Management
- [secret.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/secret.yaml) & [sealedsecret.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/sealedsecret.yaml):
  - Mutual exclusion: when `sealedSecrets.enabled: true`, renders a `bitnami.com/v1alpha1/SealedSecret` with the encrypted data blocks from `sealedSecrets.encryptedData`; otherwise renders a standard `v1/Secret` named `sandbox-api-secret`.

### 3.5. Database & Caching Components
- [cnpg-cluster.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/cnpg-cluster.yaml): CloudNativePG `Cluster` CRD. Role `"primary"` creates the leader; role `"standby"` creates a streaming replica pointed at a remote primary host.
- [postgresql.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/postgresql.yaml) & [postgresql-route.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/postgresql-route.yaml): Standalone PostgreSQL option and Gateway API TCPRoute for direct DB routing.
- [redis.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/redis.yaml), [valkey.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/valkey.yaml), & [redis-route.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/redis-route.yaml): In-memory caching with primary/replica sync options.
- [rabbitmq.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/rabbitmq.yaml) & [rabbitmq-routes.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/rabbitmq-routes.yaml): RabbitMQ broker for async worker queue decoupling.

### 3.6. Networking & Failover
- [service.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/service.yaml): Service `sandbox-api-service`, ClusterIP, port 80 → targetPort 8000.
- [ingress.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/ingress.yaml): NGINX Ingress with 900-second proxy timeouts for long-running code execution streams.
- [failover-controller.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/failover-controller.yaml): Custom controller pod using `bitnami/kubectl`, watches hub health and executes CNPG cluster promotion and OCM cluster re-registration when PrimaryHub is unreachable.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `namespace` | string | `"sandbox"` | Target namespace for all API server resources. |
| `configMap.PORT` | string | `"8000"` | HTTP server listening port. |
| `configMap.WORKLOAD_PROVIDER` | string | `"ocm"` | Workload placement engine (`"ocm"` or `"local"`). |
| `configMap.BACKEND_URL_OPENSANDBOX` | string | `"http://opensandbox-server..."` | Internal DNS URL of the OpenSandbox server. |
| `configMap.RATE_LIMIT_REQUESTS` | string | `"100"` | Max API requests per rate limit window. |
| `configMap.RATE_LIMIT_WINDOW_SECS` | string | `"60"` | Rate limit window duration in seconds. |
| `configMap.JWT_EXPIRATION_MINUTES` | string | `"60"` | JWT access token expiry. |
| `deployment.replicaCount` | integer | `1` | Number of API server replicas. |
| `deployment.securityContext.runAsUser` | integer | `1000` | UID used by the application container. |
| `sealedSecrets.enabled` | boolean | `false` | Toggles between plaintext `Secret` and encrypted `SealedSecret`. |
| `cnpg.enabled` | boolean | `false` | Enables CloudNativePG managed PostgreSQL cluster. |
| `cnpg.replication.role` | string | `"primary"` | CNPG node role (`"primary"` or `"standby"`). |
| `cnpg.replication.primaryHost` | string | `""` | Required for standby: hostname/IP of the primary cluster. |
| `valkey.enabled` | boolean | `false` | Enables Valkey caching layer. |
| `rabbitmq.enabled` | boolean | `false` | Enables RabbitMQ task queue. |
| `failoverController.enabled` | boolean | `false` | Enables automatic cross-region hub failover agent. |
| `hpa.enabled` | boolean | `false` | Enables HorizontalPodAutoscaler. |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

- **Central Orchestrator:** `apiServer` validates JWT credentials, enforces tenant quotas via PostgreSQL, dispatches tasks to `opensandbox-server` via OCM placements, and streams results back over WebSocket.
- **Multi-Region Disaster Recovery:** In a disaster scenario where PrimaryHub (us-east-1) becomes unavailable, SecondaryHub (eu-west-1) leverages the `failover-controller` to promote its CNPG standby to primary and redirect traffic.
- **Gateway Secret Patcher:** The `sandbox-api` ServiceAccount has the RBAC `Role/gateway-secret-patcher` permission, allowing the API server to update JWT signing keys in the gateway dynamically.

---

## 6. Practical Debugging & Troubleshooting

### Inspect Workload Health
```bash
# Check pod status and all three probe states
kubectl get pods -n sandbox -l app=sandbox-api
kubectl describe pod -l app=sandbox-api -n sandbox

# View live API server logs
kubectl logs -n sandbox -l app=sandbox-api -f --tail=100
```

### Validate Health Endpoint
```bash
# Exec into the pod and curl the health endpoint
kubectl exec -it -n sandbox deploy/sandbox-api -- curl -s http://localhost:8000/health
```

### Common Failure Modes
1. **`CrashLoopBackOff` on Startup:** Most commonly caused by a missing or incorrect `PG_PASSWORD` or `JWT_PRIVATE_KEY` in `sandbox-api-secret`. Confirm secret keys: `kubectl get secret sandbox-api-secret -n sandbox -o yaml`.
2. **504 Gateway Timeout on Large Code Tasks:** Increase `nginx.ingress.kubernetes.io/proxy-read-timeout` annotation in `ingress.yaml` values (default: 900s).
3. **`readOnlyRootFilesystem` Write Errors:** App must write only to `/tmp` (mounted as `emptyDir`). Any writes to other paths will produce `EROFS`. Verify the application's temp directory configuration.
