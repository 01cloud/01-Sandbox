# Prometheus Long-Term Storage: Cortex vs. S3-Backed Storage Architecture Analysis

**Author:** Kamal Tamang
**Reviewers:** Sarose Joshi, Umesh Khatiwada
**Target Environment:** 01-Sandbox Multi-Cluster Infrastructure (`PrimaryHub`, `SecondaryHub`, `Spoke1`, `Spoke2`) & 01 Cloud
**Status:** Architectural Proposal & Decision Record
**Date:** October 2026

---

## 1. Executive Summary

In the current 01-Sandbox multi-cluster architecture, metrics collected from all regional clusters (`us-east-1`, `us-west-2`, `eu-central-1`, `ap-south-1`) are pushed via WireGuard mesh to a centralized Prometheus instance running on **PrimaryHub**. This instance persists time-series data using a Kubernetes PersistentVolumeClaim backed by **AWS EBS (`gp3`)** with a retention policy of **7 days** and a volume size of **25 GiB**.

Sarose Joshi identified that relying on EBS for Prometheus storage presents fundamental scaling and cost challenges:
1. **Financial Inefficiency**: EBS gp3 costs **$0.08–$0.10/GB-month** regardless of whether historical data is actively accessed, compared to **$0.023/GB-month** for AWS S3 Standard and **$0.0125/GB-month** for S3 Standard-Infrequent Access (IA).
2. **Operational Rigidity**: EBS volumes are bound to a single AWS Availability Zone (AZ), restrict horizontal failover, require manual filesystem expansion when metrics surge, and risk unrecoverable corruption during abrupt node termination.
3. **Retention Constraints**: Storing 30 to 365 days of fine-grained multi-cluster metrics on block storage would significantly inflate infrastructure costs while requiring hundreds of gigabytes of provisioned EBS disk space per hub.

To establish long-term, scalable observability, two primary architectures were evaluated:
- **Option 1: Cortex** (currently deployed and operated in **01 Cloud**).
- **Option 2: Prometheus S3 / Native Object Storage (Thanos Sidecar / S3 Shipper Integration)**.

### Architectural Recommendation Summary

| Evaluation Criteria | Option 1: Cortex (01 Cloud Stack) | Option 2: Prometheus S3 (Thanos / S3 Shipper) |
| :--- | :--- | :--- |
| **Primary Fit** | High-throughput, multi-tenant enterprise monitoring where 01 Cloud infrastructure is already established. | Lightweight, drop-in extension to existing 01-Sandbox Prometheus Helm deployment with minimal compute overhead. |
| **Compute Overhead** | **High** (Requires Ingesters, Distributors, Compactors, Store-Gateways, Memcached, Consul/Memberlist ring). | **Very Low** (Single lightweight sidecar per Prometheus container; no distributed ring state). |
| **Storage Cost (1 TB / mo)** | ~$23 (S3 Standard) + minor API fees | ~$23 (S3 Standard) + minor API fees |
| **Compute Cost (Monthly)** | ~$120 – $250 (Additional EC2 RAM/vCPU for microservices) | ~$10 – $25 (Minimal sidecar overhead) |
| **Operational Complexity** | High (Multi-component lifecycle, ring rebalancing, stateful compaction). | Low (Prometheus writes local TSDB 2h blocks; sidecar uploads to S3). |
| **01-Sandbox Recommendation** | **Phase 2 Target**: Integrate with 01 Cloud's existing Cortex cluster via `remote_write` rather than running a standalone Cortex cluster inside the Sandbox KinD nodes. | **Phase 1 Immediate Winner**: Deploy Prometheus S3 block shipping (Thanos Sidecar) to replace 25Gi EBS with infinite S3 retention. |

---

## 2. Current State: EBS Baseline Analysis

