# Prometheus Long-Term Storage: Cortex vs. S3-Backed Storage Architecture Analysis

**Author:** Kamal Tamang
**Reviewers:** Sarose Joshi, Umesh Khatiwada
**Target Environment:** 01-Sandbox Multi-Cluster Infrastructure (`PrimaryHub`, `SecondaryHub`, `Spoke1`, `Spoke2`)
**Status:** Architectural Proposal & Decision Record
**Date:** October 2026

---

## 1. Executive Summary

In the current 01-Sandbox multi-cluster architecture, metrics collected from all regional clusters (`us-east-1`, `us-west-2`, `eu-central-1`, `ap-south-1`) are pushed via WireGuard mesh to a centralized Prometheus instance running on **PrimaryHub**. This instance persists time-series data using a Kubernetes PersistentVolumeClaim backed by **AWS EBS (`gp3`)** with a retention policy of **7 days** and a volume size of **25 GiB**.

Sarose Joshi and Umesh Khatiwada identified that relying on EBS for Prometheus storage presents fundamental scaling and cost challenges:
1. **Financial Inefficiency**: EBS gp3 costs **$0.08–$0.10/GB-month** regardless of whether historical data is actively accessed, compared to **$0.023/GB-month** for AWS S3 Standard and **$0.0125/GB-month** for S3 Standard-Infrequent Access (IA).
2. **Operational Rigidity**: EBS volumes are bound to a single AWS Availability Zone (AZ), restrict horizontal failover, require manual filesystem expansion when metrics surge, and risk unrecoverable corruption during abrupt node termination.
3. **Retention Constraints**: Storing 30 to 365 days of fine-grained multi-cluster metrics on block storage would significantly inflate infrastructure costs while requiring hundreds of gigabytes of provisioned EBS disk space per hub.

To establish long-term, scalable observability **entirely self-contained within 01-Sandbox** (with zero telemetry leaving the sandbox environment), two architectural approaches were evaluated:
- **Option 1: Provisioning Self-Hosted Cortex inside 01-Sandbox** (deploying a dedicated Cortex TSDB cluster or single-binary instance backed by an S3 bucket).
- **Option 2: Prometheus S3 Block Storage via Thanos Sidecar inside 01-Sandbox** (extending the existing Prometheus deployment to ship 2h TSDB blocks directly to S3).

### Architectural Recommendation Summary

| Evaluation Criteria | Option 1: Provision Cortex in 01-Sandbox (Monolithic) | Option 2: Prometheus S3 (Thanos Sidecar) |
| :--- | :--- | :--- |
| **Primary Fit** | Teams requiring strict multi-tenant header isolation and distributed query frontends. | Lightweight, drop-in extension to existing 01-Sandbox Prometheus Helm deployment with minimal compute overhead. |
| **Compute Overhead** | **High** (~1.5–2.0 vCPU, 2–4 GiB RAM minimum for monolithic mode; 8–16 GiB for microservices). | **Very Low** (Single lightweight sidecar per Prometheus container; < 50m CPU, < 64Mi RAM). |
| **Storage Cost (1 TB / mo)** | ~$23 (S3 Standard) + minor API fees | ~$23 (S3 Standard) + minor API fees |
| **Local EBS Buffer** | ~5 GiB (Temporary WAL buffer) | ~10 GiB (Downsized from 25 GiB, 6h retention) |
| **Operational Complexity** | Moderate to High (Memberlist ring, compactor state, WAL flushes). | Very Low (Prometheus writes local TSDB 2h blocks; sidecar uploads to S3). |
| **01-Sandbox Recommendation** | Feasible via Monolithic mode, but consumes substantial KinD node RAM. | **Winner**: Deploy Prometheus S3 block shipping (Thanos Sidecar) for immediate 71%–93% cost savings and minimal RAM footprint. |

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

## 3. Option 1: Provisioning Self-Hosted Cortex inside 01-Sandbox

### 3.1 Overview & Architecture in 01-Sandbox
Cortex is a CNCF incubating project that provides horizontally scalable, multi-tenant, long-term storage for Prometheus metrics backed by object storage (AWS S3).

