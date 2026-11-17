# OpenSandbox Execution Engine Helm Chart (`opensandbox`)

## 1. Overview & Problem Statement

Building an AI-driven automated code inspection, dynamic testing, or AI agent execution platform requires executing user code safely, quickly, and at scale. Traditional virtualization (like launching a full EC2 instance per job) incurs severe cold-start penalties (30–90 seconds) and high cloud bills. Conversely, standard Linux containers lack isolation, exposing the cluster to security compromise.

**OpenSandbox** solves this dilemma. It provides a Kubernetes-native orchestration framework for disposable, isolated sandbox environments. It decouples the management layer into two cooperating components:
1. **OpenSandbox Server:** Exposes a REST API (`/api/v1/01sbx`) for creating sandboxes, executing commands across 10 language runtimes, uploading/downloading files, and streaming stdout/stderr.
2. **OpenSandbox Controller:** A custom Kubernetes Operator that reconciles `Pool` and `BatchSandbox` CRDs, provisions pods with hardened runtime classes (gVisor), and coordinates multi-cluster workload distribution via **Open Cluster Management (OCM) ManifestWorks**.

```
       [ Client / API Server (sandbox-api) ]
                       │
                       ▼ HTTP REST /api/v1/01sbx
       ┌───────────────────────────────┐
       │  opensandbox-server           │  (NodePort 30080 → Container 8080)
       │  Reads config.toml from CM    │
       │  workload_provider = "ocm"    │
       └───────────────┬───────────────┘
                       │ Creates CRD (Pool / BatchSandbox)
                       │ via CONTROLLER_URL → localhost:8080
                       ▼
       ┌───────────────────────────────┐
       │  opensandbox-controller       │
       │  SA: opensandbox-controller   │
       └───────┬───────────────┬───────┘
               │ Local         │ OCM ManifestWorks
               ▼               ▼
     ┌──────────────────┐  ┌──────────────────────────────────┐
     │ gVisor Sandboxes │  │ Spoke Clusters (via WireGuard)  │
     │ (runtimeClass:   │  │ (sandbox-spoke-placement)        │
     │  gvisor)         │  └──────────────────────────────────┘
     └──────────────────┘
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

- **Custom Controller & Reconciliation Loop:** The OpenSandbox Controller runs an infinite loop: `Watch Pool/BatchSandbox CRDs → Detect state changes → Create/Scale Pods → Update CRD status`.
- **Open Cluster Management (OCM) Integration:** Enables scheduling sandboxes onto remote spoke clusters:
  - `Placement` & `PlacementDecision`: Selected via `server.placementName` (default: `sandbox-spoke-placement`).
  - `ManifestWork`: Wraps the sandbox pod manifest and syncs it to the remote spoke cluster over WireGuard.
- **RuntimeClass Enforcement:** Every `BatchSandbox` pod spawned includes `runtimeClassName: gvisor` in the pod template (hardcoded in the `example.batchsandbox-template.yaml` mounted into the server).
- **Multi-Language Scanner Images:** The server pod injects separate container images per language (Python, Go, Java, Node.js, Rust, Shell, C++, Ruby, Terraform, Kubernetes) via environment variables (`SCANNER_IMAGE_PYTHON`, `SCANNER_IMAGE_GO`, etc.), enabling targeted static analysis without bundling all scanners into one image.
- **PVC for Scan Data:** A `PersistentVolumeClaim` is mounted at `.Values.storage.mountPath` (default: `/data`) in the server pod. Access mode is `ReadWriteMany` to support potential multi-replica deployments.

---

## 3. Template-by-Template Manifest Breakdown

The templates in [codeInspector/charts/opensandbox/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/opensandbox/templates/) deploy:

### 3.1. [controller.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/opensandbox/templates/controller.yaml)
- **Kinds:** `ServiceAccount`, `ClusterRole`, `ClusterRoleBinding`, `Deployment`
- **Deployment name:** `opensandbox-controller` in `.Values.namespace`.
- **Image:** `opensandbox/controller:v1.0.0`
- **RBAC permissions:** Grants CRUD on Pods, Services, ConfigMaps, Namespaces, Deployments, ReplicaSets; full control over `sandbox.opensandbox.io` CRDs (`pools`, `batchsandboxes`); and read access to OCM resources (`manifestworks`, `managedclustersets`, `placements`, `placementdecisions`, `managedclusters`).
- **Key env vars:** `SANDBOX_IMAGE: "01community/01sandbox-codeinterpreter:1.0.0"` — the base container used for sandbox pods.

### 3.2. [server.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/opensandbox/templates/server.yaml)
- **Kinds:** `ServiceAccount`, `ClusterRoleBinding`, `Service`, `Deployment`, `ConfigMap` (server-config), and optionally `PersistentVolumeClaim`.
- **Deployment name:** `opensandbox-server`. Image: `opensandbox/server:v1.0.0`.
- **Service:** Name `opensandbox-server`, NodePort `30080` → container port `8080`.
- **Reuses the controller's ClusterRole** (`ClusterRoleBinding` binds `opensandbox-server` ServiceAccount to `opensandbox-controller` ClusterRole).
- **Key env vars injected:**
  - `CONTROLLER_URL: "http://opensandbox-controller.{{ .Values.namespace }}.svc.cluster.local:8080"`
  - `API_ROUTE_PREFIX: ".Values.global.apiRoutePrefix"` (default: `/api/v1/01sbx`)
  - `SANDBOX_IMAGE`, `SANDBOX_CPU` (200m), `SANDBOX_MEMORY` (512Mi)
  - `AUTO_TERMINATION_SECONDS`, `SANDBOX_CREATE_TIMEOUT_SECONDS` (600)
  - 10 `SCANNER_IMAGE_*` variables (Python, Go, Java, Node.js, K8s, Rust, Shell, C++, Ruby, Terraform)
- **ConfigMap `opensandbox-server-config`:** Contains two files:
  1. `config.toml`: Full TOML server configuration (`[server]`, `[runtime]`, `[storage]`, `[kubernetes]`, `[ingress]` blocks). Sets `workload_provider`, `placement_name`, `namespace`, informer settings.
  2. `example.batchsandbox-template.yaml`: Pod template for sandbox jobs with `runtimeClassName: gvisor`, `restartPolicy: Never`, `tolerations: - operator: "Exists"`.
- **PVC (conditional):** Rendered only when `storage.enabled: true`. Access mode: `ReadWriteMany` (not `ReadWriteOnce`). Size: `.Values.storage.size` (default `10Gi`).

### 3.3. [crds/crds.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/opensandbox/crds/crds.yaml)
- Registers custom schemas `pools.sandbox.opensandbox.io` and `batchsandboxes.sandbox.opensandbox.io`.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `namespace` | string | `"opensandbox-system"` | Namespace for all OpenSandbox components. |
| `controller.replicaCount` | integer | `1` | Number of controller manager replicas. |
| `controller.sandboxImage` | string | `"01community/01sandbox-codeinterpreter:1.0.0"` | Default sandbox runner container image. |
| `controller.resources.limits.cpu` | string | `"500m"` | Controller CPU limit. |
| `server.replicaCount` | integer | `1` | Number of server API replicas. |
| `server.workloadProvider` | string | `"ocm"` | Workload placement engine (`"ocm"` for multi-cluster or `"local"`). |
| `server.placementName` | string | `"sandbox-spoke-placement"` | OCM Placement rule name used for spoke dispatch. |
| `server.sandboxImage` | string | `"01community/01sandbox-codeinterpreter:1.0.0"` | Pod template image for BatchSandbox execution. |
| `server.sandboxCreateTimeoutSeconds` | integer | `600` | Seconds to wait for a sandbox pod to become Ready. |
| `server.autoTerminationSeconds` | integer | `0` | Sandbox TTL in seconds (0 = no automatic termination). |
| `server.sandboxCpu` | string | `"200m"` (default) | CPU request/limit for each sandbox container. |
| `server.sandboxMemory` | string | `"512Mi"` (default) | Memory request/limit for each sandbox container. |
| `storage.enabled` | boolean | `true` | Enables PVC for scan data persistence. |
| `storage.pvcName` | string | `"scan-pvc"` | PVC name. |
| `storage.size` | string | `"10Gi"` | PVC storage size. |
| `storage.accessMode` | string | `"ReadWriteOnce"` | PVC access mode (template defaults to `ReadWriteMany`). |
| `storage.mountPath` | string | `"/data"` | Mount path in server container. |
| `multiCluster.enabled` | boolean | `true` | Enables OCM multi-cluster workload dispatch. |
| `multiCluster.placementName` | string | `"sandbox-spoke-placement"` | OCM Placement name. |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

- **Sandboxed Execution Core:** When `sandbox-api` receives a code run request, it calls `opensandbox-server` REST API. The server creates a `BatchSandbox` CRD, the controller picks it up and provisions a gVisor pod locally or on a remote spoke via OCM.
- **Multi-Language Scanning:** Each language has a dedicated scanner image. When a repo scan is requested, the server dispatches the appropriate `SCANNER_IMAGE_*` container, isolating scanner dependencies from each other.
- **Zero Host Impact via gVisor:** Even if user code attempts kernel exploits, the attack is contained inside gVisor's Sentry guest kernel and cannot touch the host EC2 or other tenant workloads.

---

## 6. Practical Debugging & Troubleshooting

### Inspect Server & Controller Health
```bash
# Check all pods in the opensandbox namespace
kubectl get pods -n opensandbox-system

