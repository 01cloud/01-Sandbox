# Prometheus Metrics Engine & Rotator Helm Chart (`prometheus`)

## 1. Overview & Problem Statement

Observability is the bedrock of reliable distributed microservices. In complex multi-cluster environments like **01-Sandbox**, operations teams must collect millions of time-series metric data points every minute:
- API server HTTP request latencies and 5xx error rates.
- Container CPU, memory, and disk I/O metrics via Kubernetes pod annotations.
- PostgreSQL database connections, transaction counts, and replication lag (via CloudNativePG exporter on port 9187).
- OCM hub controller reconciliation rates, webhook admission success rates, and workqueue depth.
- Custom OCM SLO recording rules (SLI: 99.9% webhook admission rate, P99 webhook latency < 0.2s).

The `prometheus` chart deploys a production-grade **Prometheus TSDB engine** with remote-write streaming to Cortex S3, Basic Auth protection, Kubernetes service-discovery RBAC, and **two automated credential rotation CronJobs** running on separate monthly schedules.

```
    ┌────────────────────────────────────────────────────────┐
    │ Scrape Targets (Kubernetes SD + Static Configs)        │
    │  - 'prometheus' self-scrape (localhost:9090)           │
    │  - 'fastapi-api-server' (sandbox-api-service:80)       │
    │  - 'cloudnative-pg-instances' (SD by cnpg label, :9187)│
    │  - 'cloudnative-pg-operator' (SD by app name, :8080)   │
    │  - 'open-cluster-management-hub' (SD by app, :8080)    │
    │  - 'kubernetes-services' (annotation-based SD)         │
    │  - 'kubernetes-pods' (annotation-based SD)             │
    └───────────────────────────┬────────────────────────────┘
                                │ 15s pull interval
                                ▼
    ┌────────────────────────────────────────────────────────┐
    │ Prometheus Engine (v2.54.1)                            │
    │  - Local TSDB: 25Gi PVC, 7d retention                  │
    │  - Basic Auth: /etc/prometheus-web/web.yml             │
    │  - Flags: --web.enable-lifecycle                       │
    │           --web.enable-remote-write-receiver           │
    │           --web.config.file=/etc/prometheus-web/web.yml│
    │  - OCM SLO recording rules (ocm-slo-rules.yml)         │
    └───────────────┬────────────────────────┬───────────────┘
                    │                        │
     remote_write   │ (HTTPS :8443, monthly  │ SIGHUP via /-/reload
     to Cortex      │  rotating basic_auth)  │ (enabled by lifecycle flag)
                    ▼                        ▼
       ┌────────────────────────┐  ┌──────────────────────────────┐
       │ Cortex S3 Long-Term    │  │ Two CronJob Rotators         │
       │ (10.99.0.50:30443)     │  │ rotator: "0 0 1 * *" (00:00) │
       └────────────────────────┘  │ cortexRotator: "0 2 1 * *"   │
                                   │               (02:00 UTC)    │
                                   └──────────────────────────────┘
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

- **Pull-Based Scraping with Kubernetes Service Discovery (SD):** Prometheus discovers scrape targets automatically by watching the Kubernetes API for Pods and Services with specific labels or annotations. No manual target registration needed when new pods appear.
- **Recording Rules (SLOs):** Pre-compute complex PromQL expressions into new time-series. For example, `ocm:admission_success_rate:ratio_rate5m` is computed every evaluation interval and stored as a simple gauge — making dashboard queries faster and historical SLO tracking possible.
- **Dynamic Configuration Reload (SIGHUP via `--web.enable-lifecycle`):** Sending `POST http://localhost:9090/-/reload` causes Prometheus to re-read all config files from disk and apply changes in ~24ms. The CronJob rotators use this after patching Kubernetes Secrets/ConfigMaps.
- **Remote Write Receiver (`--web.enable-remote-write-receiver`):** Allows Prometheus to also **accept** incoming remote_write from other Prometheus instances (Spokes pushing into PrimaryHub), making PrimaryHub both a scraper and a remote_write aggregation point.
- **Web Config (`web.yml`):** A separate YAML file (not `prometheus.yml`) defining `basic_auth_users` with bcrypt-hashed passwords. Mounted as a Kubernetes Secret at `/etc/prometheus-web/web.yml`.
- **Two Separate CronJobs:** The rotators have intentionally different schedules:
  - `prometheus-secret-rotator`: `"0 0 1 * *"` — runs at **00:00 UTC** on the 1st of each month.
  - `cortex-secret-rotator`: `"0 2 1 * *"` — runs at **02:00 UTC** on the 1st of each month (2 hours later to allow any inter-dependent dependent rotations to settle).