In this option, Cortex is **provisioned directly inside 01-Sandbox** on the PrimaryHub KinD cluster. All metrics remain strictly within the 01-Sandbox perimeter. Prometheus acts as an ingestion agent pushing metrics to Cortex via `remote_write` to an internal Kubernetes service (`http://cortex.opensandbox-system.svc.cluster.local:9009/api/v1/push`). Cortex buffers metrics and persists TSDB blocks into a dedicated 01-Sandbox AWS S3 bucket.

```
┌────────────────────────────────────────────────────────────────────────┐
│                    01-Sandbox Internal Mesh Boundary                   │
│  [PrimaryHub]      [SecondaryHub]        [Spoke 1]        [Spoke 2]    │
│  Prometheus         Prometheus          Prometheus       Prometheus    │
└───────┬───────────────────┬──────────────────┬───────────────┬─────────┘
        │ remote_write      │ remote_write     │ remote_write  │ remote_write
        ▼                   ▼                  ▼               ▼
┌────────────────────────────────────────────────────────────────────────┐
│            PROVISIONED CORTEX (PrimaryHub KinD Cluster)                │
│                                                                        │
│        ┌──────────────┐         Distributed Hash Ring                  │
│        │ Distributor  │───────► (Consistent Hashing via Memberlist)    │
│        └──────┬───────┘                                                │
│               │                                                        │
│               ▼                                                        │
│        ┌──────────────┐         Replicated In-Memory Chunks            │
│        │   Ingester   │◄──────► (RF=1 or RF=3 with WAL)                │
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
    │  01-Sandbox AWS S3    │              │ Internal Cache   │
    │  (cortex-tsdb-blocks) │              │ (Memcached/Redis)│
    └───────────────────────┘              └──────────────────┘
```

### 3.2 Key Components
- **Distributor**: Receives internal `remote_write` samples, validates them, and routes them to ingesters using a memberlist consistent hash ring.
- **Ingester**: Stateful component buffering incoming samples in memory for up to 2 hours, cutting TSDB blocks, and flushing completed blocks to the 01-Sandbox S3 bucket.
- **Store-Gateway**: Queries historical TSDB blocks stored in the S3 bucket.
- **Compactor**: Merges smaller 2-hour TSDB blocks in S3 into larger 12-hour or 24-hour blocks and cleans up deleted series.
- **Querier / Query Frontend**: Handles PromQL queries across both in-flight memory (Ingesters) and historical data (Store-Gateway).

### 3.3 Pros of Provisioning Cortex
1. **Proven Engine Pattern**: Highly reliable TSDB block storage engine matching industry standard scalability.
2. **True Native Multi-Tenancy**: Support for strict isolation between tenants via `X-Scope-OrgID` headers if different sandbox test runs or users need isolated metric namespaces.
3. **High Availability Ingestion**: Supports replication (RF=3) across ingesters to prevent in-flight data loss during pod restarts.
4. **Fast Historical Queries**: Query Frontend and Memcached split and cache queries for low-latency dashboards.

### 3.4 Cons & Limitations of Provisioning Cortex in 01-Sandbox
1. **High Memory & CPU Footprint on Sandbox Host**:
   - Ingesters must keep active series in RAM. Even in Single-Binary (monolithic) mode, Cortex requires **at least 1.5–2.0 vCPU and 2–4 GiB RAM**.
   - Running full microservices mode requires **8–16 GiB RAM**.
   - On the EC2 host where KinD nodes already host CloudNativePG Postgres, Valkey, RabbitMQ, and resource-heavy container security scanners (Trivy, Semgrep, gVisor), Cortex adds significant resource contention.
2. **Operational Overhead**:
   - Memberlist ring state, compactor disk mounts, and write-ahead-logs introduce additional operational failure modes.

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

