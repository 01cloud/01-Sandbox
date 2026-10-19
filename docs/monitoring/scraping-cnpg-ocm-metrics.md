# Scraping Cloud Native PostgreSQL & Open Cluster Management Prometheus Metrics

This guide explains how to expose, configure, and scrape Prometheus metrics for **Cloud Native PostgreSQL (CNPG)** and **Open Cluster Management (OCM)** components without deploying Grafana, dashboards, or any UI frontend.

---

## 1. Metrics Ports & Architecture Overview

| Component | Namespace | Container Port | Service Name | Metrics Path | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **CNPG PostgreSQL Instance** | `opensandbox-system` | `9187` | `postgresql-primary-metrics`<br>`postgresql-secondary-metrics` | `/metrics` | Native instance-manager metrics (replication lag, active backends, database size, transactions, WAL state). |
| **CNPG Operator** | `opensandbox-system` | `8080` | `cnpg-operator-metrics` | `/metrics` | Operator reconcile rates, queue depth, admission webhook latency. |
| **OCM Hub Registration Controller** | `open-cluster-management-hub` | `8080` | `ocm-registration-controller-metrics` | `/metrics` | CSR approvals, client cert leases, hub registration reconcile loops. |
| **OCM Hub Work Controller** | `open-cluster-management-hub` | `8080` | `ocm-work-controller-metrics` | `/metrics` | ManifestWork distribution, status sync, work queue depths. |
| **OCM Hub Placement Controller** | `open-cluster-management-hub` | `8080` | `ocm-placement-controller-metrics` | `/metrics` | Cluster placement decisions, scheduling latency. |
| **OCM Klusterlet Agents** | `open-cluster-management-agent` | `8080` | `ocm-klusterlet-registration-metrics`<br>`ocm-klusterlet-work-metrics` | `/metrics` | Spoke agent lease renewal, CSR generation, ManifestWork actuation. |

---

## 2. Key Metrics for Health & Performance Tracking

### Cloud Native PostgreSQL (Port 9187)
- **Replication Lag & Standby Health**:
  - `cnpg_pg_replication_lag`: Replication delay behind primary in seconds.
  - `cnpg_pg_stat_replication_replay_lag_seconds`: Replay delay on standby replica.
  - `cnpg_pg_stat_replication_write_diff_bytes`: Byte difference between master WAL write and standby write.
- **Connections & Utilization**:
  - `cnpg_backends_total{state="active"}`: Active database client connections.
  - `cnpg_backends_total{state="idle in transaction"}`: Long-running idle transactions.
  - `cnpg_database_size_bytes`: Database size in bytes.
- **Transactions & Errors**:
  - `cnpg_pg_stat_database_xact_commit`: Total committed transactions.
  - `cnpg_pg_stat_database_xact_rollback`: Total rolled back transactions.
  - `cnpg_pg_stat_database_deadlocks`: Total deadlocks encountered.
  - `cnpg_pg_stat_archiver_failed_count`: Number of failed WAL archive attempts.

### Open Cluster Management (Port 8080)
- **Controller Reconcile Performance**:
  - `controller_runtime_reconcile_total`: Total reconciliations performed per controller.
  - `controller_runtime_reconcile_errors_total`: Count of reconciliation failures.
  - `controller_runtime_reconcile_time_seconds`: Latency distribution of reconciliation routines.
- **Workqueue & Task Latency**:
  - `workqueue_depth`: Current backlog of items waiting for processing.
  - `workqueue_queue_duration_seconds`: Time tasks spend waiting in queue before execution.
  - `workqueue_work_duration_seconds`: Execution duration of controller workers.
- **Kubernetes API Server Interaction**:
  - `rest_client_requests_total`: Outbound calls to kube-apiserver by HTTP code and resource.
  - `rest_client_request_duration_seconds`: Round-trip request duration to the API server.

---

## 3. How to Apply the Configurations

### Option A: Standalone Manifests (Recommended for Quick Setup)

The manifests are organized in `manifests/monitoring/`:

```bash
# 1. Apply CNPG metrics services (port 9187 for DB, port 8080 for Operator)
kubectl --context kind-primaryhub apply -f manifests/monitoring/01-cnpg-metrics.yaml

# 2. Apply OCM Hub metrics services (port 8080 for registration, work, placement)
kubectl --context kind-primaryhub apply -f manifests/monitoring/02-ocm-metrics.yaml

# 3. Apply Headless Prometheus Scraper (scrapes all endpoints, stores time series, no UI)
kubectl --context kind-primaryhub apply -f manifests/monitoring/03-prometheus-scraper.yaml
```