# Stream controller logs
kubectl logs -n opensandbox-system -l app=opensandbox-controller -f --tail=100

# Stream server API logs
kubectl logs -n opensandbox-system -l app=opensandbox-server -f --tail=100
```

### Inspect the Server's Mounted Configuration
```bash
# View the TOML config the server reads
kubectl exec -it -n opensandbox-system deploy/opensandbox-server -- \
  cat /etc/opensandbox/config.toml

# View the BatchSandbox pod template
kubectl exec -it -n opensandbox-system deploy/opensandbox-server -- \
  cat /etc/opensandbox/example.batchsandbox-template.yaml
```

### Check CRDs and OCM Resources
```bash
# List all active pools and batch sandboxes
kubectl get pools.sandbox.opensandbox.io -n opensandbox-system
kubectl get batchsandboxes.sandbox.opensandbox.io -n opensandbox-system

# Check OCM PlacementDecisions to see which spoke was selected
kubectl get placementdecisions -n opensandbox-system
```

### Common Failure Modes
1. **Sandbox Pod Fails with Runtime Error / `RuntimeClass not found`:** The worker node lacks the `gvisor` RuntimeClass or containerd is not configured with the `runsc` handler. Run `kubectl get runtimeclass gvisor`.
2. **OCM ManifestWork Pending / Not Synced:** Verify WireGuard connectivity (`ping 10.99.0.x`), and confirm spoke clusters have accepted the OCM join (`kubectl get managedclusters`).
3. **Server Cannot Write to `/data`:** PVC may not be bound or the access mode may conflict with multi-replica deployments. Check `kubectl get pvc scan-pvc -n opensandbox-system` for `Bound` status.
