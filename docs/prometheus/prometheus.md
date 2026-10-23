# Prometheus Metrics & Observability Guide

## 1. Overview & Architecture

The Prometheus monitoring component is deployed as a lightweight, headless scraper inside the **`opensandbox-system`** namespace across the multi-cluster infrastructure.

### Key Characteristics:
* **Zero Disruption / Non-Invasive**: Scrapes PostgreSQL and Open Cluster Management (OCM) without requiring database restarts, custom agents, or cluster re-creation.
* **Headless Deployment**: Runs without an ingress or exposed external route by default, minimizing attack surface while remaining accessible via cluster-internal DNS, CLI, or secure SSH/SSM port-forwarding.
* **Automated SLO & SLI Evaluation**: Uses Prometheus recording rules evaluated continuously every 15 seconds to calculate Service Level Indicators (SLIs) against defined Service Level Objectives (SLOs).

```
                      ┌──────────────────────────────────────┐
                      │          Prometheus Scraper          │
                      │      (opensandbox-system:9090)       │
                      └──────────────────┬───────────────────┘
                                         │
        ┌────────────────────────────────┼──────────────────────────────┐
        │                                │                              │
        ▼                                ▼                              ▼
┌──────────────────┐           ┌───────────────────┐          ┌───────────────────┐
│ CloudNative-PG   │           │ Open Cluster Mgmt │          │  FastAPI Backend  │
│  Port 9187       │           │  Webhooks :8080   │          │  Port 80          │
│ • WAL Streaming  │           │ • Spoke Admission │          │ • Request Rates   │
│ • Replay Lag     │           │ • Join Latency    │          │ • Error Codes     │
│ • Sync Status    │           │ • Webhook Health  │          │ • API Durations   │
└──────────────────┘           └───────────────────┘          └───────────────────┘
```

---

## 2. High Availability PostgreSQL Replication Metrics

CloudNative-PG exposes full database metrics on port `9187`. The custom query configuration map (`cnpg-default-monitoring`) enables real-time physical streaming replication tracking.

### Active Recording Rules & Metrics

| Recording Rule / Metric | PromQL Expression | Target / SLO | Description |
| :--- | :--- | :--- | :--- |
| **`postgresql:replication_lag_seconds`** | `cnpg_pg_replication_lag` | `0s` ($< 5s$) | End-to-end replication lag in seconds from Primary to Standby |
| **`postgresql:streaming_replicas_count`** | `cnpg_pg_replication_streaming_replicas` | $\ge 1$ | Active streaming replica sessions connected to Primary |
| **`postgresql:wal_streaming_replay_lag_bytes`**| `cnpg_pg_stat_replication_stream_replay_lag_bytes` | $< 10MB$ | Bytes sent to Standby but not yet replayed |
| **`cnpg_pg_stat_replication_stream_write_lag_seconds`** | Native Exporter Metric | `0s` | Seconds elapsed between writing WAL locally and on standby |
| **`cnpg_pg_stat_replication_stream_flush_lag_seconds`** | Native Exporter Metric | `0s` | Seconds elapsed until WAL flushed to disk on standby |
| **`cnpg_pg_stat_replication_stream_replay_lag_seconds`**| Native Exporter Metric | `0s` | Seconds elapsed until WAL applied/replayed on standby |

---

## 3. Open Cluster Management (OCM) SLI / SLO Metrics

Open Cluster Management manages multi-region spoke clusters (`spoke1`, `spoke2`, `spoke3`, `spoke4`, `spoke5`). It exposes cluster admission, mutation, and validation metrics via its admission webhooks on port `8080`.

### Active Recording Rules & Metrics

