# Push-Based Prometheus Remote Write Architecture & Sequence Flow

This document provides a dedicated, comprehensive breakdown of the **Push-Based Prometheus Remote Write (PRW)** architecture used in the **01-Sandbox multi-cluster infrastructure**. It tracks every metric sample from its initial edge scrape, through crash-safe disk journaling, in-flight Snappy compression, authenticated WireGuard transit, kernel Netfilter routing, Central TSDB ingestion on AWS EBS, and instant Grafana visualization.

---

## 1. End-to-End Sequence Diagram (Mermaid)

The following sequence diagram illustrates the complete chronological path of a metric sample:

```mermaid
sequenceDiagram
    autonumber

    actor User as Engineer / Browser
    participant Rotator as Secret Rotator (CronJob)
    participant OCM as OCM Hub (ManifestWork)
    participant Targets as K8s Targets (cAdvisor, OCM)
    participant Scraper as Spoke Prometheus (Edge)
    participant DiskWAL as Local Disk WAL (/prometheus/wal)
    participant WALWatcher as WAL Watcher (Workers)
    participant SSM as AWS SSM (/sandbox/monitoring/rw-*)
    participant WireGuard as WireGuard VPN (10.99.0.0/24)
    participant Netfilter as Linux Kernel (DNAT :30900)
    participant WebAuth as Prometheus Web Auth (web.yml)
    participant CentralProm as Central TSDB (Receiver)
    participant EBS as AWS EBS (7d Retention)
    participant Grafana as Central Grafana

    %% PHASE 0: AUTOMATED SECRET ROTATION (KUBERNETES-NATIVE DUAL-AUTH)
    Note over Rotator, CentralProm: Phase 0 — Automated Zero-Downtime Secret Rotation (Every 30d / On-Call)
    Rotator->>Rotator: 1. Generate User_B, Pass_B & Blowfish/Bcrypt hash ($2b$)
    Rotator->>WebAuth: 2. Patch secret/prometheus-web-config: [User_A + User_B] (Dual-Auth)
    Rotator->>CentralProm: 3. Hot-reload via POST /-/reload (7.8ms, zero restarts)
    Note over CentralProm: PrimaryHub now simultaneously accepts both User_A and User_B
    Rotator->>OCM: 4. Create/Update ManifestWork in spoke namespaces (spoke1, spoke2)
    OCM->>WireGuard: 5. Deliver Secret across encrypted WireGuard mesh
    WireGuard->>Scraper: 6. klusterlet-work-agent applies Secret to monitoring namespace
    Scraper->>Scraper: 7. Config-reloader detects secret change & calls POST /-/reload
    Note over Scraper: Spoke switches to User_B with zero 401 errors or dropped samples
    Rotator->>SSM: 8. Sync User_B to AWS SSM (/sandbox/monitoring/rw-*) for new EC2 joins
    Note over Rotator: 9. Overlap grace period (600s) ensures all distributed spokes switch
    Rotator->>WebAuth: 10. Prune User_A; commit final web.yml (only User_B)
    Rotator->>CentralProm: 11. Final POST /-/reload; User_A is safely revoked

    %% PHASE 1: LOCAL SCRAPING & WAL COMMIT
    Note over Targets, DiskWAL: Phase 1 — Local Scrape & Crash-Safe WAL (Network-Independent)
    Scraper->>Targets: 12. Poll metrics every 15s (localhost / in-cluster)
    Targets-->>Scraper: Return ~60 samples/sec
    Scraper->>DiskWAL: 13. Append to active segment (sync, uncompressed)
    Note over DiskWAL: Scrapes continue appending uninterrupted even if WAN drops

    %% PHASE 2: WAL WATCHING, BATCHING & COMPRESSION
    Note over DiskWAL, WALWatcher: Phase 2 — WAL Watching, Batching & Snappy Compression
    WALWatcher->>DiskWAL: 14. Tail committed samples (like 'tail -f')
    DiskWAL-->>WALWatcher: Stream new sample bytes
    WALWatcher->>WALWatcher: 15. Pool batches (<=1000 samples or 5s deadline)
    WALWatcher->>WALWatcher: 16. Attach: cluster="spoke1", role="spoke", region="eu-central-1"
    WALWatcher->>WALWatcher: 17. Serialize -> Protobuf (prompb.WriteRequest)
    WALWatcher->>WALWatcher: 18. Google Snappy compress (~88% WAN reduction)

    %% PHASE 3: AUTHENTICATED TRANSIT & KERNEL NAT
    Note over WALWatcher, WebAuth: Phase 3 — Authenticated Transit & Kernel NAT Ingress
    WALWatcher->>WireGuard: 19. HTTP POST /api/v1/write + Authorization: Basic [User_B]
    WireGuard->>Netfilter: 20. Arrives at 10.99.0.1:30900 (ChaCha20 decrypted)
    Netfilter->>WebAuth: 21. Kernel DNAT -> KinD container (172.18.0.2:30900)

    %% PHASE 4: AUTHENTICATION, INGESTION & PERSISTENCE
    Note over WebAuth, EBS: Phase 4 — Security Gate (Bcrypt), TSDB Ingest & 7-Day EBS Storage
    WebAuth->>WebAuth: 22. Validate basic_auth against web.yml (bcrypt hash)
    alt Credentials Valid
        WebAuth->>CentralProm: 23. Forward payload to Remote Write Receiver
        CentralProm->>CentralProm: 24. Decompress Snappy & index {cluster="spoke1"}
        CentralProm->>CentralProm: 25. Append into in-memory Head Chunks
        CentralProm-->>WALWatcher: 26. HTTP 204 No Content (batch committed)
    else Invalid / Missing Credentials
        WebAuth-->>WALWatcher: 27. HTTP 401 Unauthorized (Write Blocked)
    end
    CentralProm->>EBS: 28. Every 2h: compact to immutable blocks on EBS
    Note over EBS: TSDB compactor auto-purges blocks older than 7 days

    %% PHASE 5: VISUALIZATION
    Note over User, Grafana: Phase 5 — Instant Visualization via Authenticated Datasource
    User->>Grafana: 29. Select dashboard & pick "spoke1" in $cluster dropdown
    Grafana->>WebAuth: 30. PromQL via local DNS (< 3ms) with Basic Auth
    WebAuth->>CentralProm: 31. Pass authenticated query: up{cluster="spoke1"}
    CentralProm-->>Grafana: 32. Return TSDB data instantly
    Grafana-->>User: 33. Dashboard renders in < 0.3s
```

