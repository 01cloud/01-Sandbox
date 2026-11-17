# AgentGateway Controller Helm Chart (`agentgateway-controller`)

## 1. Overview & Problem Statement

In the Kubernetes Gateway API specification, a distinction is made between the **Control Plane** and the **Data Plane**:
- The **Data Plane** consists of the high-performance network proxies (such as Envoy) that inspect, filter, and route live HTTP/TCP packets.
- The **Control Plane** is the intelligent brain that watches Kubernetes resources (`Gateway`, `HTTPRoute`, `ReferenceGrant`, `Service`, `Endpoints`) and dynamically translates them into lower-level proxy instructions (via Envoy's **xDS** gRPC API).

The `agentgateway-controller` chart deploys this control plane. Without this controller, Gateway API CRDs are merely static data in `etcd`; the controller brings them to life by continuously reconciling cluster state and streaming active routing tables to the proxy data plane.

```
┌────────────────────────────────────────────────────────┐
│ Kubernetes API Server (etcd)                           │
│  - Gateway ("agentgateway-proxy")                      │
│  - HTTPRoute ("agentgateway-api-route")                │
│  - AgentgatewayPolicy ("apikey-auth")                  │
│  - Services & Endpoints                                │
└───────────────────────────┬────────────────────────────┘
                            │ Watch & Reconcile Loop
                            ▼
┌────────────────────────────────────────────────────────┐
│ agentgateway-controller (Control Plane)                │
│  - Translates K8s CRDs -> Envoy xDS configurations     │
│  - Manages Gateway status & conditions                 │
│  - Exposes xDS gRPC Server on port 9978                │
└───────────────────────────┬────────────────────────────┘
                            │ xDS gRPC Stream (Port 9978)
                            ▼
┌────────────────────────────────────────────────────────┐
│ Envoy Data Plane Proxy (agentgateway)                  │
│  - Dynamically updates routes, clusters, listeners     │
│  - Evaluates authorization policies & rate limits      │
└────────────────────────────────────────────────────────┘
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

- **Operator / Controller Pattern:** A core loop in Kubernetes: `Read desired state -> Inspect actual state -> Reconcile differences`. The controller constantly watches resources using Informers and updates the Envoy proxies when Services scale, Pod IPs change, or Routes are created.
- **ServiceAccount & ClusterRole (RBAC):** To watch cluster-wide resources (like Nodes, Services, Namespaces, Secrets, and Gateway CRDs), the controller pod needs identity (`ServiceAccount`) and permissions (`ClusterRole` + `ClusterRoleBinding`).
- **xDS Protocol:** Envoy's "Discovery Service" protocol. Instead of restarting proxies when configuration changes, Envoy connects to `agentgateway-controller` via gRPC (port 9978). New routes and endpoints are pushed with zero proxy downtime.
- **Horizontal Pod Autoscaler (HPA):** Dynamically scales controller replicas up or down based on CPU/memory utilization. Only activated when `.Values.controller.horizontalPodAutoscaler` is non-empty.
- **Vertical Pod Autoscaler (VPA):** Automatically optimizes CPU/memory requests over time based on actual usage. Activated when `.Values.controller.verticalPodAutoscaler` is non-empty.
- **Pod Disruption Budget (PDB):** Safeguards the controller during cluster maintenance by guaranteeing a minimum number of running replicas. Activated when `.Values.controller.podDisruptionBudget` is non-empty.

> [!IMPORTANT]
> HPA, VPA, and PDB are all rendered from a **single template file** (`horizontalpodautoscaler.yaml`) with separate conditional blocks. They are only deployed when their respective `values.yaml` sections are non-empty.

---

## 3. Template-by-Template Manifest Breakdown

The templates in [codeInspector/charts/agentgateway-controller/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway-controller/templates/) deploy:

### 3.1. [deployment.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway-controller/templates/deployment.yaml)
- **Kind:** `Deployment` (`apps/v1`)
- **Metadata:** Name generated from `{{ include "agentgateway.fullname" . }}`, deployed in `.Release.Namespace`.
- **Functionality:**
  - Deploys the controller container from `cr.agentgateway.dev/controller`.
  - Exposes three key ports:
    - `grpc-xds-agw` (port `9978`): The xDS discovery gRPC server that proxies connect to.
    - `health` (port `9093`): Liveness and readiness HTTP probes.
    - `metrics` (port `9092`): Prometheus scraping endpoint.
  - Pod annotations include `prometheus.io/scrape: "true"` automatically, enabling auto-discovery.
  - Supports optional rollout strategy override (`controller.strategy`), priority class, and extra environment variables (`controller.extraEnv`).

### 3.2. [role.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway-controller/templates/role.yaml)
- **Kind:** `ClusterRole` (`rbac.authorization.k8s.io/v1`)
- **Metadata:** Name `agentgateway-{{ .Release.Namespace }}`.
- **Functionality:**
  - Grants full CRUD on core Kubernetes resources: `ConfigMaps`, `Services`, `Secrets`, `ServiceAccounts`, and `Events`.
  - Grants read-only access on `Endpoints`, `Namespaces`, `Nodes`, and `Pods`.
  - Grants full CRUD on Gateway API CRDs (`gateway.networking.k8s.io`) and AgentGateway CRDs (`agentgateway.dev`).

### 3.3. [serviceaccount.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway-controller/templates/serviceaccount.yaml)
- **Kinds:** `ServiceAccount` (`v1`) + `ClusterRoleBinding` (`rbac.authorization.k8s.io/v1`) — rendered together in one file, conditional on `serviceAccount.create: true`.
- **Functionality:**
  - Creates the execution identity for the controller pod.
  - Immediately binds it to the `ClusterRole` via `ClusterRoleBinding` named `agentgateway-role-{{ .Release.Namespace }}`.

### 3.4. [service.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway-controller/templates/service.yaml)
- **Kind:** `Service` (`v1`), conditional on `controller.service.enabled: true`.
- **Functionality:**
  - Internal `ClusterIP` service exposing three named ports:
    - `grpc-xds-agw`: Port `9978`
    - `health`: Port `9093`
    - `metrics`: Port `9092`
  - Supports advanced service configuration: dual-stack IP families, session affinity, and load balancer options.

### 3.5. [horizontalpodautoscaler.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway-controller/templates/horizontalpodautoscaler.yaml)
- **Contains three conditional resources:**
  - `HorizontalPodAutoscaler` (`autoscaling/v2`): When `controller.horizontalPodAutoscaler` is set.
  - `VerticalPodAutoscaler` (`autoscaling.k8s.io/v1`): When `controller.verticalPodAutoscaler` is set.
  - `PodDisruptionBudget` (`policy/v1`): When `controller.podDisruptionBudget` is set.
- **Functionality:** All three `scaleTargetRef` / `targetRef` / `selector` values are auto-configured by the chart to target the controller Deployment.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `controller.replicaCount` | integer | `1` | Number of controller control plane instances. |
| `controller.logLevel` | string | `"info"` | Logging verbosity (`debug`, `info`, `warn`, `error`). |
| `image.registry` | string | `"cr.agentgateway.dev"` | Default container registry for all chart images. |
| `controller.image.repository` | string | `"controller"` | Controller image name (combined with registry). |
| `controller.service.enabled` | boolean | `true` | Toggles Service resource creation. |
| `controller.service.ports.agwGrpc` | integer | `9978` | xDS gRPC port (proxies connect here). |
| `controller.service.ports.metrics` | integer | `9092` | Prometheus metrics scrape port. |
| `controller.service.ports.health` | integer | `9093` | Health/liveness probe port. |
| `controller.xds.tls.enabled` | boolean | `false` | Enables mTLS for xDS gRPC control plane streams. When enabled, create `Secret/agentgateway-xds-cert` with `tls.crt`, `tls.key`, `ca.crt`. |
| `controller.horizontalPodAutoscaler` | object | `{}` | HPA spec (empty = HPA disabled). |
| `controller.verticalPodAutoscaler` | object | `{}` | VPA spec (empty = VPA disabled). |
| `controller.podDisruptionBudget` | object | `{}` | PDB spec (empty = PDB disabled). |
| `serviceAccount.create` | boolean | `true` | Creates ServiceAccount and ClusterRoleBinding. |
| `inferenceExtension.enabled` | boolean | `false` | Enables AI inference routing extensions for local LLM workloads. |
| `discoveryNamespaceSelectors` | list | `[]` | Restricts Gateway API resource discovery to specific namespaces by label selector. |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

- **Dynamic Discovery:** In 01-Sandbox, ephemeral sandboxes are provisioned dynamically. The controller monitors `sandbox-api-service` endpoints and updates Envoy routes within milliseconds of a pod becoming Ready.
- **Observability:** Telemetry exposed on port `9092` is scraped by the local Prometheus instance, tracking translation latency, xDS client connections, and Gateway API validation errors.

---

## 6. Practical Debugging & Troubleshooting

### Inspect Controller Status & Logs
```bash
# Check if the controller deployment is running
kubectl get deployment -n agentgateway-system
kubectl get pods -n agentgateway-system

# Stream controller logs to debug route translation errors
kubectl logs -n agentgateway-system -l app.kubernetes.io/name=agentgateway -f --tail=100
```

### Query Controller Health & Metrics
```bash
# Port-forward to query health check endpoint
kubectl port-forward -n agentgateway-system svc/agentgateway 9093:9093
curl http://localhost:9093/healthz

# Port-forward to check Prometheus metrics
kubectl port-forward -n agentgateway-system svc/agentgateway 9092:9092
curl http://localhost:9092/metrics | grep -i gateway
```

### Common Failure Modes
1. **CrashLoopBackOff / Permission Denied:** The `ClusterRoleBinding` is missing or was not created (e.g., `serviceAccount.create: false` with no pre-existing SA). Verify with `kubectl describe clusterrole agentgateway-agentgateway-system`.
2. **Proxies Not Updating (Stale Routes):** If proxies report stale routes after creating a new HTTPRoute, the proxies may have lost their xDS gRPC stream. Check controller logs for reconnection errors and test: `nc -zv <controller-svc>.agentgateway-system.svc.cluster.local 9978`.
3. **Invalid GatewayClass (`Programmed: False`):** The controller only processes Gateways whose `spec.gatewayClassName` matches `agentgateway`. If the value in `agentgateway/values.yaml` → `gateway.gatewayClassName` is different, the Gateway remains unacknowledged.
