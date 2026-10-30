# Push-Based Prometheus Remote Write Architecture & Sequence Flow

This document provides a dedicated, comprehensive breakdown of the **Push-Based Prometheus Remote Write (PRW)** architecture used in the **01-Sandbox multi-cluster infrastructure**. It tracks every metric sample from its initial edge scrape, through crash-safe disk journaling, in-flight Snappy compression, authenticated WireGuard transit, kernel Netfilter routing, Central TSDB ingestion on AWS EBS, and instant Grafana visualization.

---

## 1. End-to-End Sequence Diagram (Mermaid)

The following sequence diagram illustrates the complete chronological path of a metric sample:

```mermaid
sequenceDiagram
    autonumber

    actor User as Engineer / Browser
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

    %% PHASE 0: BOOTSTRAP & DYNAMIC CREDENTIALS
    Note over Scraper, SSM: Phase 0 — Zero-Touch Bootstrap & Dynamic SSM Credentials
    Scraper->>SSM: 1. EC2 boot: query /sandbox/monitoring/rw-username & rw-password
    SSM-->>Scraper: 2. Return credentials dynamically via IAM instance profile
    Scraper->>Scraper: 3. Configure basic_auth in remote_write block

    %% PHASE 1: LOCAL SCRAPING & WAL COMMIT
    Note over Targets, DiskWAL: Phase 1 — Local Scrape & Crash-Safe WAL (Network-Independent)
    Scraper->>Targets: 4. Poll metrics every 15s (localhost / in-cluster)
    Targets-->>Scraper: Return ~60 samples/sec
    Scraper->>DiskWAL: 5. Append to active segment (sync, uncompressed)
    Note over DiskWAL: Scrapes continue appending uninterrupted even if WAN drops

    %% PHASE 2: WAL WATCHING, BATCHING & COMPRESSION
    Note over DiskWAL, WALWatcher: Phase 2 — WAL Watching, Batching & Snappy Compression
    WALWatcher->>DiskWAL: 6. Tail committed samples (like 'tail -f')
    DiskWAL-->>WALWatcher: Stream new sample bytes
    WALWatcher->>WALWatcher: 7. Pool batches (<=1000 samples or 5s deadline)
    WALWatcher->>WALWatcher: 8. Attach: cluster="spoke1", role="spoke", region="eu-central-1"
    WALWatcher->>WALWatcher: 9. Serialize -> Protobuf (prompb.WriteRequest)
    WALWatcher->>WALWatcher: 10. Google Snappy compress (~88% WAN reduction)

    %% PHASE 3: AUTHENTICATED TRANSIT & KERNEL NAT
    Note over WALWatcher, WebAuth: Phase 3 — Authenticated Transit & Kernel NAT Ingress
    WALWatcher->>WireGuard: 11. HTTP POST /api/v1/write + Authorization: Basic [credentials]
    WireGuard->>Netfilter: 12. Arrives at 10.99.0.1:30900 (ChaCha20 decrypted)
    Netfilter->>WebAuth: 13. Kernel DNAT -> KinD container (172.18.0.2:30900)

    %% PHASE 4: AUTHENTICATION, INGESTION & PERSISTENCE
    Note over WebAuth, EBS: Phase 4 — Security Gate (Bcrypt), TSDB Ingest & 7-Day EBS Storage
    WebAuth->>WebAuth: 14. Validate basic_auth against web.yml (bcrypt hash)
    alt Credentials Valid
        WebAuth->>CentralProm: 15. Forward payload to Remote Write Receiver
        CentralProm->>CentralProm: 16. Decompress Snappy & index {cluster="spoke1"}
        CentralProm->>CentralProm: 17. Append into in-memory Head Chunks
        CentralProm-->>WALWatcher: 18. HTTP 204 No Content (batch committed)
    else Invalid / Missing Credentials
        WebAuth-->>WALWatcher: 19. HTTP 401 Unauthorized (Write Blocked)
    end
    CentralProm->>EBS: 20. Every 2h: compact to immutable blocks on EBS
    Note over EBS: TSDB compactor auto-purges blocks older than 7 days

    %% PHASE 5: VISUALIZATION
    Note over User, Grafana: Phase 5 — Instant Visualization via Authenticated Datasource
    User->>Grafana: 21. Select dashboard & pick "spoke1" in $cluster dropdown
    Grafana->>WebAuth: 22. PromQL via local DNS (< 3ms) with Basic Auth
    WebAuth->>CentralProm: 23. Pass authenticated query: up{cluster="spoke1"}
    CentralProm-->>Grafana: 24. Return TSDB data instantly
    Grafana-->>User: 25. Dashboard renders in < 0.3s
```

---

## 2. In-Depth Architectural Phase Breakdown