### 2.1 Configuration in 01-Sandbox
In [`codeInspector/charts/prometheus/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/values.yaml), Prometheus is deployed as a single replica:
```yaml
retention: "7d"
persistence:
  enabled: true
  pvcName: "prometheus-pvc"
  storageClass: "standard" # EBS gp3
  size: "25Gi"
resources:
  requests:
    cpu: "100m"
    memory: "256Mi"
  limits:
    cpu: "500m"
    memory: "512Mi"
```

### 2.2 Pain Points & Bottlenecks
1. **Single-AZ Lock-in**:
   EBS volumes (`gp3`) are strictly tied to an AWS Availability Zone (e.g., `us-east-1a`). If the EC2 host fails or is rescheduled to another AZ during cluster maintenance, the PVC cannot attach, rendering monitoring unavailable.
2. **Fixed Cap vs. Ingestion Spikes**:
   With 4 clusters (PrimaryHub, SecondaryHub, Spoke 1, Spoke 2) pushing metrics simultaneously (including gVisor runtime metrics, Kata microVM telemetry, cAdvisor, and API server instrumentation), disk usage scales linearly with active container churn. When disk usage hits 100%, Prometheus halts writes and crashes into CrashLoopBackOff.
3. **Compaction IOPS & CPU Spikes**:
   Prometheus TSDB merges 2-hour blocks into larger blocks (e.g., 6h, 12h, 24h) on the local EBS disk. On smaller EC2 instances, this creates heavy I/O wait times and disk head contention, triggering scrape failures and missing alerts.

---

## 3. Option 1: Cortex (01 Cloud Architecture)

### 3.1 Overview & How It Works
Cortex is a CNCF incubating system designed to provide horizontally scalable, highly available, multi-tenant long-term storage for Prometheus. 01 Cloud already operates Cortex in production.

In Cortex, Prometheus servers act as stateless scrapers that push time-series data to Cortex via the Prometheus `remote_write` protocol. Cortex then manages ingestion, caching, querying, and persistent storage in object storage (AWS S3).

```
┌────────────────────────────────────────────────────────────────────────┐
│                          01-Sandbox Clusters                           │
│  [PrimaryHub]      [SecondaryHub]        [Spoke 1]        [Spoke 2]    │
│  Prometheus         Prometheus          Prometheus       Prometheus    │
└───────┬───────────────────┬──────────────────┬───────────────┬─────────┘
        │ remote_write      │ remote_write     │ remote_write  │ remote_write
        ▼                   ▼                  ▼               ▼
┌────────────────────────────────────────────────────────────────────────┐
│                              CORTEX                                    │
│                                                                        │
│        ┌──────────────┐         Distributed Hash Ring                  │
│        │ Distributor  │───────► (Consistent Hashing via Memberlist)    │
│        └──────┬───────┘                                                │
│               │                                                        │
│               ▼                                                        │
│        ┌──────────────┐         Replicated In-Memory Chunks            │
│        │   Ingester   │◄──────► (RF=3 with WAL)                        │
│        └──────┬───────┘                                                │
│               │ Flush 2-hour TSDB blocks                               │
│               ▼                                                        │
│   ┌───────────────────────┐                                            │
│   │     Compactor &       │◄───────────────────────┐                   │
│   │    Store-Gateway      │                        │                   │
│   └───────────┬───────────┘                        │                   │
└───────────────┼────────────────────────────────────┼───────────────────┘
                ▼                                    │
    ┌───────────────────────┐              ┌──────────────────┐
    │    AWS S3 Bucket      │              │ Memcached /      │
    │  (cortex-tsdb-blocks) │              │ Redis Cache      │
    └───────────────────────┘              └──────────────────┘
```

### 3.2 Key Components
- **Distributor**: Stateless service that receives `remote_write` samples, validates them, splits them by tenant ID (`X-Scope-OrgID`), and routes them to ingesters using a consistent hash ring.
- **Ingester**: Stateful service that buffers incoming samples in memory for up to 2 hours, forms TSDB blocks, and flushes completed blocks to AWS S3.
- **Store-Gateway**: Queries historical TSDB blocks stored in AWS S3 and caches block indexes in Memcached/Redis for low-latency queries.
- **Compactor**: Merges smaller 2-hour TSDB blocks in S3 into larger 12-hour or 24-hour blocks, deduplicates series across replicas, and cleans up expired/deleted blocks.
- **Ruler**: Evaluates recording rules and alert expressions independently of query load.
- **Query Frontend / Querier**: Handles PromQL queries, accelerates execution by splitting queries along time boundaries, and leverages Memcached to avoid redundant queries.

### 3.3 Pros of Cortex
1. **Proven Production Baseline in 01 Cloud**: The team already has operational runbooks, dashboards, and expertise for Cortex.
2. **True Native Multi-Tenancy**: Support for strict isolation between tenants via `X-Scope-OrgID` headers. Different sandbox users, customers, or internal clusters can query their own metrics without leaking data.
3. **High Availability Ingestion (Zero Data Loss)**: Cortex uses an Ingestion Replication Factor (typically RF=3). If an ingester crashes or an EC2 instance reboots, metric data is safe in sibling ingesters.
4. **Sub-Second Global Queries with Caching**: Query Frontend and Memcached cache raw series chunks and query results, providing sub-second responses even for 90-day time ranges.
5. **Deduplication Across Multi-Cluster Writes**: If PrimaryHub and SecondaryHub remote-write the same metric streams, Cortex automatically deduplicates them.

### 3.4 Cons & Limitations of Cortex
1. **High Compute & Memory Footprint**:
   - Ingesters must keep active series in RAM. In a 500k active series environment, Cortex requires **at least 8–16 GiB of RAM** across ingesters.
   - Deploying Cortex *inside* the 01-Sandbox KinD cluster would quickly overwhelm the current node resources (which allocate 512Mi–1536Mi per container).
2. **Operational Complexity**:
   - Requires managing a ring topology (memberlist/consul), chunk/block compactions, and caching layers (Memcached or Redis).
   - High write amplification if ingester memory limits are configured too low.
3. **Network Ingestion Cost**:
   - All metric samples must be constantly streamed across WAN/WireGuard to the Cortex ingestion endpoint.

---

## 4. Option 2: Prometheus Native / S3-Backed Long-Term Storage

### 4.1 Understanding Prometheus "Native" S3 Capabilities
A common misconception is that standard Prometheus can natively point its primary TSDB storage directly to an S3 bucket URI (e.g. `storage.tsdb.path: s3://my-bucket`).

In reality, the Prometheus TSDB engine requires **POSIX `mmap` and atomic append operations** on local disk to write current head chunks. Object storage (S3) is an eventually consistent key-value store with no append-in-place primitives.

However, the Prometheus ecosystem natively supports **two primary S3 long-term storage approaches**:

#### Approach A: Prometheus + Thanos Sidecar (Standard Kubernetes Pattern)
Prometheus runs locally with minimal storage (2–6 hours of local retention on small EBS). A lightweight **Thanos Sidecar** runs in the same pod:
1. Observes local TSDB block generation every 2 hours.
2. Uploads completed, immutable 2-hour TSDB blocks directly to AWS S3.
3. Leaves current (uncompacted) metrics in Prometheus for immediate querying.
4. A centralized **Thanos Store Gateway** queries S3 for long-term historical data, while **Thanos Querier** merges live data and historical S3 data into a single PromQL endpoint.

```
┌──────────────────────────────────────────────────────────────────┐
│                   Prometheus Pod (PrimaryHub)                    │
│                                                                  │
│  ┌──────────────────────┐          ┌──────────────────────────┐  │
│  │ Prometheus (TSDB)    │─────────►│ Thanos Sidecar           │  │
│  │ Local EBS: 5-10 GiB  │ (read    │ - Ships 2h blocks to S3  │  │
│  │ Retention: 6 Hours   │  blocks) │ - Acts as gRPC StoreAPI  │  │
│  └──────────────────────┘          └────────────┬─────────────┘  │
└─────────────────────────────────────────────────┼────────────────┘
                                                  │ Upload blocks
                                                  ▼
                                      ┌───────────────────────┐
                                      │    AWS S3 Bucket      │
                                      │ (prometheus-longterm) │
                                      └───────────┬───────────┘
                                                  │
                                                  │ Fetch historical
                                                  ▼
                                      ┌───────────────────────┐
                                      │ Thanos Store Gateway  │
                                      │ (Cached via Memcached)│
                                      └───────────┬───────────┘
                                                  │
                                                  ▼
                                      ┌───────────────────────┐
                                      │    Thanos Querier     │◄─── Grafana
                                      │ (Global PromQL engine)│
                                      └───────────────────────┘
```

#### Approach B: Direct Remote-Write to S3 / AWS Managed Prometheus (AMP)
Prometheus runs as a stateless scraper (or Prometheus Agent mode), using `remote_write` to stream metrics to a managed S3-backed service such as AWS Managed Prometheus (AMP) or an S3 remote-write bridge.

### 4.2 Pros of Prometheus + S3 (Thanos Sidecar Pattern)
1. **Extremely Low Resource Overhead**:
   - The sidecar container runs alongside Prometheus and consumes **< 50m CPU and < 64Mi RAM**.
   - No distributed ring state, no ingester state machines to manage.
2. **Minimal Local Disk Footprint**:
   - Local EBS disk can be drastically downsized from **25 GiB down to 5–10 GiB** (holding only 2–6 hours of WAL and recent blocks).
   - Once a 2-hour block is confirmed in S3, local storage is truncated.
3. **Native TSDB Block Compatibility**:
   - The blocks written to S3 are standard Prometheus TSDB format. Any standard Prometheus tooling, offline analytical tools, or DuckDB/ClickHouse TSDB importers can process them directly from S3.
4. **Seamless Drop-In to Existing Helm Chart**:
   - The official Prometheus Helm chart and our customized [`codeInspector/charts/prometheus`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus) can enable this with a single sidecar container and an IAM role / S3 bucket secret.

### 4.3 Cons & Limitations of Prometheus + S3
1. **Query Latency on Historical Data Without Store-Gateway**:
   - Cold queries on metrics older than 24 hours require downloading index headers from S3. Without a local SSD cache or Memcached on the Store Gateway, multi-month queries can take 3–5 seconds.
2. **2-Hour Latency for S3 Persistence**:
   - Data reaches S3 only after Prometheus closes a 2-hour TSDB block. If the local EBS disk is destroyed before the 2-hour window closes, uncompacted in-flight samples from the WAL must be replayed from remote writers.
3. **Limited Native Multi-Tenancy**:
   - Unlike Cortex (which enforces tenant ID headers per sample), Prometheus + Thanos Sidecar stores all metrics for a cluster in the same S3 prefix unless separate bucket prefixes are configured per cluster.

---

## 5. Detailed Head-to-Head Comparison

| Metric / Dimension | Current Setup (EBS gp3) | Option 1: Cortex (01 Cloud Model) | Option 2: Prometheus + S3 (Thanos Sidecar) |
| :--- | :--- | :--- | :--- |
| **Storage Technology** | AWS EBS Block Volume (`gp3`) | AWS S3 Object Storage (Blocks/Chunks) | AWS S3 Object Storage (TSDB Blocks) |
| **Data Retention Capacity** | Limited (7–14 days before disk fill) | **Virtually Infinite** (Years via S3 lifecycle) | **Virtually Infinite** (Years via S3 lifecycle) |
| **Multi-Tenancy** | None | **Strict Native Isolation** (`X-Scope-OrgID`) | Soft Isolation (Label-based / Prefix-based) |
| **Fault Tolerance & HA** | None (Single EBS attached to single pod) | **High (Replication Factor = 3)** | High (S3 99.999999999% durability + local failover) |
| **Ingestion Protocol** | Local Scrape + Remote Write Receiver | Push via `remote_write` HTTP | Local Scrape + Remote Write to local TSDB |
| **Compute Footprint** | Low (Prometheus pod only: 500m / 512Mi) | **High** (Distributors, Ingesters, Gateway: ~4-8 vCPU, 8-16 GB RAM) | **Very Low** (Prometheus + Sidecar: 600m / 600Mi total) |
| **Local Disk Requirement** | High (25–100+ GiB EBS) | Stateless / Ephemeral (Temporary WAL only) | Low (5–10 GiB EBS for active 2h buffer) |
| **Query Latency (Recent < 2h)** | Fast (Local disk read: ~50ms) | Fast (In-memory Ingester query: ~40ms) | Fast (Local Prometheus TSDB: ~40ms) |
| **Query Latency (Historical > 30d)** | N/A (Evicted after 7 days) | Very Fast (~100-300ms via Memcached/Frontend) | Moderate (~500ms - 2s depending on cache) |
| **Operational Complexity** | Very Low (Vanilla Prometheus) | **High** (Ring rebalancing, compaction, caches) | **Low to Moderate** (Simple sidecar + Store-Gateway) |
| **Existing Team Expertise** | High | **High** (Already in production at 01 Cloud) | Moderate (Standard Kubernetes ecosystem) |

---

## 6. Financial & Cost Model Analysis

### 6.1 Storage Pricing Parameters (AWS `us-east-1`)
- **EBS `gp3`**: `$0.08 / GB-month` ($0.096 with IOPS/throughput provisioning).
- **AWS S3 Standard**: `$0.023 / GB-month` (first 50 TB).
- **AWS S3 Standard-IA (Infrequent Access)**: `$0.0125 / GB-month`.
- **AWS S3 Glacier Instant Retrieval**: `$0.004 / GB-month`.
- **S3 API Calls**: `$0.005 per 1,000 PUT requests`, `$0.0004 per 1,000 GET requests`.

### 6.2 Total Cost of Storage Over Time (Storage-Only Comparison)

Assuming an active metric churn generating **500 GiB of compressed TSDB blocks per month**:

| Retention Duration | Active Data Size | EBS `gp3` Cost / Month | S3 Standard Cost / Month | S3 Infrequent Access Cost / Month | S3 Glacier Instant Cost / Month |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **7 Days (Current)** | ~120 GiB | **$9.60** | $2.76 | $1.50 | $0.48 |
| **30 Days (1 Month)** | ~500 GiB | **$40.00** | $11.50 | $6.25 | $2.00 |
| **90 Days (3 Months)** | ~1.5 TiB | **$120.00** | $34.50 | $18.75 | $6.00 |
| **180 Days (6 Months)**| ~3.0 TiB | **$240.00** | $69.00 | $37.50 | $12.00 |
| **365 Days (1 Year)**  | ~6.0 TiB | **$480.00** | $138.00 | $75.00 | $24.00 |

> [!TIP]
> **Storage Cost Reduction**: Switching from EBS to S3 Standard reduces raw storage expenditure by **71.2%**. Utilizing S3 Lifecycle rules to transition blocks older than 30 days to S3 Glacier Instant Retrieval achieves a **95% cost reduction** compared to EBS.

### 6.3 Total Cost of Ownership (TCO) Including Compute Infrastructure

Storage costs are only half of the equation; the compute infrastructure required to run the solution must be factored in:

#### Scenario: Running Inside the 01-Sandbox PrimaryHub Node
- **Cortex as a standalone cluster inside 01-Sandbox**:
  - Requires deploying at least 2 Ingesters, 1 Distributor, 1 Querier, 1 Store-Gateway, 1 Compactor, and 1 Memcached instance.
  - Requires an additional **8–12 GB of dedicated node memory**.
  - On AWS EC2 (`t3.2xlarge` or `m5.xlarge`), this adds **~$100–$140/month in EC2 compute overhead**.
  - **Verdict**: Unsuitable to run as an independent microservice stack inside 01-Sandbox KinD instances.

#### Scenario: Prometheus + S3 (Thanos Sidecar)
- Adds a single container to the existing Prometheus pod.
- Resource increase: `+50m CPU`, `+64Mi RAM`.
- Local EBS downsized from 25 GiB to 10 GiB ($0.80/month).
- S3 Bucket Storage: ~$11.50/month (30-day retention).
- Total compute overhead: **Negligible (< $3/month)**.

#### Scenario: Remote-Writing to 01 Cloud's Existing Cortex Cluster
- Zero additional microservices in 01-Sandbox.
- 01-Sandbox Prometheus acts as a remote-write agent forwarding metrics to `https://cortex.01cloud.com/api/v1/push`.
- 01-Sandbox local disk drops to minimum buffer (5 GiB).
- Utilizes 01 Cloud's already amortized Cortex compute and S3 storage.

---

## 7. Technical Risk & Resource Utilization Analysis

### 7.1 Memory & CPU Utilization Profiles

```
MEMORY PROFILES UNDER HIGH METRIC INGESTION:

1. Current Prometheus (EBS)
   ├── Buffer Cache (Pagecache): ~300 MiB
   └── TSDB Head Chunks:        ~212 MiB
   Total RAM: ~512 MiB (Hits cgroup limits during 2h compactions)

2. Cortex Microservice Stack (Inside Cluster)
   ├── Ingesters (RF=3):        ~6,144 MiB (holds active chunks for 2h)
   ├── Distributors:            ~512 MiB
   ├── Store-Gateways:          ~1,536 MiB (index headers)
   ├── Memcached (Cache):       ~2,048 MiB
   └── Compactor:               ~1,024 MiB
   Total RAM: ~11,264 MiB (Overwhelms Sandbox KinD nodes)

3. Prometheus + Thanos Sidecar (S3 Shipper)
   ├── Prometheus Head Chunks:  ~256 MiB (retention dialed down to 4h)
   └── Thanos Sidecar:          ~64 MiB (uploads static blocks via S3 SDK)
   Total RAM: ~320 MiB (Safely runs within 512Mi cgroup limit!)
```

### 7.2 Failure Modes & Disaster Recovery

| Failure Scenario | Current Setup (EBS) | Cortex (01 Cloud Model) | Prometheus + S3 Sidecar |
| :--- | :--- | :--- | :--- |
| **EC2 Instance Hard Crash** | EBS detached; pod stuck until volume unmounts. Data safe if volume not corrupted. | Ingesters replicate (RF=3); other ingesters continue ingestion without interruption. | Local uncompacted WAL (< 2h) replayed on reboot; all historical > 2h data safe in S3. |
| **Availability Zone Outage** | **Total Outage**: EBS cannot move across AZs. | Highly resilient if Cortex ingesters span multiple AZs. | Prometheus recreates in new AZ, attaches new small EBS, and queries historical data from S3. |
| **Disk Exhaustion (Metric Spike)** | **Pod CrashLoop**: Requires manual volume expansion via AWS console. | **Immune**: Ingesters stream blocks to S3; local disk is not a bottleneck. | **Protected**: Local retention fixed at 4h; old blocks pruned immediately after S3 upload. |

---

## 8. Strategic Recommendation & Implementation Roadmap

Based on technical feasibility, cost efficiency, resource utilization in 01-Sandbox, and existing infrastructure in 01 Cloud, we recommend a **Two-Phase Observability Evolution Strategy**:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        RECOMMENDED ROADMAP                             │
├────────────────────────────────────────────────────────────────────────┤
│ PHASE 1: Immediate S3 Integration (Prometheus + S3 Block Shipping)      │
│  - Keep existing Prometheus in PrimaryHub & SecondaryHub               │
│  - Downsize local EBS from 25 GiB -> 10 GiB                            │
│  - Attach Thanos Sidecar / S3 Shipper to push 2h blocks to S3 bucket   │
│  - Configure S3 Lifecycle: S3 Standard (30d) -> Glacier Instant (1y)   │
│  - Result: Infinite retention, 75%+ cost savings, zero compute penalty │
├────────────────────────────────────────────────────────────────────────┤
│ PHASE 2: Dual Ingestion to 01 Cloud Cortex Enterprise Platform         │
│  - Configure Prometheus remote_write to push to 01 Cloud Cortex        │
│  - Leverage 01 Cloud's existing Cortex cluster, multi-tenancy & Grafana│
│  - Do NOT deploy Cortex microservices inside 01-Sandbox KinD nodes     │
│  - Result: Centralized enterprise dashboards across 01 Cloud & Sandbox │
└────────────────────────────────────────────────────────────────────────┘
```

### 8.1 Phase 1 Implementation Plan (Prometheus S3 Integration)

#### Step 1: Create S3 Bucket and IAM Policy
Create S3 bucket `01-sandbox-prometheus-metrics` with lifecycle transitions:
- `0–30 Days`: S3 Standard
- `31–180 Days`: S3 Standard-Infrequent Access (IA)
- `181–365 Days`: S3 Glacier Instant Retrieval
- `> 365 Days`: Expiration

#### Step 2: Update Prometheus Helm Chart Configuration
In [`codeInspector/charts/prometheus/templates/deployment.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/deployment.yaml), add the sidecar container and update storage arguments:

```yaml
# Add to prometheus container args:
args:
  - --storage.tsdb.path=/prometheus
  - --storage.tsdb.retention.time=6h
  - --storage.tsdb.min-block-duration=2h
  - --storage.tsdb.max-block-duration=2h
  - --web.enable-lifecycle
  - --web.enable-admin-api

# Add sidecar container in same pod:
- name: thanos-sidecar
  image: quay.io/thanos/thanos:v0.36.1
  args:
    - sidecar
    - --tsdb.path=/prometheus
    - --prometheus.url=http://127.0.0.1:9090
    - --objstore.config-file=/etc/thanos/bucket.yaml
  volumeMounts:
    - name: storage
      mountPath: /prometheus
    - name: thanos-config
      mountPath: /etc/thanos
```

#### Step 3: Bucket Configuration Secret (`bucket.yaml`)
```yaml
type: S3
config:
  bucket: 01-sandbox-prometheus-metrics
  endpoint: s3.us-east-1.amazonaws.com
  region: us-east-1
  sse_config:
    type: "SSE-S3"
```

### 8.2 Phase 2 Implementation Plan (Federation to 01 Cloud Cortex)

Once S3 persistence is active on PrimaryHub, configure Prometheus to stream high-value telemetry directly into 01 Cloud's production Cortex instance by adding a `remote_write` block in [`codeInspector/charts/prometheus/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/values.yaml):

```yaml
remoteWrite:
  - url: "https://cortex.01cloud.com/api/v1/push"
    headers:
      X-Scope-OrgID: "01-sandbox-prod"
    basicAuth:
      username: "sandbox-hub"
      password: "${CORTEX_REMOTE_WRITE_TOKEN}"
    queueConfig:
      maxSamplesPerSend: 1000
      batchSendDeadline: 5s
      maxShards: 10
```

---

## 9. Conclusion

1. **EBS is unsustainable for long-term retention**: Persisting metrics beyond 7–14 days on EBS creates prohibitive AWS costs ($0.08–$0.10/GB-month) and introduces single-AZ availability risks.
2. **Do not run a standalone Cortex cluster inside 01-Sandbox**: Cortex is a world-class system, but running its complete microservice stack inside KinD sandbox nodes would consume excessive RAM and CPU that should be reserved for container security analysis.
3. **The Optimal Strategy**:
   - Adopt **Prometheus S3 Block Shipping (Thanos Sidecar)** inside 01-Sandbox for immediate 71%–95% storage cost savings, low memory footprint, and infinite durability.
   - Forward metrics to **01 Cloud's existing Cortex infrastructure** via `remote_write` for cross-platform dashboards and multi-tenant reporting.