---

## 3. Template-by-Template Manifest Breakdown

The templates in [codeInspector/charts/prometheus/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/) deploy:

### 3.1. [deployment.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/deployment.yaml)
- **Kind:** `Deployment` (`apps/v1`), name `prometheus`.
- **ServiceAccount:** `prometheus` (created in `rbac.yaml`).
- **Container flags:**
  - `--config.file=/etc/prometheus/prometheus.yml`
  - `--storage.tsdb.path=/prometheus`
  - `--storage.tsdb.retention.time={{ .Values.retention }}` (default `7d`)
  - `--web.enable-lifecycle` (enables `/-/reload`)
  - `--web.enable-remote-write-receiver` (accepts push from spokes)
  - `--web.config.file=/etc/prometheus-web/web.yml` (conditional on `auth.enabled`)
  - `--web.external-url={{ .Values.ingressPath }}` (conditional on `ingressRoute.enabled`)
- **Volume mounts:**
  - `/etc/prometheus` ← `ConfigMap/prometheus-config`
  - `/etc/prometheus-web` ← `Secret/prometheus-web-config` (conditional on `auth.enabled`)
  - `/prometheus` ← PVC `prometheus-pvc` or `emptyDir`

### 3.2. [configmap.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/configmap.yaml)
- **Kind:** `ConfigMap`, name `prometheus-config`. Contains two data keys:
  1. **`prometheus.yml`:** Full Prometheus configuration.
     - `global`: `scrape_interval: 15s`, `scrape_timeout: 10s`, `evaluation_interval: 15s`, `external_labels: {cluster, cluster_role}`.
     - `rule_files: [/etc/prometheus/ocm-slo-rules.yml]`.
     - `remote_write`: Rendered from `.Values.remoteWrite` (points to Cortex `10.99.0.50:8443` with Basic Auth `cortex-writer` / `CortexWriterSecret2026!`).
     - `scrape_configs`: 7 jobs — `prometheus`, `fastapi-api-server`, `cloudnative-pg-instances`, `cloudnative-pg-operator`, `open-cluster-management-hub`, `kubernetes-services`, `kubernetes-pods`.
  2. **`ocm-slo-rules.yml`:** Recording rules for OCM SLIs (admission success rate, admission errors, webhook P99 latency, reconcile success rate, workqueue depth) and PostgreSQL HA rules (replication lag, WAL streaming bytes, streaming replicas count, WAL archival failures — rendered only when `clusterRole == "hub"`).

### 3.3. [rbac.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/rbac.yaml)
- **Kinds:** `ServiceAccount`, `ClusterRole/prometheus-cluster-role`, `ClusterRoleBinding/prometheus-cluster-role-binding`.
- **Permissions:** `get`, `list`, `watch` on `nodes`, `nodes/metrics`, `services`, `endpoints`, `pods`; `get` on `/metrics` non-resource URL.

### 3.4. [secret-web-config.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/secret-web-config.yaml)
- **Kind:** `Secret`, name `prometheus-web-config`. Conditional on `auth.enabled`.
- **Key:** `web.yml` with `basic_auth_users` block. Renders users from `.Values.auth.users` map if set, otherwise defaults to `{ .Values.auth.remoteWrite.username: .Values.auth.remoteWrite.hashedPassword }`.