---

## 2. In-Depth Architectural Phase Breakdown

### Phase 0: Automated Zero-Downtime Secret Rotation & Dynamic Bootstrap
* **Steps 1–11**:
  1. Every 30 days (or on demand), the Kubernetes-native `prometheus-secret-rotator` CronJob executes in PrimaryHub's `opensandbox-system` namespace.
  2. Generates a new credential pair (`User_B`, `Pass_B`) and computes a Blowfish/Bcrypt `$2b$` hash via Python's standard library `crypt.crypt(..., crypt.mksalt(crypt.METHOD_BLOWFISH))`, requiring zero pip dependencies.
  3. Patches `secret/prometheus-web-config` with **Dual-Auth (`[User_A + User_B]`)** under `basic_auth_users`.
  4. Triggers hot-reload via SIGHUP / `POST /-/reload` on PrimaryHub Prometheus (**sub-15ms latency**, zero container restarts, zero dropped TCP connections). PrimaryHub now simultaneously accepts both credentials.
  5. Creates or updates Open Cluster Management (OCM) `ManifestWork` in each spoke namespace (`spoke1`, `spoke2`).
  6. The spoke's local `klusterlet-work-agent` receives the payload across WireGuard and applies `secret/prometheus-remote-write-auth` into the `monitoring` namespace.
  7. Spoke's config-reloader sidecar detects the filesystem update and fires `POST /-/reload` on the edge scraper pod, cutting over to `User_B`.
  8. Simultaneously, the Rotator updates AWS SSM Parameter Store (`/sandbox/monitoring/rw-*`) so newly launched EC2 spoke instances bootstrap with the new secret automatically during cloud-init.
  9. Enters a **10-minute overlap grace period (`GRACE_PERIOD_SECONDS=600`)** to ensure all geographically distributed spokes (Frankfurt, Mumbai, Ireland) have completed their sync.
  10. Revokes `User_A` from `web.yml` and triggers final `POST /-/reload` on PrimaryHub Prometheus. `User_A` is now completely revoked; only `User_B` remains valid.