| Metric / Dimension | Current Setup (EBS gp3) | Option 1: Provisioned Cortex in 01-Sandbox | Option 2: Prometheus + S3 (Thanos Sidecar) |
| :--- | :--- | :--- | :--- |
| **Storage Technology** | AWS EBS Block Volume (`gp3`) | AWS S3 Object Storage (TSDB Blocks) | AWS S3 Object Storage (TSDB Blocks) |
| **Data Retention Capacity** | Limited (7–14 days before disk fill) | **Virtually Infinite** (Years via S3 lifecycle) | **Virtually Infinite** (Years via S3 lifecycle) |
| **Multi-Tenancy** | None | **Strict Native Isolation** (`X-Scope-OrgID`) | Soft Isolation (Label-based / Prefix-based) |
| **Fault Tolerance & HA** | None (Single EBS attached to single pod) | High (Replication Factor = 1 or 3 with WAL) | High (S3 99.999999999% durability + local failover) |
| **Ingestion Protocol** | Local Scrape + Remote Write Receiver | Push via `remote_write` to internal Cortex | Local Scrape + Remote Write to local TSDB |
| **Compute Footprint** | Low (Prometheus pod only: 500m / 512Mi) | **High** (Monolithic Cortex: 1.5–2.0 vCPU, 2–4 GB RAM) | **Very Low** (Prometheus + Sidecar: 600m / 600Mi total) |
| **Local Disk Requirement** | High (25–100+ GiB EBS) | Low / Ephemeral (5–10 GiB temporary WAL buffer) | Low (5–10 GiB EBS for active 2h buffer) |
| **Query Latency (Recent < 2h)** | Fast (Local disk read: ~50ms) | Fast (In-memory Ingester query: ~40ms) | Fast (Local Prometheus TSDB: ~40ms) |
| **Query Latency (Historical > 30d)** | N/A (Evicted after 7 days) | Very Fast (~100-300ms via internal cache) | Moderate (~500ms - 2s depending on cache) |
| **Operational Complexity** | Very Low (Vanilla Prometheus) | **Moderate to High** (Memberlist ring, compactor) | **Very Low** (Simple sidecar + Store-Gateway) |
| **Telemetry Sovereignty** | 100% Internal to 01-Sandbox | 100% Internal to 01-Sandbox | 100% Internal to 01-Sandbox |

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

Storage costs are only half of the equation; the compute infrastructure required to run the solution inside 01-Sandbox must be factored in:

#### Scenario A: Provisioning Cortex inside 01-Sandbox (Monolithic Mode)
- Deploying Cortex as a single-binary monolithic pod inside the PrimaryHub KinD cluster.
- Memory requirement: **2–4 GiB RAM dedicated**.
- CPU requirement: **1.5–2.0 vCPU dedicated**.
- Because the PrimaryHub EC2 instance hosts KinD along with CloudNativePG, Valkey, RabbitMQ, and analysis workers, this memory footprint requires upgrading the EC2 instance tier (e.g., from `t3.xlarge` to `t3.2xlarge`).
- **Additional Compute Cost**: **+$35 – $70 / month** in EC2 instance upsizing.

#### Scenario B: Prometheus + S3 (Thanos Sidecar) in 01-Sandbox (Recommended)
- Adds a single lightweight sidecar container to the existing Prometheus pod.
- Resource increase: `+50m CPU`, `+64Mi RAM`.
- Local EBS downsized from 25 GiB to 10 GiB ($0.80/month).
- S3 Bucket Storage: ~$11.50/month (30-day retention).
- Fits completely within existing EC2 instance headroom.
- **Additional Compute Cost**: **$0.00 / month**.

---

## 7. Technical Risk & Resource Utilization Analysis

### 7.1 Memory & CPU Utilization Profiles

```
MEMORY PROFILES UNDER HIGH METRIC INGESTION (01-SANDBOX HOST):

1. Current Prometheus (EBS)
   ├── Buffer Cache (Pagecache): ~300 MiB
   └── TSDB Head Chunks:        ~212 MiB
   Total RAM: ~512 MiB (Hits cgroup limits during 2h compactions)

2. Provisioned Cortex (Monolithic Mode in KinD)
   ├── In-Memory TSDB Ingestion: ~2,048 MiB (holds active chunks for 2h)
   ├── Compactor Buffer:        ~512 MiB
   └── Internal Memberlist/Ring:~256 MiB
   Total RAM: ~2,816 MiB (Heavy memory load on Sandbox node)

3. Prometheus + Thanos Sidecar (S3 Shipper)
   ├── Prometheus Head Chunks:  ~256 MiB (retention dialed down to 4h)
   └── Thanos Sidecar:          ~64 MiB (uploads static blocks via S3 SDK)
   Total RAM: ~320 MiB (Safely runs within existing limits!)
```