### 3.5. [rotator-cronjob.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/rotator-cronjob.yaml)
- **Conditional:** Rendered when `.Values.rotator.enabled: true`.
- **Kinds:** `ServiceAccount`, `ClusterRole/prometheus-secret-rotator-role`, `ClusterRoleBinding`, `ConfigMap/prometheus-rotator-script`, and `CronJob/prometheus-secret-rotator`.
- **Schedule:** `.Values.rotator.schedule` (default `"0 0 1 * *"` — 00:00 UTC on the 1st).
- **ClusterRole permissions:** `get`, `list`, `watch`, `create`, `update`, `patch`, `delete` on `secrets`, `configmaps`, `pods`, `pods/exec`; also read access on `deployments`, `managedclusters`, and `manifestworks`.
- **CronJob spec:** Init container copies `kubectl` to a shared `emptyDir`; main container runs `python3 /scripts/rotate.py`.
- **Rotation steps (Python):** Generates a new bcrypt-hashed password, patches `Secret/prometheus-web-config`, updates `ConfigMap/prometheus-config` with the new remote_write credentials, optionally syncs to AWS SSM Parameter Store, runs a 10-minute grace period, then prunes the old credential.

### 3.6. [cortex-rotator-cronjob.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/cortex-rotator-cronjob.yaml)
- **Conditional:** Rendered when `.Values.cortexRotator.enabled: true`.
- **Schedule:** `.Values.cortexRotator.schedule` (default `"0 2 1 * *"` — 02:00 UTC on the 1st).
- **CronJob `cortex-secret-rotator`:** Uses `cortex-secret-rotator` ServiceAccount (separate from prometheus rotator SA).
- **Volume: `cortex-kubeconfig-vol`:** Mounts `Secret/cortex-rotator-kubeconfig` at `/etc/cortex-kubeconfig` to enable cross-cluster `kubectl` commands to the Cortex EC2 cluster.
- **Rotation steps (Python in `cortex-rotator-script` ConfigMap):** Generates timestamped usernames (`cortex-writer-YYYYMMDDHHSS`), appends new credentials to Cortex's `.htpasswd` Secret, updates PrimaryHub's `prometheus-config` with new remote_write credentials, triggers SIGHUP on Prometheus, waits 10-minute grace period, then prunes old rotated credentials from Cortex `.htpasswd`.

### 3.7. Supporting Templates
- [pvc.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/pvc.yaml): `PVC/prometheus-pvc`, 25Gi, StorageClass `standard`.
- [service.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/service.yaml): ClusterIP on port 9090 + NodePort 30900.
- [cnpg-metrics-services.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/cnpg-metrics-services.yaml): Internal Services for CNPG database metrics on port 9187.
- [ocm-metrics-services.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/ocm-metrics-services.yaml): Internal Services for OCM hub controller metrics.
- [httproute.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/httproute.yaml), [policy.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/policy.yaml), [referencegrant.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/referencegrant.yaml): Gateway API routing and access control.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `namespace` | string | `"opensandbox-system"` | Namespace for all Prometheus resources. |
| `clusterName` | string | `"primaryhub"` | Injected as `external_labels.cluster`. |
| `clusterRole` | string | `"hub"` | Injected as `external_labels.cluster_role`. Also gates PostgreSQL HA recording rules. |
| `retention` | string | `"7d"` | Local TSDB data retention. |
| `replicaCount` | integer | `1` | Prometheus pod replica count. |
| `persistence.enabled` | boolean | `true` | Mounts PVC for TSDB storage. |
| `persistence.size` | string | `"25Gi"` | PVC size. |
| `service.nodePort.port` | integer | `30900` | NodePort for WireGuard cross-cluster access. |
| `auth.enabled` | boolean | `true` | Enables Web API Basic Authentication. |
| `auth.remoteWrite.username` | string | `"prometheus-rw"` | Web auth username for Prometheus API/UI. |
| `auth.remoteWrite.hashedPassword` | string | `"$2y$10$..."` | Bcrypt-hashed password for the web config. |
| `auth.remoteWrite.plainPassword` | string | `"PrometheusRemoteWriteSecure2026!"` | Plaintext password (used by rotator). |
| `remoteWrite` | list | (Cortex URL) | List of remote_write targets (`url`, `tls_config`, `basic_auth`, `queue_config`). |
| `rotator.enabled` | boolean | `true` | Enables the Prometheus web auth rotator CronJob. |
| `rotator.schedule` | string | `"0 0 1 * *"` | Monthly cron at 00:00 UTC on the 1st. |
| `rotator.gracePeriodSeconds` | integer | `600` | Overlap window before pruning old credentials. |
| `cortexRotator.enabled` | boolean | `true` | Enables the Cortex remote_write credential rotator CronJob. |
| `cortexRotator.schedule` | string | `"0 2 1 * *"` | Monthly cron at 02:00 UTC on the 1st. |
| `cortexRotator.gracePeriodSeconds` | integer | `600` | Overlap window before pruning old Cortex credentials. |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