---

### Phase 1: Local Scraping & Crash-Safe WAL (Network-Independent)
* **Steps 12–13**:
  1. The edge scraper running inside the spoke cluster (e.g., `spoke1` in Frankfurt or `spoke2` in Mumbai) polls local targets (such as cAdvisor, CoreDNS, and OCM Klusterlet agents) every **15 seconds** over local in-cluster networking.
  2. As samples arrive (~59 samples/second), Prometheus appends them directly to sequential Write-Ahead Log (WAL) files in `/prometheus/wal/` (e.g. `00000014`, `00000015`, `00000016`).
  3. **Zero-Loss Guarantee**: This local write is **completely independent of the WAN link or PrimaryHub**. If the WireGuard VPN goes down, scraping never pauses; all metrics safely accumulate on disk.

---

### Phase 2: WAL Watching, Batching & Snappy Compression
* **Steps 14–18**:
  1. A background Prometheus worker thread called the **WAL Watcher** continuously tails the active WAL file (analogous to `tail -f`).
  2. Samples are buffered in memory and grouped into batches according to `queue_config`:
     * Flushes immediately when **1,000 samples** accumulate (`max_samples_per_send: 1000`).
     * Or flushes every **5 seconds** deadline (`batch_send_deadline: 5s`), whichever occurs first.
  3. The watcher attaches multi-cluster identity labels:
     ```yaml
     cluster: "spoke1"
     cluster_role: "spoke"
     region: "eu-central-1"
     ```
  4. The batch is serialized into standard Prometheus Protocol Buffers (`prompb.WriteRequest`).
  5. The serialized Protobuf payload is compressed using **Google Snappy** (`Content-Encoding: snappy`).
     * **Wire Efficiency**: Reduces the wire size from ~180–220 bytes/sample down to **~17.84 bytes/sample (~88% bandwidth reduction)**.

---

### Phase 3: Authenticated Transit over Encrypted WireGuard & Kernel NAT Ingress
* **Steps 19–21**:
  1. The worker issues an `HTTP POST /api/v1/write` containing the Snappy-compressed Protobuf payload and the HTTP Basic Authentication header:
     ```http
     POST /api/v1/write HTTP/1.1
     Host: 10.99.0.1:30900
     Authorization: Basic <base64_encoded_credentials>
     Content-Encoding: snappy
     Content-Type: application/x-protobuf
     ```
  2. The packet travels across the encrypted **WireGuard VPN mesh** (`10.99.0.0/24`) using ChaCha20-Poly1305 kernel encryption.
  3. When it reaches PrimaryHub host (`10.99.0.1:30900`), the **Linux Kernel Netfilter (iptables DNAT)** rule redirects the packet directly into the KinD Kubernetes container (`172.18.0.2:30900`) at wire speed without userspace proxy overhead.

---

