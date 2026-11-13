# Multi-Region Observability Architecture: Push-Based Prometheus, Zero-Downtime Secret Rotation, and Cortex S3 Long-Term Storage

**Companion Slide Deck for Engineering, Architecture, and Leadership Reviews**
**Document:** Presentation Slide Outlines & Speaker Script
**File Reference:** [presentation-slides.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/presentation-slides.md)
**Technical Companion:** [presentation-technical-depth.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/presentation-technical-depth.md)

---

## Slide Index & Agenda

1. [Slide 1: Executive Title & Vision](#slide-1-executive-title--vision)
2. [Slide 2: The Core Architectural Challenges in Multi-Region Telemetry](#slide-2-the-core-architectural-challenges-in-multi-region-telemetry)
3. [Slide 3: High-Level Solution Architecture: The Unified Triad](#slide-3-high-level-solution-architecture-the-unified-triad)
4. [Slide 4: Pillar 1 — Push-Based Prometheus Remote Write over Encrypted WireGuard](#slide-4-pillar-1--push-based-prometheus-remote-write-over-encrypted-wireguard)
5. [Slide 5: Data Journey: From Edge Scrape to Central Ingestion](#slide-5-data-journey-from-edge-scrape-to-central-ingestion)
6. [Slide 6: Pillar 2 — Zero-Downtime Autonomous Secret Rotation](#slide-6-pillar-2--zero-downtime-autonomous-secret-rotation)
7. [Slide 7: Secret Rotation in Action: The Dual-Auth Overlap Pattern](#slide-7-secret-rotation-in-action-the-dual-auth-overlap-pattern)
8. [Slide 8: Autonomous Spoke Distribution & Cloud-Init Bootstrap](#slide-8-autonomous-spoke-distribution--cloud-init-bootstrap)
9. [Slide 9: Pillar 3 — The Storage Dilemma: Why EBS Fails at Scale](#slide-9-pillar-3--the-storage-dilemma-why-ebs-fails-at-scale)
10. [Slide 10: Dedicated Cortex Engine with AWS S3 Tiering](#slide-10-dedicated-cortex-engine-with-aws-s3-tiering)
11. [Slide 11: Enterprise Security Architecture: Defense-in-Depth](#slide-11-enterprise-security-architecture-defense-in-depth)
12. [Slide 12: Unified Multi-Cluster Observability in Central Grafana](#slide-12-unified-multi-cluster-observability-in-central-grafana)
13. [Slide 13: Concrete Business & Engineering Impact (Before vs. After)](#slide-13-concrete-business--engineering-impact-before-vs-after)
14. [Slide 14: Roadmap, Summary & Q&A](#slide-14-roadmap-summary--qa)

---

## Slide 1: Executive Title & Vision

### Slide Content

```
========================================================================================
                      ENTERPRISE MULTI-REGION OBSERVABILITY
   Push-Based Prometheus | Zero-Downtime Secret Rotation | Cortex & AWS S3 Cold Tiering
========================================================================================
                  Zero Data Loss  •  Zero Host Contention  •  Zero Downtime
```

* **Target Environment:** 01-Sandbox Global Multi-Cluster Topology
  * **Central Hub:** `us-east-1` (Virginia)
  * **Secondary Hub:** `eu-west-1` (Ireland)
  * **Edge Spokes:** `eu-central-1` (Frankfurt), `ap-south-1` (Mumbai)
* **Core Technological Triad:**
  1. **Push-Based Scrape & Transit:** In-cluster edge scrapers pushing Snappy-compressed metric batches over WireGuard mesh.
  2. **Autonomous Secret Rotation:** Kubernetes-native CronJob executing zero-downtime Dual-Auth rotation via OCM and AWS SSM.
  3. **Decoupled Long-Term Storage:** Dedicated hardened Cortex node streaming compacted TSDB blocks into durable, low-cost AWS S3.

---

### Presenter Notes (What to Say)
> "Good morning team. Today I am presenting our newly upgraded, production-grade observability architecture for the 01-Sandbox infrastructure. As we expanded across multiple AWS regions—Virginia, Frankfurt, Mumbai, and Ireland—our legacy monitoring approach hit severe architectural boundaries in network latency, security risk, and EBS storage limits.
>
> Today, I'll walk you through how we resolved these three pillars: first, implementing push-based Prometheus remote write over an encrypted WireGuard mesh; second, introducing fully automated, zero-downtime credential rotation with Dual-Auth overlap; and third, deploying a dedicated, hardened Cortex node backed by AWS S3 that cuts our storage footprint by over 75% while providing multi-year historical queries."

---

## Slide 2: The Core Architectural Challenges in Multi-Region Telemetry

### Slide Content

| Dimension | Legacy Central Pull Model | Operational & Security Consequences |
| :--- | :--- | :--- |
| **Network & Firewall** | Central Hub scrapes remote edge clusters across public WAN | Requires opening inbound firewall ports on edge clusters; exposed attack surface |
| **WAN Flakiness** | Ephemeral packet drops and regional latency spikes (150ms–280ms) | Prometheus scrape timeouts cascade; permanent metric gaps during network jitter |
| **Credential Hygiene** | Static, hard-coded Basic Auth credentials across all clusters | Massive blast radius if compromised; manual rotation causes fleet-wide scrape downtime |
| **Storage & Host Footprint** | Monolithic TSDB retained on AWS EBS on PrimaryHub | EBS volume fills crash databases; compactor CPU/RAM spikes starve Valkey & PostgreSQL |
| **Cost at Scale** | AWS EBS gp3 storage priced at \$0.08/GB-month + provisioned IOPS | Exponentially expensive for 1–3 years of telemetry retention; single-AZ lock-in |

* **The Core Problem Statement:**
  > *Pulling metrics across cross-continental WANs over static credentials onto a single EBS volume is insecure, brittle to network jitter, and financially unsustainable.*

---

### Presenter Notes (What to Say)
> "To understand why we made these architectural investments, look at the five critical failure points of traditional Prometheus architectures across regions.
>
> In a pull model, a central server tries to reach out to remote edge nodes in Frankfurt or Mumbai. That forces us to punch holes in edge firewalls. Even worse, whenever cross-continental WAN connectivity flutters, samples are dropped forever because Prometheus pull has zero local buffering.
>
> Furthermore, credentials were static, meaning rotating them required coordinated maintenance windows. Finally, retaining months of raw data on AWS EBS was risking disk exhaustion on our PrimaryHub, threatening core production workloads like Valkey and PostgreSQL."

---

## Slide 3: High-Level Solution Architecture: The Unified Triad

### Slide Content

```
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │                          EDGE SPOKE REGIONS (eu-central-1, ap-south-1)           │
   │   In-Cluster Targets ──► Local Prometheus Scraper ──► Local WAL Buffer (6h)      │
   └────────────────────────────────────────┬─────────────────────────────────────────┘
                                            │ Push: Encrypted WireGuard (10.99.0.0/24)
                                            │ Batched & Snappy Compressed (Protobuf)
                                            ▼
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │                          PRIMARYHUB REGION (us-east-1)                           │
   │                                                                                  │
   │  ┌───────────────────────────┐                ┌───────────────────────────────┐  │
   │  │   Secret Rotator CronJob  │                │    PrimaryHub Prometheus      │  │
   │  │   - 30-Day Auto Trigger   │                │    - Dual-Auth Security Gate  │  │
   │  │   - Dual-Auth Overlap     │ ──────────────►│    - Lean In-Memory Buffer    │  │
   │  │   - OCM & SSM Sync        │                │    - 6h Retention Only        │  │
   │  └───────────────────────────┘                └──────────────┬────────────────┘  │
   └──────────────────────────────────────────────────────────────┼───────────────────┘
                                                                  │ Remote Write Push
                                                                  │ (cortex-writer)
                                                                  ▼
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │                          DEDICATED CORTEX NODE (us-east-1 Private VPC)           │
   │   NGINX TLS 1.3 / RBAC Gate ──► Cortex TSDB Engine ──► AWS S3 Bucket             │
   │   (Tenant: sandbox-metrics)     (2h Local WAL Flush)   (01-sandbox-cortex-storage)│
   └────────────────────────────────────────┬─────────────────────────────────────────┘
                                            │ PromQL Historical Queries
                                            ▼
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │                          CENTRAL GRAFANA DASHBOARDS                              │
   │   Unified Spoke & Hub Fleet Visibility • Sub-Second Query Execution              │
   └──────────────────────────────────────────────────────────────────────────────────┘
```

* **Three Independent, Resilient Layers:**
  1. **Edge Resiliency:** Edge clusters scrape locally and push upstream. If WAN disconnects, edge WAL holds samples safely.
  2. **Zero-Trust Credential Plane:** Credentials rotate automatically with zero downtime using Dual-Auth overlap.
  3. **Decoupled Durable Tier:** Cortex buffers writes to local EBS and flushes immutable blocks to AWS S3.

---

### Presenter Notes (What to Say)
> "Here is the bird's-eye view of the system we built.
>
> Notice how the responsibilities are cleanly decoupled. At the edge, spokes collect their own telemetry and store it in an ephemeral write-ahead log. They push those metrics across our encrypted WireGuard mesh into PrimaryHub.
>
> On PrimaryHub, Prometheus acts as an intelligent router and lean ingestion buffer with only 6 hours of retention. It verifies edge credentials against a dynamic Dual-Auth gate managed by an automated CronJob.
>
> Then, PrimaryHub streams the consolidated telemetry directly into our dedicated Cortex instance. Cortex buffers data for 2 hours, deduplicates it, compresses it into immutable blocks, and ships it to AWS S3. Grafana queries both real-time data from PrimaryHub and multi-year data from Cortex seamlessly."

---

## Slide 4: Pillar 1 — Push-Based Prometheus Remote Write over Encrypted WireGuard

### Slide Content

```
   [ Local Scrape Target ]
             │ (15s Poll via Localhost / Pod Network)
             ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ EDGE SPOKE PROMETHEUS SCRAPER                                          │
   │                                                                        │
   │  1. In-Cluster TSDB WAL Buffer (emptyDir, 6-Hour Retention)            │
   │     - Crash-Safe: Appends to disk synchronously before network transit │
   │     - Disconnection Immunity: If WAN drops, scrapes continue buffering │
   │                                                                        │
   │  2. WAL Watcher Worker Subsystem                                       │
   │     - Concurrency: Multi-worker sample tailing                         │
   │     - Batching: Max 1,000 samples OR 5-second deadline                 │
   │     - Metadata Injection: cluster="spoke1", region="eu-central-1"      │
   │     - Serialization: Protocol Buffers (prompb.WriteRequest)            │
   │     - Compression: Google Snappy Algorithm (~88% Bandwidth Reduction)  │
   └────────────────────────────────────┬───────────────────────────────────┘
                                        │ HTTP POST /api/v1/write
                                        │ WireGuard VPN Overlay (10.99.0.0/24)
                                        │ Kernel Netfilter DNAT (NodePort 30900)
                                        ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ PRIMARYHUB PROMETHEUS (Receiver Mode: --web.enable-remote-write-receiver)│
   └────────────────────────────────────────────────────────────────────────┘
```

* **Key Advantages:**
  * **No Inbound Edge Firewall Holes:** Edge clusters establish outbound tunnels only.
  * **88% WAN Bandwidth Savings:** Raw metric payload compressed from ~1,024 B/sample to ~120 B/sample.
  * **Network Resilience:** Zero metric dropouts during cross-region network splits; WAL back-fills automatically on reconnect.

---

### Presenter Notes (What to Say)
> "Let's dive into Pillar 1: Push-Based Prometheus. Instead of having PrimaryHub scrape across the internet, each edge spoke cluster runs a lightweight Prometheus agent.
>
> The edge scraper polls local pods, cAdvisor, and system targets over the internal node network. Every sample is first written to a local write-ahead log on disk. That means if the trans-Atlantic fiber line glitches or latency spikes, the edge doesn't drop a single byte of telemetry.
>
> Next, an internal WAL Watcher subsystem batches samples into 1,000-metric chunks, injects external labels like cluster='spoke1' and region='eu-central-1', serializes them into Protocol Buffers, and compresses them with Google Snappy. This reduces WAN bandwidth by roughly 88%. The batch is transmitted over our private WireGuard mesh directly into PrimaryHub's remote-write receiver."

---

## Slide 5: Data Journey: From Edge Scrape to Central Ingestion

### Slide Content

```mermaid
sequenceDiagram
    autonumber
    participant Target as In-Cluster Target
    participant Scraper as Edge Spoke TSDB
    participant WALWatcher as WAL Watcher Engine
    participant WireGuard as WireGuard (10.99.0.0/24)
    participant Netfilter as Kernel DNAT (:30900)
    participant CentralProm as PrimaryHub Prometheus
    participant CortexNode as Dedicated Cortex S3

    Target->>Scraper: 1. Scrape metrics every 15s (CoreDNS, cAdvisor, Pods)
    Scraper->>Scraper: 2. Commit raw samples to local WAL on disk
    WALWatcher->>Scraper: 3. Tail committed samples from active WAL segment
    WALWatcher->>WALWatcher: 4. Batch (1,000 samples) + Inject cluster="spoke1"
    WALWatcher->>WALWatcher: 5. Snappy compress Protobuf payload
    WALWatcher->>WireGuard: 6. HTTP POST /api/v1/write + Authorization: Basic [User]
    WireGuard->>Netfilter: 7. Encrypted packet arrives at 10.99.0.1:30900
    Netfilter->>CentralProm: 8. Kernel DNAT routes into KinD Container (172.18.0.2)
    CentralProm->>CentralProm: 9. Validate Basic Auth against web.yml
    CentralProm-->>WALWatcher: 10. HTTP 204 No Content (Batch Acknowledged)
    CentralProm->>CortexNode: 11. Upstream Remote-Write to Cortex (S3 Long-Term Buffer)
```

* **Timeline Execution:**
  * Steps 1–2 (Scrape & Disk Commit): **< 2ms**
  * Steps 3–5 (Batching & Snappy Compression): **< 5ms**
  * Steps 6–8 (WireGuard Transit & Netfilter DNAT): **30ms–140ms** (geographic distance)
  * Steps 9–10 (Bcrypt Auth & Ingestion Commit): **< 4ms**
  * **End-to-End Freshness:** Real-time metrics appear on central dashboards in **< 6 seconds**.

---

### Presenter Notes (What to Say)
> "Here is the exact lifecycle of a metric sample. It takes less than 6 seconds from the moment a CPU spike occurs on an edge node in Mumbai to the moment it renders in our central Grafana in Virginia.
>
> Notice Step 2: because we commit to local disk first, network jitter cannot induce data loss. When the packet hits PrimaryHub, Linux Netfilter kernel DNAT seamlessly routes port 30900 into our container runtime.
>
> PrimaryHub validates the HTTP Basic Auth header against its active user list, accepts the metric with an HTTP 204 No Content, and then streams that data forward into Cortex for archival."

---

## Slide 6: Pillar 2 — Zero-Downtime Autonomous Secret Rotation

### Slide Content

* **The Security Imperative:**
  * Cloud security standards (SOC2, ISO 27001, CIS Benchmarks) mandate regular credential rotation.
  * Static passwords in configuration files create prolonged vulnerability windows and lateral movement risks.
* **The Distributed Rotation Dilemma:**
  * How do you rotate a shared secret across 5+ geographically distributed clusters without dropping a single write during the transition?

```
   TRADITIONAL (STATIC) ROTATION                      KUBERNETES-NATIVE DUAL-AUTH ROTATION
   ┌────────────────────────────────────────┐         ┌────────────────────────────────────────┐
   │ 1. Update Hub Password                 │         │ 1. Hub Activates [User_A + User_B]     │
   │ 2. Edge Nodes get HTTP 401 Unauthorized│         │ 2. Edge Nodes push with User_A or B    │
   │ 3. Metrics dropped across WAN          │   VS    │ 3. OCM updates Edge Spokes to User_B   │
   │ 4. Update Spokes one-by-one            │         │ 4. Zero 401 Errors, Zero Sample Loss   │
   │ 5. Normal operation resumes            │         │ 5. Grace period expires; Revoke User_A │
   │ Result: 10–30 Minutes Telemetry Outage │         │ Result: 100% Telemetry Continuity      │
   └────────────────────────────────────────┘         └────────────────────────────────────────┘
```

* **Architectural Innovation: The 4-Phase Lifecycle**
  1. **Phase 1: Dual-Auth Generation & Activation** (Hub simultaneously accepts Old + New).
  2. **Phase 2: Edge Distribution via OCM** (Push new credentials to edge spoke clusters).
  3. **Phase 3: AWS SSM Cloud-Init Sync** (Autonomous bootstrap for auto-scaled nodes).
  4. **Phase 4: Safe Legacy Revocation** (Graceful retirement of old credentials).

---

### Presenter Notes (What to Say)
> "Now let's examine Pillar 2: our automated secret rotation architecture. In distributed systems, rotating credentials is a notorious operational headache. If you update the server password first, all edge clients get 401 Unauthorized errors and your dashboards go dark. If you update the edge clients first, they fail until the server updates.
>
> To solve this completely, we engineered the Dual-Auth Overlap pattern. We deploy a Kubernetes CronJob that generates cryptographically strong passwords and patches PrimaryHub to accept BOTH the old and new credentials simultaneously.
>
> This creates an overlap window where edge nodes can migrate at their own pace without dropping a single sample. Once the fleet has migrated, the old secret is gracefully revoked."

---

## Slide 7: Secret Rotation in Action: The Dual-Auth Overlap Pattern

### Slide Content

```mermaid
sequenceDiagram
    autonumber
    actor Cron as Rotator CronJob (Day 30)
    participant HubSecret as K8s Secret: web.yml
    participant PromHub as PrimaryHub Prometheus
    participant OCM as OCM ManifestWork
    participant Spoke as Edge Spoke Scraper

    Note over Cron,PromHub: Phase 1: Dual-Auth Activation
    Cron->>Cron: Generate User_B & Bcrypt Hash ($2b$)
    Cron->>HubSecret: Patch web.yml with [User_A + User_B]
    Cron->>PromHub: Send POST /-/reload or SIGHUP
    PromHub->>PromHub: Hot-Reloads in 7.8ms (Zero Dropped Connections)
    Note over PromHub: PrimaryHub now accepts BOTH User_A & User_B

    Note over Cron,Spoke: Phase 2: Edge Fleet Migration
    Cron->>OCM: Deploy ManifestWork with User_B to spoke namespaces
    OCM->>Spoke: Deliver Secret to 'monitoring' namespace
    Spoke->>Spoke: Config-Reloader hot-reloads Prometheus agent
    Spoke->>PromHub: Push metrics using User_B (HTTP 204 OK)

    Note over Cron,PromHub: Phase 4: Legacy Credential Revocation (After 600s Grace Period)
    Cron->>HubSecret: Prune User_A; commit web.yml with [User_B only]
    Cron->>PromHub: Final POST /-/reload
    Note over PromHub: User_A safely revoked. Rotation 100% complete!
```

* **Core Operational Metrics:**
  * **Hot-Reload Time:** **7.8 milliseconds** via Prometheus configuration reloader.
  * **Service Restarts:** **0** (No container teardowns, no pod rescheduling).
  * **HTTP 401 Authorization Failures:** **0** across the entire rotation cycle.

---

### Presenter Notes (What to Say)
> "This sequence diagram details the zero-downtime mechanics.
>
> When the CronJob fires, it creates User B with a Blowfish Bcrypt hash and updates PrimaryHub's web.yml. A sub-10ms SIGHUP hot-reloads Prometheus without dropping a single TCP connection.
>
> At this exact moment, PrimaryHub accepts both User A and User B. Next, Open Cluster Management delivers User B to the spoke clusters across our encrypted mesh. The spoke config-reloader picks up the new secret and switches over.
>
> Even if a spoke takes 2 minutes to update due to network delay, it experiences zero 401 errors because User A is still valid. After a 10-minute grace period, the rotator cleans up User A, locking down the system with the new secret."

---

## Slide 8: Autonomous Spoke Distribution & Cloud-Init Bootstrap

### Slide Content

```
                                  ┌─────────────────────────────┐
                                  │   Secret Rotator CronJob    │
                                  └──────────────┬──────────────┘
                                                 │
                        ┌────────────────────────┴────────────────────────┐
                        │ Phase 2                                         │ Phase 3
                        ▼                                                 ▼
          ┌───────────────────────────┐                     ┌───────────────────────────┐
          │ Open Cluster Management   │                     │ AWS SSM Parameter Store   │
          │ (OCM ManifestWork Engine) │                     │ (us-east-1 Region)        │
          └─────────────┬─────────────┘                     └─────────────┬─────────────┘
                        │ WireGuard Mesh                                  │ AWS IAM SigV4
                        ▼                                                 ▼
          ┌───────────────────────────┐                     ┌───────────────────────────┐
          │ EXISTING ACTIVE SPOKES    │                     │ NEW EC2 AUTONOMOUS NODES  │
          │ - Spoke 1 (Frankfurt)     │                     │ - EC2 Cloud-Init Launch   │
          │ - Spoke 2 (Mumbai)        │                     │ - Pulls Active Creds Live │
          │ - Spoke 3 (Mumbai)        │                     │ - Deploys Metric Exporters│
          │ Dynamic K8s Secret Update │                     │ Instant Telemetry Ingest  │
          └───────────────────────────┘                     └───────────────────────────┘
```

* **Two Parallel Synchronization Paths:**
  1. **Existing Fleet (OCM ManifestWork):** Declarative, Kubernetes-native drift management pushes updated credentials directly into the `monitoring` namespace of registered spoke clusters.
  2. **Greenfield Fleet (AWS SSM Parameter Store):** Rotator updates `/sandbox/monitoring/rw-username` and `rw-password`. Any new EC2 instance provisioned by Terraform automatically fetches the current credential during `user_data` execution.
* **Result:** **Zero manual touch.** Whether maintaining existing clusters or launching new regions, security credentials stay synchronized automatically.

---

### Presenter Notes (What to Say)
> "Secret rotation isn't just about existing nodes—it's also about greenfield nodes.
>
> In Phase 2, we use Open Cluster Management's ManifestWork to declaratively synchronize existing spokes. But what happens if an auto-scaling event or Terraform run launches a brand-new spoke cluster tomorrow?
>
> That's where Phase 3 comes in: our CronJob also syncs the active credential to AWS SSM Parameter Store. When a new EC2 instance boots up, its cloud-init user data pulls the live credential directly from SSM using its IAM instance profile.
>
> It provisions its local scraper, configures its remote-write pipeline, and begins pushing metrics to PrimaryHub immediately. Zero human intervention required."

---

## Slide 9: Pillar 3 — The Storage Dilemma: Why EBS Fails at Scale

### Slide Content

```
   THE MONOLITHIC EBS STORAGE CRISIS (PRIMARYHUB)

   ┌────────────────────────────────────────────────────────────────────────┐
   │ AWS EBS Volume (gp3) Mounted to PrimaryHub EC2                         │
   │                                                                        │
   │  [ PostgreSQL DB ]  [ Valkey Cache ]  [ Prometheus TSDB: 100+ GB ]     │
   │         ▲                  ▲                       ▲                   │
   │         │                  │                       │                   │
   │  HIGH MEMORY CONTENTION & COMPACTION IOPS BURSTS   │                   │
   │  - Compactor consumes up to 4 GB RAM during 2h block merge             │
   │  - Disk fills up: triggers emergency pod evictions & DB read-only lock │
   │  - Cost: $0.08/GB-month + provisioned IOPS = $$$$                      │
   │  - Resilience: Single-AZ bound; no native regional DR                  │
   └────────────────────────────────────────────────────────────────────────┘
```

* **The Production Risks of EBS-Based Prometheus:**
  * **Resource Starvation:** TSDB compactor memory spikes risk starving core microVM orchestration, Valkey cache, and PostgreSQL databases.
  * **Catastrophic Disk Exhaustion:** Once an EBS volume reaches 100%, Prometheus halts writes and corrupts write-ahead logs.
  * **Economic Inefficiency:** Retaining 1 year of raw high-resolution metrics across 5 clusters on EBS costs hundreds of dollars monthly with zero tiered savings.

---

### Presenter Notes (What to Say)
> "Now let's examine our third and most impactful pillar: Long-Term Storage.
>
> Before this upgrade, PrimaryHub stored all incoming telemetry on an attached AWS EBS gp3 volume. While fine for a single cluster, as we added Frankfurt, Ireland, and Mumbai, this created an operational ticking time bomb.
>
> Prometheus TSDB compactor cycles consume massive amounts of RAM and disk IOPS every two hours when cutting blocks. On a shared host, those spikes directly compete with Valkey and PostgreSQL. If the EBS disk fills up, the entire database locks into read-only mode.
>
> Furthermore, EBS volumes are confined to a single AWS Availability Zone and cost \$0.08 per gigabyte-month. Keeping a year of telemetry on EBS simply doesn't scale."

---

## Slide 10: Dedicated Cortex Engine with AWS S3 Tiering

### Slide Content

```
   ┌─────────────────────────────────────────────────────────────────────────────────────┐
   │                   DEDICATED CORTEX EC2 NODE (10.99.0.50 / us-east-1)                │
   │                                                                                     │
   │   HTTPS Ingress (8443)                                                              │
   │           │                                                                         │
   │           ▼                                                                         │
   │   ┌───────────────────────────────────┐                                             │
   │   │ NGINX Security & Auth Proxy       │                                             │
   │   │ - TLS 1.3 Termination             │                                             │
   │   │ - Bcrypt Basic Auth Verification  │                                             │
   │   │ - Header: X-Scope-OrgID injected  │                                             │
   │   └─────────────────┬─────────────────┘                                             │
   │                     │ http://127.0.0.1:9009                                         │
   │                     ▼                                                               │
   │   ┌───────────────────────────────────┐       ┌──────────────────────────────────┐  │
   │   │ Cortex Monolithic Engine          │       │ Local Working EBS (30 GiB gp3)   │  │
   │   │ - Ingester: 2h In-Memory WAL      │ ◄───► │ - Flat, bounded disk usage       │  │
   │   │ - Compactor: Deduplicates blocks  │       │ - Safe against disk fill-ups     │  │
   │   │ - Store-Gateway: S3 Index Cache   │       └──────────────────────────────────┘  │
   │   └─────────────────┬─────────────────┘                                             │
   └─────────────────────┼───────────────────────────────────────────────────────────────┘
                         │ AWS SigV4 HTTPS (VPC S3 Gateway)
                         ▼
   ┌─────────────────────────────────────────────────────────────────────────────────────┐
   │                          AWS S3 OBJECT STORAGE BUCKET                               │
   │                           01-sandbox-cortex-storage                                 │
   │  - 99.999999999% (11 9s) Durability  •  AES-256 KMS Encryption  •  Multi-AZ Built-In│
   │  - Tiering: S3 Standard ($0.023/GB) ──► Infrequent Access ($0.0125/GB) ──► Glacier  │
   └─────────────────────────────────────────────────────────────────────────────────────┘
```

* **Core Operational Breakthroughs:**
  * **Flat Local Disk Footprint:** Working disk never exceeds ~10–20 GiB. Local EBS is only used for the active 2-hour buffer.
  * **Zero PrimaryHub Resource Impact:** Cortex runs on its own isolated EC2 instance (`t3.medium`).
  * **Infinite Retention at Low Cost:** Historical telemetry resides in AWS S3 with automated lifecycle transition policies.

---

### Presenter Notes (What to Say)
> "Here is our solution: a dedicated, hardened Cortex node paired with AWS S3.
>
> We isolated Cortex on its own dedicated EC2 instance in a private VPC subnet. Incoming writes pass through an NGINX security proxy that terminates TLS 1.3 and enforces multi-tenant authentication.
>
> The Cortex ingester holds raw samples in a 2-hour write-ahead log on a small 30 GiB EBS volume. Every 2 hours, the compactor seals those samples into immutable, compressed TSDB blocks and flushes them directly into our S3 bucket, `01-sandbox-cortex-storage`.
>
> Once shipped to S3, the local chunks are purged. This means our local disk usage stays completely flat forever. S3 gives us 11 nines of durability, native multi-AZ replication, and cuts our storage costs from \$0.08 down to \$0.023 per gigabyte."

---

## Slide 11: Enterprise Security Architecture: Defense-in-Depth

### Slide Content

```
                                  DEFENSE-IN-DEPTH SECURITY LAYERS

  Layer 1: Network Isolation       Private VPC Subnet • No Public IPv4 • No Internet Ingress
                                   All inter-cluster traffic encapsulated in WireGuard VPN (ChaCha20-Poly1305)
              ▼
  Layer 2: Transport Security      TLS 1.3 End-to-End Encryption • Strict Cipher Suites (P-256, RSA-4096)
              ▼
  Layer 3: Security Reverse Proxy  NGINX Reverse Proxy (Port 8443) • Rate Limiting & Header Sanitization
                                   Admin Endpoints (/ring, /compactor) explicitly BLOCKED
              ▼
  Layer 4: Role-Based Access       Write-Only RBAC: cortex-writer limited strictly to POST /api/v1/push
                                   Read-Only RBAC:  grafana-reader limited strictly to GET /prometheus/*
              ▼
  Layer 5: Multi-Tenant Partition  Cortex auth_enabled: true • Injects X-Scope-OrgID: sandbox-metrics
                                   Metrics cryptographically isolated inside dedicated S3 prefix
              ▼
  Layer 6: Host Hardening          Zero SSH (Port 22 closed) • AWS SSM Session Manager Only
                                   IMDSv2 Enforced (Hop Limit 4) • S3 Access via Least-Privilege IAM Roles
```

* **Zero Public Surface Area:** Even if an attacker scans public AWS IP ranges, the Cortex node is completely invisible.
* **Cryptographic Segmentation:** A compromised reader credential cannot overwrite or tamper with ingested metrics.

---

### Presenter Notes (What to Say)
> "Security was our foremost design constraint. We implemented a six-layer defense-in-depth model for the Cortex deployment.
>
> First, the node has zero public IP addresses and no open SSH port 22. Administration is conducted exclusively through AWS Systems Manager Session Manager.
>
> Second, transport is secured with TLS 1.3 over private WireGuard tunnels.
>
> Third, our NGINX proxy enforces strict Role-Based Access Control: the writer account can ONLY execute HTTP POSTs to the push endpoint, while the Grafana reader account can ONLY issue GET requests to query endpoints. Internal Cortex admin APIs like the hash ring are blocked.
>
> Finally, we enforce IMDSv2, multi-tenant S3 prefix isolation, and KMS server-side encryption at rest."

---

## Slide 12: Unified Multi-Cluster Observability in Central Grafana

### Slide Content

```
   ┌────────────────────────────────────────────────────────────────────────────────────────┐
   │ GRAFANA DASHBOARD: Spoke Edge Fleet - Dynamic Multi-Cluster Observability             │
   │                                                                                        │
   │ [Cluster Dropdown: spoke1 | spoke2 | spoke3 | primaryhub | secondaryhub] [Refresh: 10s]│
   ├────────────────────────────┬────────────────────────────┬──────────────────────────────┤
   │ Prometheus Scraper Health  │ CoreDNS Service Health     │ CoreDNS Query Throughput     │
   │        HEALTHY             │          ONLINE            │          12.4 req/s          │
   ├────────────────────────────┴────────────────────────────┴──────────────────────────────┤
   │ CoreDNS Request Latency (P99, P90, Avg)                                                │
   │  3.2ms ──┐                                                                             │
   │  0.4ms   └───-─────────────────────────────────────────────────────── (Real-Time Push) │
   ├────────────────────────────────────────────────────────────────────────────────────────┤
   │ Ingestion Engine Status & Active Series                                                │
   │  Spoke1: 890 series  |  Spoke2: 910 series  |  Spoke3: 875 series  |  Hub: 2,450 series│
   └────────────────────────────────────────────────────────────────────────────────────────┘
```

* **Datasource Architecture:**
  * **Hot Real-Time Tier (`Prometheus`):** Queries PrimaryHub TSDB directly for instant sub-second alerts and dashboard views (< 6 hours).
  * **Cold Durable Tier (`cortex-s3-longterm`):** Queries Cortex Store-Gateway for historical trend analysis, SLO reporting, and multi-year auditing.
* **Dynamic Fleet Templating:** New edge spokes appear automatically in the `$cluster` variable as soon as they push their first metric batch.

---

### Presenter Notes (What to Say)
> "Here is what the end result looks like for our engineering team in Grafana.
>
> We have a single pane of glass for the entire global fleet. Through Grafana templating, the dashboard dynamically queries the label index: the moment Spoke 3 or any future cluster comes online, it immediately populates in the cluster selector dropdown.
>
> Engineers can inspect real-time metrics—like CoreDNS latency, memory utilization, and cAdvisor container stats—with sub-second refresh rates.
>
> When investigating long-term capacity or quarterly trends, Grafana queries our cortex-s3-longterm datasource, which transparently streams blocks from S3 without putting any load on PrimaryHub."

---

## Slide 13: Concrete Business & Engineering Impact (Before vs. After)

### Slide Content

| Metric / Dimension | Before Implementation | After Implementation | Measurable Impact |
| :--- | :--- | :--- | :--- |
| **Edge Firewall Ingress** | Required open inbound ports for Hub scraping | **Zero inbound ports** (Outbound WireGuard only) | **100% reduction** in exposed network attack surface |
| **WAN Packet Loss Resilience** | Direct metric drops during network jitter | **6-Hour Local Disk WAL Buffer** on edge | **Zero data loss** during cross-region WAN disconnects |
| **Secret Rotation Downtime** | 10–30 min manual downtime per rotation | **7.8ms Hot-Reload** with Dual-Auth Overlap | **Zero downtime**; 100% continuous metric ingestion |
| **PrimaryHub RAM Contention** | Compactor spikes up to 4 GB RAM on Hub | **Dedicated Cortex Node** on isolated instance | **Zero host memory contention** with Valkey/Postgres |
| **Storage Infrastructure Cost** | \$0.08 / GB-month (AWS EBS gp3) | **\$0.023 / GB-month** (AWS S3 Standard) | **> 71% reduction** in raw storage expenditure |
| **Historical Data Retention** | Capped at 7–14 days before disk fill | **Multi-Year Durable Retention** in AWS S3 | **Uncapped compliance** and capacity forecasting |

---

### Presenter Notes (What to Say)
> "Let's summarize the bottom-line engineering and business impact.
>
> From a security perspective, we eliminated 100% of inbound firewall openings on our edge nodes, and credential rotation went from an error-prone manual operation to a sub-10ms automated process.
>
> From an operational reliability perspective, local WAL buffering ensures cross-region network splits no longer create data gaps. PrimaryHub is protected from compactor RAM spikes, guaranteeing stability for our database workloads.
>
> And financially, migrating long-term metrics from EBS to S3 cuts our storage bill by over 71% while turning a 7-day retention limit into multi-year durability."

---

## Slide 14: Roadmap, Summary & Q&A

### Slide Content

```
   ┌──────────────────────────────────────────────────────────────────────────────────┐
   │                                ARCHITECTURE SUMMARY                              │
   │                                                                                  │
   │  ✓ Push-Based Scraper Engine   : Edge WAL buffering + Snappy over WireGuard      │
   │  ✓ Kubernetes-Native Dual-Auth : Zero-downtime automated 30-day credential cycle │
   │  ✓ Dedicated Cortex & AWS S3   : Infinite durability, flat EBS disk, 71% savings │
   │  ✓ Production Status           : 100% Deployed & Active across All 5 Clusters    │
   └──────────────────────────────────────────────────────────────────────────────────┘
```

* **Next Phase Engineering Roadmap:**
  1. **Automated S3 Lifecycle Tiering:** Move metrics older than 90 days to S3 Glacier Instant Retrieval for an additional 60% cost reduction.
  2. **Multi-Region Cortex Read Replicas:** Deploy local Store-Gateways in European and Asian regions to accelerate cross-continental query performance.
  3. **Alertmanager Integration:** Distribute alerting rules directly to Cortex Ruler for centralized evaluation across historical and real-time data.
* **Floor Open for Questions:**
  * Technical Companion Document: [presentation-technical-depth.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/presentation-technical-depth.md)
  * Implementation Guide: [long-term-strorage-cortex-s3-implementation.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/long-term-strorage-cortex-s3-implementation.md)

---

### Presenter Notes (What to Say)
> "In summary, we have transformed 01-Sandbox's observability from a fragile, single-cluster pull setup into a resilient, enterprise-grade telemetry pipeline that scales effortlessly across regions.
>
> All five clusters—PrimaryHub, SecondaryHub, and Spokes 1, 2, and 3—are live, authenticated, and streaming to Cortex and S3 right now. Looking ahead, our next steps will be adding automated Glacier lifecycle policies and Cortex Ruler alerting.
>
> For those interested in the low-level packet flows, configuration files, and mathematical models, please refer to our companion technical depth manual. Thank you, and I'd be happy to open the floor to any questions."