If configuring `secondaryhub` or `spoke1`/`spoke2`:
```bash
# On secondaryhub:
kubectl --context kind-secondaryhub apply -f manifests/monitoring/01-cnpg-metrics.yaml
kubectl --context kind-secondaryhub apply -f manifests/monitoring/02-ocm-metrics.yaml
kubectl --context kind-secondaryhub apply -f manifests/monitoring/03-prometheus-scraper.yaml

# On spoke clusters (for Klusterlet agents):
kubectl --context kind-spoke1 apply -f manifests/monitoring/02-ocm-metrics.yaml
```

### Option B: Patch Running CNPG Clusters Directly

To ensure live CNPG instance pods inherit Prometheus scrape annotations immediately:

```bash
# On primaryhub:
kubectl --context kind-primaryhub patch cluster postgresql-primary -n opensandbox-system --type=merge -p '{
  "spec": {
    "inheritedMetadata": {
      "annotations": {
        "prometheus.io/scrape": "true",
        "prometheus.io/port": "9187",
        "prometheus.io/path": "/metrics"
      }
    }
  }
}'

# On secondaryhub:
kubectl --context kind-secondaryhub patch cluster postgresql-secondary -n opensandbox-system --type=merge -p '{
  "spec": {
    "inheritedMetadata": {
      "annotations": {
        "prometheus.io/scrape": "true",
        "prometheus.io/port": "9187",
        "prometheus.io/path": "/metrics"
      }
    }
  }
}'
```

### Option C: Via Helm Chart (`codeInspector`)

The `codeInspector` umbrella chart has been pre-configured with the updated scrape jobs and services:

```bash
helm upgrade --install codeinspector ./codeInspector \
  --kube-context kind-primaryhub \
  --namespace opensandbox-system \
  --set prometheus.enabled=true \
  --set grafana.enabled=false
```

---

## 4. How to Scrape Metrics Directly (Without Any UI)

### 1. Verification Script
Run the automated verification script:
```bash
./manifests/monitoring/verify-metrics.sh kind-primaryhub
```

### 2. Direct Curl via Port-Forwarding

#### A. Scrape PostgreSQL Instance Metrics (Port 9187)
```bash
kubectl --context kind-primaryhub port-forward -n opensandbox-system svc/postgresql-primary-metrics 9187:9187 &
curl -s http://localhost:9187/metrics | grep cnpg_
```

#### B. Scrape CloudNativePG Operator Metrics (Port 8080)
```bash
kubectl --context kind-primaryhub port-forward -n opensandbox-system svc/cnpg-operator-metrics 8080:8080 &
curl -s http://localhost:8080/metrics | grep controller_runtime_
```

#### C. Scrape OCM Registration Controller Metrics (Port 8080)
```bash
kubectl --context kind-primaryhub port-forward -n open-cluster-management-hub svc/ocm-registration-controller-metrics 8081:8080 &
curl -s http://localhost:8081/metrics | grep workqueue_
```

#### D. Scrape OCM Work Controller Metrics (Port 8080)
```bash
kubectl --context kind-primaryhub port-forward -n open-cluster-management-hub svc/ocm-work-controller-metrics 8082:8080 &
curl -s http://localhost:8082/metrics | grep controller_runtime_reconcile
```

#### E. Scrape OCM Klusterlet Agent Metrics on Spoke (Port 8080)
```bash
kubectl --context kind-spoke1 port-forward -n open-cluster-management-agent svc/ocm-klusterlet-registration-metrics 8083:8080 &
curl -s http://localhost:8083/metrics
```

### 3. Querying the Headless Prometheus Scraper Engine

The headless Prometheus server runs on port `9090` in the `monitoring` namespace. You can query it directly via the Prometheus HTTP API:

```bash
kubectl --context kind-primaryhub port-forward -n monitoring svc/prometheus-scraper 9090:9090 &

# Check all active scrape targets and health status:
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {job: .labels.job, instance: .labels.instance, health: .health}'

# Query current replication lag:
curl -s "http://localhost:9090/api/v1/query?query=cnpg_pg_replication_lag" | jq .

# Query OCM reconcile error rate:
curl -s "http://localhost:9090/api/v1/query?query=rate(controller_runtime_reconcile_errors_total[5m])" | jq .
```