### Phase 0: Zero-Touch Bootstrap & Dynamic SSM Credentials
* **Steps 1–3**:
  1. During EC2 initialization or container startup, the spoke scraper queries the **AWS SSM Parameter Store** path `/sandbox/monitoring/rw-username` and `/sandbox/monitoring/rw-password` (with KMS decryption) using its IAM instance profile.
  2. Zero credentials or plaintext passwords are baked into disk images, git repositories, or Helm templates.
  3. The Prometheus Helm chart injects these credentials into its `remote_write` configuration block under `basic_auth`.

---

### Phase 1: Local Scraping & Crash-Safe WAL (Network-Independent)
* **Steps 4–5**:
  1. The edge scraper running inside the spoke cluster (e.g., `spoke1` in Frankfurt or `spoke2` in Mumbai) polls local targets (such as cAdvisor, CoreDNS, and OCM Klusterlet agents) every **15 seconds** over local in-cluster networking.
  2. As samples arrive (~59 samples/second), Prometheus appends them directly to sequential Write-Ahead Log (WAL) files in `/prometheus/wal/` (e.g. `00000014`, `00000015`, `00000016`).
  3. **Zero-Loss Guarantee**: This local write is **completely independent of the WAN link or PrimaryHub**. If the WireGuard VPN goes down, scraping never pauses; all metrics safely accumulate on disk.

---

### Phase 2: WAL Watching, Batching & Snappy Compression
* **Steps 6–10**:
  1. A background Prometheus worker thread called the **WAL Watcher** continuously tails the active WAL file (analogous to `tail -f`).
  2. Samples are buffered in memory and grouped into batches according to `queue_config`:
     * Flushes immediately when **1,000 samples** accumulate (`max_samples_per_send: 1000`).
     * Or flushes every **5 seconds** regardless (`batch_send_deadline: 5s`).
  3. The watcher attaches multi-cluster identity labels:
     ```yaml
     cluster: "spoke1"
     cluster_role: "spoke"
     region: "eu-central-1"
     ```
  4. The batch is serialized into a Google Protocol Buffer (`prompb.WriteRequest`) and compressed using **Google Snappy**, shrinking wire payload size by **~88%** (from ~200 bytes per sample down to ~17.8 bytes).

---

### Phase 3: Authenticated Transit & Kernel NAT Ingress
* **Steps 11–13**:
  1. The worker issues an `HTTP POST /api/v1/write` containing the Snappy-compressed Protobuf payload and the HTTP Basic Authentication header:
     ```http
     Authorization: Basic cHJvbWV0aGV1cy1ydzpQcm9tZXRoZXVzUmVtb3RlV3JpdGVTZWN1cmUyMDI2IQ==
     ```
  2. The packet travels across the encrypted **WireGuard VPN mesh** (`10.99.0.0/24`) using ChaCha20-Poly1305 kernel encryption.
  3. When it reaches PrimaryHub host (`10.99.0.1:30900`), the **Linux Kernel Netfilter (iptables DNAT)** rule redirects the packet directly into the KinD Kubernetes container (`172.18.0.2:30900`) at wire speed without userspace proxy overhead.

---

### Phase 4: Basic Auth Security Gate, TSDB Ingestion & 7-Day EBS Storage
* **Steps 14–20**:
  1. **Security Gate**: The Prometheus server evaluates the incoming request against `--web.config.file=/etc/prometheus-web/web.yml`.
     * If credentials are valid (verified against bcrypt hash), the request passes.
     * If credentials are missing or wrong, Prometheus immediately returns **`HTTP 401 Unauthorized`**, blocking unauthorized access.
  2. **Ingestion**: The payload is decompressed and incoming metrics are routed to in-memory Head Chunks, tagged by `cluster="spoke1"`.
  3. PrimaryHub returns **`HTTP 204 No Content`** to acknowledge that the batch has been accepted and committed.
  4. **Compaction & EBS Persistence**: Every 2 hours, Prometheus flushes in-memory samples into immutable 2-hour TSDB blocks on the **100 GiB AWS EBS volume** (`/dev/nvme1n1`). Metrics older than 7 days are automatically purged by the TSDB compactor.

---

### Phase 5: Instant Local Visualization in Grafana
* **Steps 21–25**:
  1. The user opens Grafana and selects a dashboard (e.g. *Spoke Edge Fleet – Dynamic Multi-Cluster Observability*).
  2. Grafana issues a PromQL query (e.g., `up{cluster="spoke1"}`) to Prometheus via in-cluster DNS (`http://prometheus-service.opensandbox-system.svc.cluster.local:9090`).
  3. Because all spoke metrics have already been pushed to PrimaryHub, **no cross-WAN request is needed**.
  4. The query executes locally in **< 3ms**, and the dashboard renders in **< 0.3s** (compared to 4–8 second delays in traditional pull models).

---

## 3. Related Documentation

* Detailed Walkthrough & Commands: [`walkthrough-demo.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/walkthrough-demo.md)
* 10-Minute Team Presentation Script: [`walkthrough-demo-final.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/walkthrough-demo-final.md)
* System Architecture & Runbook: [`prometheus.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/prometheus.md)