### Phase 4: Dual-Auth Security Gate, In-Memory Head Chunks & Persistent AWS EBS Storage
* **Steps 22–28**:
  1. **Security Gate & Automated Secret Rotation**: The Prometheus server evaluates the incoming request against `--web.config.file=/etc/prometheus-web/web.yml`.
     * If credentials match any active user in `basic_auth_users` (verified against bcrypt hash), the request passes.
     * **Automated Zero-Downtime Rotation**: PrimaryHub runs a Kubernetes CronJob (`prometheus-secret-rotator`) that injects new rotating credentials alongside existing ones, triggers hot-reload in 7.8ms via `POST /-/reload`, distributes the secret to all spokes via OCM `ManifestWork`, and safely revokes the old secret after a 10-minute grace period.
     * If credentials are missing or wrong, Prometheus immediately returns **`HTTP 401 Unauthorized`**, blocking unauthorized access.
  2. **In-Memory Head Chunks**:
     * The payload is decompressed from Google Snappy.
     * The metrics are indexed by their origin tags (e.g., `cluster="spoke1"`, `job="coredns"`).
     * Newly arrived samples are immediately committed into **in-memory Head Chunks (RAM)**. This allows Grafana to query live data with sub-3ms response times without waiting for disk writes.
     * Simultaneously, samples append to PrimaryHub's local Write-Ahead Log (`/prometheus/wal/`) for crash recovery.
  3. **Acknowledgement**: PrimaryHub returns **`HTTP 204 No Content`** to the spoke WAL Watcher to confirm that the batch has been accepted and committed.
  4. **Where the Metrics Are Physically Stored on Disk**:
     * In the Prometheus container, metrics reside at `/prometheus`.
     * This path is backed by Kubernetes PVC `prometheus-pvc` (StorageClass `standard`), which maps through KinD directly to a dedicated **100 GiB AWS EBS (`gp3`) volume** mounted on the EC2 host:
       ```text
       Container Path:  /prometheus
            ▲
            │ (Kubernetes PVC mount: prometheus-pvc)
       KinD Host Path:  /data/prometheus
            ▲
            │ (Host ext4 mount)
       AWS Block Disk:  /dev/nvme1n1 (100 GiB AWS EBS gp3 volume: vol-007928387cbe1370f)
       ```
  5. **TSDB Block Layout on the EBS Disk**:
     * Every **2 hours**, the TSDB engine flushes in-memory samples into immutable 2-hour TSDB block directories in `/prometheus/`:
       ```text
       /prometheus/
       ├── 01JBY1ABC.../          ← 2-hour immutable block (e.g. 06:00–08:00)
       │   ├── chunks/            ← Metric samples compressed via Gorilla XOR (~1.37 bytes/sample)
       │   ├── index              ← Inverted index for label filtering (cluster="spoke1", etc.)
       │   └── meta.json          ← Block boundaries and sample statistics
       ├── chunks_head/           ← Staged memory chunks
       └── wal/                   ← PrimaryHub active write-ahead log buffer
       ```
  6. **7-Day Retention & EC2 Survival**:
     * The TSDB compactor enforces `--storage.tsdb.retention.time=7d`, automatically purging blocks older than 7 days.
     * The AWS EBS volume has `DeleteOnTermination=false`. If the PrimaryHub EC2 instance is terminated or replaced, the volume survives in AWS and cloud-init re-attaches it on next boot, restoring the full 7-day metric history immediately.

---

### Phase 5: Instant Local Visualization in Grafana (2-Tier RAM + EBS Read Engine)
* **Steps 21–25**:
  1. The user opens Grafana and selects a dashboard (e.g. *Spoke Edge Fleet – Dynamic Multi-Cluster Observability*).
  2. Grafana issues a PromQL query (e.g., `up{cluster="spoke1"}`) to Prometheus via in-cluster DNS (`http://prometheus-service.opensandbox-system.svc.cluster.local:9090`).
  3. **How Prometheus Queries Storage (Grafana Never Reads EBS Directly)**:
     * **Recent Metrics (< 2 hours)**: Prometheus serves immediately from **RAM** (In-Memory Head Chunks) in `< 1ms` without touching the disk.
     * **Historical Metrics (2 hours to 7 days)**: Prometheus queries the **AWS EBS volume (`/dev/nvme1n1`)** mounted at `/prometheus`. It scans the inverted `index` file and reads chunk byte ranges via Linux kernel memory mapping (`mmap`).
     * **Unified Merge**: The Prometheus query engine merges the RAM samples and the EBS disk blocks into a continuous time-series stream and serializes it to JSON.
  4. Because all spoke metrics have already been pushed to PrimaryHub, **no cross-WAN request is needed**.
  5. The query executes locally in **< 3ms**, and Grafana renders dashboard panels in **< 0.3s** (compared to 4–8 second delays and 504 timeouts in traditional pull models).

---

## 3. Related Documentation

* Detailed Walkthrough & Commands: [`walkthrough-demo.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/walkthrough-demo.md)
* 10-Minute Team Presentation Script: [`walkthrough-demo-final.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/walkthrough-demo-final.md)
* System Architecture & Runbook: [`prometheus.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/prometheus.md)
