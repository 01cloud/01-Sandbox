# Grafana Observability Dashboard Helm Chart (`grafana`)

## 1. Overview & Problem Statement

In a multi-cluster, multi-region distributed system like **01-Sandbox**, tracking system health, CPU/memory saturation, gVisor sandbox container counts, database transactions, and network latency across different regions (us-east-1, eu-west-1, eu-central-1, ap-south-1) requires a unified visualization pane.

Manually configuring datasources and dashboards in a web UI is error-prone, fragile, and contrary to **GitOps / Infrastructure-as-Code (IaC)** principles. If the Grafana pod restarts or crashes without persistent storage, all custom dashboards and data source credentials could be wiped out.

The `grafana` Helm chart automates declarative dashboard provisioning, multi-cluster datasource registration (connecting to PrimaryHub Prometheus, Spoke Prometheus instances over WireGuard, and Cortex S3 Long-Term Storage), and Gateway API ingress routing — with zero manual UI setup.

```
       [ DevOps Engineers / SREs / Platform Admins ]
                             │
                             ▼
     ┌────────────────────────────────────────────────┐
     │  Gateway API HTTPRoute / NodePort (port 32000) │
     │  Hostname: api-sandbox.01security.com/grafana  │
     └──────────────────────┬─────────────────────────┘
                            │
                            ▼
              ┌─────────────────────────────┐
              │     Grafana Pod v11.1.0     │
              │  Port 3000 (containerPort)  │
              │  GF_SECURITY_ALLOW_EMBEDDING│
              │  GF_USERS_ALLOW_SIGN_UP:false│
              └──────┬───────────────┬──────┘
                     │               │
      Auto-Provisioned               Auto-Provisioned
      Dashboards (ConfigMap)         Data Sources (ConfigMap)
      - codeinspector-dashboard.json - primaryhub (local Prometheus)
      - Mounted at:                  - secondaryhub, spoke1-3 (WireGuard)
        /var/lib/grafana-dashboards  - Cortex S3 long-term storage
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

- **Declarative Provisioning via ConfigMaps:** Grafana reads datasources from `/etc/grafana/provisioning/datasources` and dashboards from `/etc/grafana/provisioning/dashboards` at startup. By mounting ConfigMaps into these directories, Grafana starts fully configured without human intervention.
- **Volatile vs. Persistent Storage:** `persistence.enabled: false` means Grafana's `/var/lib/grafana` is backed by an `emptyDir` volume. If the pod restarts, the SQLite database is lost. This is intentional here — all configuration is code (ConfigMaps), so there is nothing valuable to persist. Enable persistence only if you store custom user preferences or alerts in the database.
- **NodePort vs. Gateway Routing:**
  - `NodePort` (port `32000`): Allows administrators to reach Grafana directly on the EC2 host IP for maintenance or disaster recovery.
  - `HTTPRoute` (`httproute.yaml`): Exposes Grafana through the Gateway API under `/grafana` with hostname matching, rate limiting, and referencing the `grafana-service` cross-namespace.
- **Cross-Namespace `ReferenceGrant`:** Grafana runs in `opensandbox-system`; the Gateway proxy runs in `agentgateway-system`. The `ReferenceGrant` explicitly authorizes the Gateway's HTTPRoute to reference the `grafana-service` Service object.
- **Subpath Routing:** When `ingressRoute.enabled: true`, the chart sets:
  - `GF_SERVER_ROOT_URL: "%(protocol)s://%(domain)s:%(http_port)s{{ .Values.ingressPath }}"` — Grafana's templated URL formatter generates correct absolute URLs for redirect chains and asset loading.
  - `GF_SERVER_SERVE_FROM_SUB_PATH: "true"` — Strips the subpath prefix before routing internally.

---

## 3. Template-by-Template Manifest Breakdown

The templates in [codeInspector/charts/grafana/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/) configure:

### 3.1. [deployment.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/deployment.yaml)
- **Kind:** `Deployment` (`apps/v1`), name: `grafana`.
- **Functionality:**
  - Deploys `grafana/grafana:11.1.0`.
  - Container port: `3000`.
  - **Environment variables injected:**
    - `GF_SERVER_ROOT_URL` (conditional on `ingressRoute.enabled`): Uses Grafana's Python-style `%(protocol)s://%(domain)s:%(http_port)s` templating.
    - `GF_SERVER_SERVE_FROM_SUB_PATH: "true"` (conditional).
    - `GF_SECURITY_ALLOW_EMBEDDING: "true"`: Allows embedding Grafana panels in iframes.
    - `GF_SECURITY_ADMIN_PASSWORD: {{ .Values.adminPassword }}`: Sets the initial admin password.
    - `GF_USERS_ALLOW_SIGN_UP: "false"`: Disables self-registration.
  - **Volume Mounts:**
    - `/etc/grafana/provisioning/datasources` ← `ConfigMap/grafana-datasources`
    - `/etc/grafana/provisioning/dashboards` ← `ConfigMap/grafana-dashboards-provider`
    - `/var/lib/grafana-dashboards` ← `ConfigMap/grafana-dashboard-codeinspector`
    - `/var/lib/grafana` ← `emptyDir` or PVC (depending on `persistence.enabled`)