| Recording Rule / Metric | Description | Target / SLO |
| :--- | :--- | :--- |
| **`ocm:admission_success_rate:ratio_rate5m`** | Ratio of HTTP 200 vs total admission requests over 5 min | $\ge 99.9\%$ |
| **`ocm:managedcluster_admissions_total`** | Total successful ManagedCluster admission and join validations | Continuous count |
| **`ocm:managedcluster_admission_errors_total`**| Total admission errors (HTTP 5xx) on ManagedCluster webhooks | `0` ($< 0.1\%$) |
| **`ocm:webhook_latency_seconds:p99`** | P99 latency of cluster admission webhooks | $< 200ms$ |
| **`controller:reconcile_success_rate:ratio_rate5m`**| Operator loop reconciliation success rate | $\ge 99.9\%$ |
| **`controller:workqueue_depth:sum`** | Active backlog depth across all controller workqueues | $< 10$ |

---

## 4. Querying Metrics in the EC2 Instance (CLI)

All metrics can be inspected directly via terminal without any graphical interface.

### A. Check Replication Lag & Replica Health
```bash
kubectl exec -n opensandbox-system deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/query?query={__name__=~\"cnpg_pg_stat_replication.*|postgresql:.*\"}" \
  | jq -r '.data.result[] | "\(.metric.__name__): \(.value[1])\"' | sort -u
```

### B. Check OCM Spoke Cluster Admission Success
```bash
# Total successful admissions:
kubectl exec -n opensandbox-system deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=sum(controller_runtime_webhook_requests_total{code=\"200\",app=~\"cluster-manager.*\"})"

# Total admission errors:
kubectl exec -n opensandbox-system deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=sum(controller_runtime_webhook_requests_total{code=\"500\",app=~\"cluster-manager.*\"})%20or%20vector(0)"
```

### C. Check All Active Scrape Targets and Status
```bash
kubectl exec -n opensandbox-system deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/targets" | jq -r '.data.activeTargets[] | "\(.labels.job) (\(.discoveredLabels.__address__)): \(.health)\"'
```

### D. Search and List Available Metric Names (490+ Total)
```bash
# List all metric names:
kubectl exec -n opensandbox-system deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/label/__name__/values" | jq -r '.data[]'

# Search for specific metrics (e.g., replication, memory, connection):
kubectl exec -n opensandbox-system deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/label/__name__/values" | jq -r '.data[]' | grep -i "replication"
```

---


---

## 5. Detailed Breakdown: What Each Scraped Metric Means

This section explains the exact meaning, thresholds, and operational relevance of the telemetry scraped by Prometheus.

### A. Webhook Metrics (`controller_runtime_webhook_*`)
Admission webhooks are the security and integrity gatekeepers of Kubernetes. Every time a spoke cluster registers or a database spec changes, it is intercepted and validated here.

| Metric | Type | What It Measures | Healthy / Ideal Value |
| :--- | :--- | :--- | :--- |
| **`controller_runtime_webhook_requests_total`** | Counter | Total number of admission calls intercepted, labeled by `code` (`200`, `400`, `500`) and `webhook` path. | `code="200"` steadily increments; `code="500"` should be **`0`**. |
| **`controller_runtime_webhook_requests_in_flight`** | Gauge | Number of admission calls currently being processed concurrently at this exact instant. | **`0`** (or very low). Any number $> 10$ sustained indicates CPU starvation or a webhook deadlock. |
| **`controller_runtime_webhook_latency_seconds_sum`** | Counter | Cumulative duration (in seconds) spent processing all requests across that webhook route. | Used with `latency_seconds_count` to derive average processing time. |
| **`controller_runtime_webhook_latency_seconds_count`** | Counter | Total number of requests measured for latency. | Matches total requests received. |
| **`controller_runtime_webhook_latency_seconds_bucket`** | Histogram | Prometheus cumulative buckets (e.g., `le="0.005"`, `le="0.025"`, `le="0.1"`) showing latency distributions. | 100% of counts should fall within `le="0.05"` ($< 50ms$). |