- **Telemetry Hub:** Gathers metrics from every tier — Gateway, API server, database, OCM hub, and sandbox workloads.
- **Remote Write to Cortex:** Ships all scraped data to Cortex S3 for multi-year retention and cross-cluster historical querying.
- **Zero-Downtime Security Rotation:** The two staggered CronJobs (00:00 and 02:00 UTC) ensure credentials rotate automatically every month. The 10-minute grace overlap means active Prometheus remote_write connections are never interrupted.

---

## 6. Practical Debugging & Troubleshooting

### Inspect Prometheus Health and Reload
```bash
# Check pod status
kubectl get pods -n opensandbox-system -l app=prometheus

# Stream logs
kubectl logs -n opensandbox-system -l app=prometheus -f --tail=100

# Trigger manual SIGHUP reload (after editing ConfigMap/Secret)
kubectl exec -it -n opensandbox-system deploy/prometheus -- \
  curl -s -X POST http://localhost:9090/-/reload && echo "Reload triggered"
```

### Manually Trigger Rotator CronJobs
```bash
# Trigger a manual run of the Prometheus web auth rotator
kubectl create job --from=cronjob/prometheus-secret-rotator \
  test-prom-rotate -n opensandbox-system
kubectl logs -n opensandbox-system -l job-name=test-prom-rotate -f

# Trigger a manual run of the Cortex credential rotator
kubectl create job --from=cronjob/cortex-secret-rotator \
  test-cortex-rotate -n opensandbox-system
kubectl logs -n opensandbox-system -l job-name=test-cortex-rotate -f
```

### Common Failure Modes
1. **Remote Write Failing (401 Unauthorized):** Credentials in `remoteWrite.basic_auth` don't match Cortex's `.htpasswd`. Verify: `kubectl get secret cortex-auth-secret -n cortex -o jsonpath='{.data.\.htpasswd}' | base64 -d`.
2. **`/-/reload` Returns 403 Forbidden:** `--web.enable-lifecycle` flag is missing from the deployment args. Verify: `kubectl get deployment prometheus -n opensandbox-system -o jsonpath='{.spec.template.spec.containers[0].args}'`.
3. **Rotator Job Fails (CrashLoopBackOff):** Likely a Python import error or missing `kubectl` binary in the shared tool volume. Check: `kubectl logs -n opensandbox-system -l app=cortex-secret-rotator`.
4. **Self-scrape `/prometheus/metrics` returns 401:** When `ingressRoute.enabled: true`, the self-scrape job also uses `basic_auth`. Confirm the scrape config's `basic_auth.username/password` matches the web config.