### 7.2 Failure Modes & Disaster Recovery

| Failure Scenario | Current Setup (EBS) | Provisioned Cortex in 01-Sandbox | Prometheus + S3 Sidecar |
| :--- | :--- | :--- | :--- |
| **EC2 Instance Hard Crash** | EBS detached; pod stuck until volume unmounts. Data safe if volume not corrupted. | Ingesters recover state from S3 TSDB blocks; local WAL replayed if mounted to PVC. | Local uncompacted WAL (< 2h) replayed on reboot; all historical > 2h data safe in S3. |
| **Availability Zone Outage** | **Total Outage**: EBS cannot move across AZs. | Cortex recovers in new AZ, points to same S3 bucket, and resumes querying. | Prometheus recreates in new AZ, attaches new small EBS, and queries historical data from S3. |
| **Disk Exhaustion (Metric Spike)** | **Pod CrashLoop**: Requires manual volume expansion via AWS console. | **Immune**: Ingesters stream blocks to S3; local disk is only a buffer. | **Protected**: Local retention fixed at 4h; old blocks pruned immediately after S3 upload. |

---

## 8. Strategic Recommendation & Architectural Verdict

Based on technical feasibility, cost efficiency, resource utilization in 01-Sandbox, and operational simplicity:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        ARCHITECTURAL VERDICT                           │
├────────────────────────────────────────────────────────────────────────┤
│ RECOMMENDED: Option 2 (Prometheus + Thanos S3 Sidecar in 01-Sandbox)   │
│  - Keep existing Prometheus in PrimaryHub & SecondaryHub               │
│  - Downsize local EBS from 25 GiB -> 10 GiB                            │
│  - Attach Thanos Sidecar container to push 2h blocks to 01-Sandbox S3  │
│  - Configure S3 Lifecycle: S3 Standard (30d) -> Glacier Instant (1y)   │
│  - Result: 100% self-contained, infinite retention, 71%-93% savings,   │
│            zero additional server daemons, < 64 MiB RAM footprint      │
├────────────────────────────────────────────────────────────────────────┤
│ ALTERNATIVE: Option 1 (Provision Monolithic Cortex in 01-Sandbox)      │
│  - Deploy Cortex single-binary Helm chart inside PrimaryHub KinD       │
│  - Configure S3 bucket backend in 01-Sandbox AWS account               │
│  - Configure Prometheus remote_write to internal Cortex service        │
│  - Trade-off: Consumes 2–4 GiB RAM; viable only if EC2 node is upsized │
└────────────────────────────────────────────────────────────────────────┘
```

Detailed step-by-step implementation procedures for both architectures are documented in [`docs/prometheus/long-term-strorage-cortex-s3-implementation.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/long-term-strorage-cortex-s3-implementation.md).

---

## 9. Conclusion

1. **EBS is unsustainable for long-term retention**: Persisting metrics beyond 7–14 days on EBS creates prohibitive AWS costs ($0.08–$0.10/GB-month) and introduces single-AZ availability risks.
2. **Provisioning Cortex inside 01-Sandbox**: While Cortex provides robust TSDB storage and multi-tenancy, running it locally inside the 01-Sandbox KinD cluster imposes a heavy memory tax (2–4 GiB RAM) on the host EC2 instance.
3. **The Optimal Strategy**:
   - Adopt **Prometheus S3 Block Shipping (Thanos Sidecar)** directly inside 01-Sandbox.
   - All telemetry and storage remain **100% internal and sovereign** to 01-Sandbox.
   - Delivers immediate 71%–93% storage cost savings with minimal operational complexity.