#### Webhook Routes Explained:
* **`/validate-cluster-...-managedcluster`**: Intercepts spoke cluster join requests. Validates that the cluster credentials, CSRs, and client certificates match the Hub's authority.
* **`/mutate-cluster-...-managedcluster`**: Automatically injects standard metadata, labels, and lease intervals into incoming spoke clusters upon acceptance.
* **`/validate-...-managedclustersetbinding`**: Ensures spoke clusters are correctly mapped to cluster sets for workload placement.
* **`/validate-work-...-manifestwork`**: Validates manifest payloads before they are pushed from the Hub to spoke clusters.
* **`/validate-...-cluster` & `/mutate-...-cluster`**: CloudNative-PG webhook that validates PostgreSQL cluster configurations, persistent volume specs, and backup policies.

---

### B. PostgreSQL Replication & WAL Streaming Metrics
These metrics are exported directly from PostgreSQL's internal `pg_stat_replication` and `pg_stat_wal_receiver` engine tables.

| Metric | Unit | What It Measures | Healthy / Ideal Value |
| :--- | :--- | :--- | :--- |
| **`cnpg_pg_stat_replication_stream_write_lag_seconds`** | Seconds | Delay between the Primary committing a transaction and the Standby saving the WAL data into its operating system memory buffer. | **`0s`** ($< 2s$). |
| **`cnpg_pg_stat_replication_stream_flush_lag_seconds`** | Seconds | Delay until the Standby successfully writes the WAL data to physical EBS disk (`fsync`). | **`0s`** ($< 5s$). |
| **`cnpg_pg_stat_replication_stream_replay_lag_seconds`** | Seconds | Delay until the Standby applies the WAL record to its database tables, making the data readable. | **`0s`** ($< 5s$). |
| **`cnpg_pg_stat_replication_stream_replay_lag_bytes`** | Bytes | Number of bytes of WAL stream that the Standby has received from the network but has not yet executed into database state. | **`0`** ($< 10MB$). |
| **`cnpg_pg_stat_replication_stream_sent_lag_bytes`** | Bytes | Total bytes written on Primary that have not yet crossed the network to the Standby. | **`0`** ($< 5MB$). |
| **`postgresql:streaming_replicas_count`** | Count | Number of active standby database instances currently streaming data from Primary. | **$\ge 1$** (at least 1 active standby). |
| **`state` label (`streaming`)** | State | Connection state of the replication socket between Primary and Standby. | Must be **`streaming`** (not `backup`, `catchup`, or `disconnected`). |
| **`sync_state` label (`async` / `sync`)** | Mode | Replication mode. In multi-region deployments, asynchronous (`async`) prevents cross-region network spikes from slowing down primary writes. | `async` (or `sync`). |

---

### C. Kubernetes Operator & Controller Workqueue Metrics (`workqueue_*`)
These metrics track the health of internal worker queues for operators (CNPG operator, OCM agent, AgentGateway).

| Metric | Unit | What It Measures | Healthy / Ideal Value |
| :--- | :--- | :--- | :--- |
| **`workqueue_depth`** | Gauge | Number of items currently queued up waiting for an operator reconciler thread to pick them up. | **`0`** (or $< 5$). A value $> 20$ means the operator cannot keep up with cluster changes. |
| **`workqueue_adds_total`** | Counter | Cumulative rate of events (creates, updates, deletes) pushed into the operator workqueue. | Monotonically increasing rate. |
| **`workqueue_queue_duration_seconds_bucket`** | Histogram | Time (in seconds) that an item spends sitting in the queue before processing starts. | Target: $P_{99} < 0.5s$. |
| **`workqueue_work_duration_seconds_bucket`** | Histogram | Actual execution time taken by the controller worker to reconcile one object. | Target: $P_{99} < 1.0s$. |
| **`workqueue_retries_total`** | Counter | Number of times a reconciliation job failed and had to be requeued for a retry. | Should remain stable; sudden spikes indicate failing API calls or Deadlocks. |

---

### D. PostgreSQL Core Engine & Activity Metrics
Exported from port `9187` on `postgresql-primary-1`:

| Metric | Unit | What It Measures | Healthy / Ideal Value |
| :--- | :--- | :--- | :--- |
| **`cnpg_pg_stat_database_xact_commit`** | Counter | Cumulative successful transaction commits. | Steadily increasing. |
| **`cnpg_pg_stat_database_xact_rollback`** | Counter | Cumulative aborted or rolled back transactions. | Low ratio compared to commits ($< 1\%$). |
| **`cnpg_pg_stat_database_blks_hit`** | Counter | Disk blocks read directly from PostgreSQL RAM buffer cache. | High value. |
| **`cnpg_pg_stat_database_blks_read`** | Counter | Disk blocks that had to be read from physical EBS storage. | Low value. Cache hit ratio = $\frac{\text{hit}}{\text{hit} + \text{read}} > 99\%$. |
| **`cnpg_pg_stat_activity_connections`** | Gauge | Current active backend client connections to PostgreSQL. | Stable within pool limits ($< 100$). |

## 6. Viewing in the Web Browser

When you are ready to explore metrics visually via interactive charts, targets, and query consoles:

### Option 1: 1-Command Script from Your Laptop
From your local orchestrator terminal:
```bash
cd ~/Desktop/Kamal/01-Sandbox/prod-docker-multi-script
./port-forward-prometheus.sh
```
Open your browser to: **[http://localhost:9090](http://localhost:9090)**

### Option 2: From AWS SSM Session on the EC2 Host
1. On the EC2 instance terminal:
   ```bash
   KUBECONFIG=/etc/kubernetes/admin.conf kubectl -n opensandbox-system port-forward --address 0.0.0.0 svc/prometheus-service 9090:9090
   ```
2. On your local laptop:
   ```bash
   aws ssm start-session \
     --target <PRIMARYHUB_INSTANCE_ID> \
     --region us-east-1 \
     --document-name AWS-StartPortForwardingSession \
     --parameters '{"portNumber":["9090"],"localPortNumber":["9090"]}'
   ```
3. Open **[http://localhost:9090](http://localhost:9090)** in your browser.

### Key Browser URLs:
* **`/targets`** — Live status of all scrapers (PostgreSQL, OCM, FastAPI, Envoy, CoreDNS).
* **`/rules`** — Real-time evaluation table of SLO/SLI recording rules.
* **`/graph`** — Interactive PromQL query builder and graphing tool.

---

## 7. Safe Helm Chart Upgrade Procedure

When modifying Helm values or adding recording rules, use the dedicated safe upgrade script to prevent resetting existing clusters or state:

```bash
cd ~/Desktop/Kamal/01-Sandbox/prod-docker-multi-script

# Upgrade PrimaryHub:
./upgrade-helm.sh primaryhub

# Or upgrade both PrimaryHub and SecondaryHub:
./upgrade-helm.sh all
```

### Safety Guarantees:
* Does **not** touch or re-provision EC2 instances, launch templates, or KinD clusters.
* Does **not** delete PVCs, database volumes, or persistent state (`--skip-crds` enabled).
* Does **not** touch WireGuard VPN tunnels or OCM registration tokens.

---

## 8. Multi-Cluster Prometheus Architecture (All Clusters)

### Overview

Prometheus is now deployed on **all clusters** — both hubs and all spoke clusters. Each cluster's Prometheus carries an `external_label` that identifies which cluster the metric came from. A central **Grafana** instance on `primaryhub` pulls from all Prometheus scrapers over the existing WireGuard overlay (`10.99.0.0/24`).

```
                        ┌───────────────────────────────────────────────────┐
                        │         Grafana – kind-primaryhub                 │
                        │         (monitoring namespace, port 3000)          │
                        └───┬───────────┬───────────┬──────────┬────────────┘
                            │ (local)   │ WG mesh   │ WG mesh  │ WG mesh
                            ▼           ▼           ▼          ▼
                  ┌──────────────┐ ┌──────────┐ ┌────────┐ ┌────────┐ ┌────────┐
                  │ Prometheus   │ │Prometheus│ │Prom.   │ │Prom.   │ │Prom.   │
                  │ primaryhub   │ │secondary │ │spoke1  │ │spoke2  │ │spoke3  │
                  │ :9090 (CIP)  │ │10.99.0.2 │ │10.99.0.3│ │10.99.0.4│ │10.99.0.5│
                  │              │ │:30900    │ │:30900  │ │:30900  │ │:30900  │
                  │cluster=      │ │cluster=  │ │cluster=│ │cluster=│ │cluster=│
                  │"primaryhub"  │ │"2ndaryhub│ │"spoke1"│ │"spoke2"│ │"spoke3"│
                  └──────────────┘ └──────────┘ └────────┘ └────────┘ └────────┘
```

### What Each Cluster Scrapes

| Cluster | CNPG metrics | OCM Hub controllers | OCM Klusterlet agent | App services |
| :--- | :---: | :---: | :---: | :---: |
| `primaryhub` | ✅ | ✅ | ✅ | ✅ |
| `secondaryhub` | ✅ | ✅ | ✅ | ✅ |
| `spoke1` | ❌ | ❌ | ✅ | ✅ |
| `spoke2` | ❌ | ❌ | ✅ | ✅ |
| `spoke3` | ❌ | ❌ | ✅ | ✅ |

### Cluster Identity Labels

Every metric from every cluster carries:
```
cluster="primaryhub"    cluster_role="hub"
cluster="secondaryhub"  cluster_role="hub"
cluster="spoke1"        cluster_role="spoke"
cluster="spoke2"        cluster_role="spoke"
cluster="spoke3"        cluster_role="spoke"
```
This means in Grafana you can write a single PromQL query and filter by `{cluster="spoke1"}` to see only that cluster's metrics.

### Deploying Monitoring to All Clusters

```bash
cd ~/Desktop/Kamal/01-Sandbox

# Deploy Prometheus to ALL clusters + Grafana to primaryhub:
./manifests/monitoring/apply-monitoring.sh all

# Or deploy selectively:
./manifests/monitoring/apply-monitoring.sh primaryhub
./manifests/monitoring/apply-monitoring.sh secondaryhub
./manifests/monitoring/apply-monitoring.sh spokes
./manifests/monitoring/apply-monitoring.sh spoke3   # single spoke
./manifests/monitoring/apply-monitoring.sh grafana  # Grafana only
```

---

## 9. Centralized Grafana – Access & Dashboards

### Accessing Grafana in Your Local Browser

**Option 1 – Quickest (port-forward via SSM):**
```bash
cd ~/Desktop/Kamal/01-Sandbox/prod-docker-multi-script
./port-forward-grafana.sh
```
Open: **http://localhost:3000** — Login: `admin` / `sandbox-admin`

**Option 2 – Manual:**
```bash
aws ssm start-session \
  --target <PRIMARYHUB_INSTANCE_ID> \
  --region us-east-1 \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["3000"],"localPortNumber":["3000"]}'
```

### Pre-Provisioned Dashboard: 01-Sandbox Multi-Cluster Overview

The Grafana deployment automatically provisions a dashboard with:

| Panel | Data source(s) |
| :--- | :--- |
| Prometheus Scraper Health (all clusters) | All datasources via `${cluster}` variable |
| Pod Restart Count per cluster | All 5 cluster datasources |
| PostgreSQL Replication Lag (primaryhub→secondaryhub) | primaryhub |
| OCM Webhook Admission Success Rate | primaryhub + secondaryhub |
| OCM Klusterlet Health – all spokes | spoke1, spoke2, spoke3 |
| Controller Workqueue Depth | primaryhub + secondaryhub |
| PostgreSQL Active Connections | primaryhub + secondaryhub |

### Viewing Individual Cluster Prometheus

```bash
# View any specific cluster's raw Prometheus:
./port-forward-prometheus.sh primaryhub
./port-forward-prometheus.sh secondaryhub
./port-forward-prometheus.sh spoke1
./port-forward-prometheus.sh spoke2
./port-forward-prometheus.sh spoke3
```

---

## 10. Accessing Live Metrics from a Private Subnet

All EC2 instances are in private subnets with no public IPs. The following options are available to view live dashboards from your local browser:

### Option A: AWS SSM Port Forwarding (Current Recommended Approach)

This is what `port-forward-grafana.sh` does. SSM creates an encrypted tunnel to the private EC2 instance, and `kubectl port-forward` tunnels Grafana's port to your laptop. No firewall changes needed — uses the existing SSM endpoint via AWS PrivateLink.

```
Laptop → AWS SSM PrivateLink → EC2 (primaryhub) → kubectl port-forward → Grafana:3000
```

**Pros:** Already working, no extra infra, no public exposure.
**Cons:** Requires `aws-cli` + `session-manager-plugin` on your laptop; tunnel dies if SSM session times out.

### Option B: Cloudflare Tunnel (Zero-Trust, Persistent URL)

Cloudflare Tunnel (`cloudflared`) creates a permanent, TLS-encrypted tunnel from your private EC2 to Cloudflare's edge — giving you a stable `https://grafana.your-domain.workers.dev` URL accessible from any browser, anywhere, with no inbound firewall rules.

**Architecture:**
```
Your Browser → https://grafana.yourdomain.com (Cloudflare Edge)
                     ↕ (encrypted Cloudflare tunnel)
               EC2 primaryhub (private subnet)
                     ↓
               kubectl port-forward → Grafana:3000
```

**Setup on primaryhub EC2 instance:**
```bash
# 1. Install cloudflared on the EC2 instance
curl -L --output cloudflared.rpm \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-x86_64.rpm
sudo rpm -ivh cloudflared.rpm

# 2. Authenticate with your Cloudflare account
cloudflared tunnel login

# 3. Create a named tunnel
cloudflared tunnel create grafana-sandbox

# 4. Create tunnel config on the EC2:
cat > ~/.cloudflared/config.yml << 'CFEOF'
tunnel: grafana-sandbox
credentials-file: /home/ec2-user/.cloudflared/<TUNNEL-ID>.json
ingress:
  - hostname: grafana.yourdomain.com
    service: http://localhost:3000
  - service: http_status:404
CFEOF

# 5. First: start Grafana port-forward in background on the EC2 host:
KUBECONFIG=/etc/kubernetes/admin.conf kubectl -n monitoring \
  port-forward --address 127.0.0.1 svc/grafana 3000:3000 &

# 6. Start the tunnel (run as systemd service for persistence):
cloudflared tunnel run grafana-sandbox
```

**Create systemd service for persistence:**
```bash
sudo cloudflared service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
```

**Pros:** Persistent public HTTPS URL; works from any device; no VPN required; Cloudflare Access can add authentication (SSO, email OTP) on top.
**Cons:** Requires a Cloudflare account and a domain managed on Cloudflare; adds a third-party in the data path (metrics are not sensitive, but worth noting).

### Option C: AWS SSM + Permanent Local Port Mapping (tmux)

Run the SSM tunnel in a persistent `tmux` session on your laptop so it survives terminal closure:
```bash
tmux new-session -d -s grafana-tunnel \
  'aws ssm start-session --target <PRIMARYHUB_ID> --region us-east-1 \
   --document-name AWS-StartPortForwardingSession \
   --parameters "{\"portNumber\":[\"3000\"],\"localPortNumber\":[\"3000\"]}"'
```

### Recommendation

| Scenario | Best Option |
| :--- | :--- |
| Quick ad-hoc inspection from your laptop | **Option A** (`port-forward-grafana.sh`) |
| Permanent dashboard URL for the whole team | **Option B** (Cloudflare Tunnel) |
| Team behind VPN / AWS internal access | **Option A** with tmux persistence |