### 3.2. [configmap-datasources.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/configmap-datasources.yaml)
- **Kind:** `ConfigMap`, name: `grafana-datasources`.
- **Functionality:**
  - Declares all Prometheus datasource connections. When `.Values.datasources` is set, renders the custom list; otherwise auto-generates:
    1. `primaryhub` (default): `http://prometheus-service.opensandbox-system.svc.cluster.local:9090` with Basic Auth.
    2. `secondaryhub`, `spoke1`, `spoke2`, `spoke3`: Remote Prometheus over WireGuard overlay IPs (`10.99.0.x:30900`), each also authenticated.
  - Marked `editable: false` to prevent accidental UI overrides being lost on restart.

### 3.3. [configmap-dashboards.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/configmap-dashboards.yaml)
- **Kinds:** Two ConfigMaps.
  1. **`grafana-dashboards-provider`**: Defines the Grafana dashboard provisioning configuration pointing to `/var/lib/grafana-dashboards` with `disableDeletion: false` and `editable: true`.
  2. **`grafana-dashboard-codeinspector`**: Contains the full JSON definition of the 01-Sandbox / CodeInspector monitoring dashboard (annotation configs, panel definitions, queries, etc.).

### 3.4. [service.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/service.yaml)
- **Kind:** `Service`, name: `grafana-service`.
- **Functionality:** Type `NodePort`, port `3000`, NodePort `32000`.

### 3.5. [httproute.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/httproute.yaml)
- Routes `agentgateway-proxy` traffic for path `/grafana` and hostnames from `.Values.ingressHostnames` to `grafana-service:3000`.

### 3.6. [policy.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/policy.yaml)
- Applies an `AgentgatewayPolicy` for rate limiting or access control on the Grafana HTTPRoute.

### 3.7. [referencegrant.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/referencegrant.yaml)
- `ReferenceGrant` allowing `agentgateway-system` Gateway HTTPRoutes to reference `grafana-service` in `opensandbox-system`.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `namespace` | string | `"opensandbox-system"` | Target namespace for Grafana. |
| `prometheusServiceName` | string | `"prometheus-service"` | Service name of the local Prometheus. |
| `prometheusNamespace` | string | `"opensandbox-system"` | Namespace of the local Prometheus service. |
| `prometheusPort` | integer | `9090` | Local Prometheus port. |
| `prometheusAuth.enabled` | boolean | `true` | Enables Basic Auth on the local Prometheus datasource. |
| `prometheusAuth.username` | string | `"prometheus-rw"` | Username for the local Prometheus query. |
| `prometheusAuth.password` | string | `"PrometheusRemoteWriteSecure2026!"` | Password for the local Prometheus query. |
| `remoteClusters` | object | `{secondaryhub, spoke1, spoke2, spoke3}` | Map of remote cluster Prometheus URLs over WireGuard. |
| `replicaCount` | integer | `1` | Number of Grafana pod replicas. |
| `service.type` | string | `"NodePort"` | Service type. |
| `service.nodePort` | integer | `32000` | NodePort for direct host-level access. |
| `ingressRoute.enabled` | boolean | `true` | Enables Gateway API HTTPRoute and subpath routing. |
| `ingressHostnames` | list | `["api-sandbox.01security.com", "10.0.10.9"]` | Hostnames matched by the HTTPRoute. |
| `ingressPath` | string | `"/grafana"` | Subpath for the Grafana web UI. |
| `adminPassword` | string | `"admin"` | Initial admin password. **Must be changed in production.** |
| `persistence.enabled` | boolean | `false` | Mounts a PVC for durable SQLite storage. |
| `image.tag` | string | `"11.1.0"` | Grafana container image version. |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

- **Single Pane of Glass:** SREs and platform developers observe real-time health across all 4 AWS regions simultaneously from a single dashboard.
- **Dual-Horizon Querying:**
  - **Short-Term (0-7 days):** Queries the local Prometheus TSDB (`primaryhub` datasource) for fast, high-resolution triage.
  - **Long-Term (> 7 days):** Queries Cortex S3 long-term storage to analyze seasonal trends, customer usage growth, and compliance audits.

---

## 6. Practical Debugging & Troubleshooting

### Check Pod & Service Status
```bash
# Verify Grafana pod is running
kubectl get pods -n opensandbox-system -l app=grafana
kubectl logs -n opensandbox-system -l app=grafana -f --tail=100
```

### Inspect Provisioned Configuration Inside the Pod
```bash
# View the auto-provisioned datasources file
kubectl exec -it -n opensandbox-system deploy/grafana -- \
  cat /etc/grafana/provisioning/datasources/datasources.yaml

# Verify the dashboard JSON file is mounted
kubectl exec -it -n opensandbox-system deploy/grafana -- \
  ls /var/lib/grafana-dashboards/
```

### Common Failure Modes
1. **Datasource Error "Bad Gateway" / "Connection Refused":** Grafana cannot reach the Prometheus service. Verify `prometheusServiceName` and `prometheusNamespace` values match the actual deployed Prometheus Service name.
2. **Subpath Redirect Loop (`/grafana` → `/login` → 404):** Ensure `ingressRoute.enabled: true` to populate `GF_SERVER_ROOT_URL` and `GF_SERVER_SERVE_FROM_SUB_PATH`. The root URL format must use Grafana's `%(protocol)s://%(domain)s` syntax — do not hardcode a literal URL.
3. **Dashboard Edits Disappearing on Pod Restart:** Because `persistence.enabled: false`, all Grafana state is in-memory. Changes made via UI are lost on restart. Export dashboard JSON and add it to `configmap-dashboards.yaml` to persist edits.
4. **NodePort 32000 Not Reachable:** Check that the EC2 Security Group allows inbound TCP on port 32000 from your IP.
