# Cortex & S3 Long-Term Storage Architecture & Technical Manual

**Document:** Production Technical Specification & Operational Verification Manual
**File Reference:** [cortex-technical.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/cortex-technical.md)
**Related Architecture Docs:**
* Presentation Deck: [presentation-slides.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/presentation-slides.md)
* Technical Depth: [presentation-technical-depth.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/presentation-technical-depth.md)
* Secret Rotation: [kubernetes-secret-rotation-implementation.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/kubernetes-secret-rotation-implementation.md)
* Push Sequence: [prometheus-push-based-sequence.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/prometheus-push-based-sequence.md)

---

## Table of Contents

1. [Executive Summary & Global Topology](#1-executive-summary--global-topology)
2. [End-to-End Telemetry Ingestion Pipeline](#2-end-to-end-telemetry-ingestion-pipeline)
   * [2.1 Edge Spoke Scraper & Local WAL Buffer](#21-edge-spoke-scraper--local-wal-buffer)
   * [2.2 PrimaryHub Dual Routing: Real-Time Ingest & Upstream Remote Write](#22-primaryhub-dual-routing-real-time-ingest--upstream-remote-write)
   * [2.3 Dedicated Cortex Node & NGINX RBAC Security Gate](#23-dedicated-cortex-node--nginx-rbac-security-gate)
   * [2.4 Cortex Compactor & Shipper Subsystem to AWS S3](#24-cortex-compactor--shipper-subsystem-to-aws-s3)
   * [2.5 Central Grafana Dual-Tier Datasource Integration](#25-central-grafana-dual-tier-datasource-integration)
3. [Sequence Diagram: Edge Scrape to PrimaryHub, Cortex, and S3](#3-sequence-diagram-edge-scrape-to-primaryhub-cortex-and-s3)
4. [Step-by-Step Manual Verification Runbook](#4-step-by-step-manual-verification-runbook)
   * [Step 1: Inspect TSDB Blocks in AWS S3](#step-1-inspect-tsdb-blocks-in-aws-s3)
   * [Step 2: Verify Active Ingest Across All Clusters on PrimaryHub](#step-2-verify-active-ingest-across-all-clusters-on-primaryhub)
   * [Step 3: Monitor Real-Time Streaming on Cortex NGINX Proxy](#step-3-monitor-real-time-streaming-on-cortex-nginx-proxy)
   * [Step 4: Verify Block Compaction & S3 Upload in Cortex Logs](#step-4-verify-block-compaction--s3-upload-in-cortex-logs)
   * [Step 5: Verify Active Credentials in AWS SSM Parameter Store](#step-5-verify-active-credentials-in-aws-ssm-parameter-store)
5. [Configuration Blueprints](#5-configuration-blueprints)
   * [5.1 PrimaryHub Prometheus Remote Write Configuration](#51-primaryhub-prometheus-remote-write-configuration)
   * [5.2 Cortex NGINX Auth Proxy Configuration](#52-cortex-nginx-auth-proxy-configuration)
   * [5.3 Cortex Monolithic Configuration (`cortex.yaml`)](#53-cortex-monolithic-configuration-cortexyaml)
   * [5.4 Grafana Datasource Configuration](#54-grafana-datasource-configuration)
6. [Cortex Authentication Architecture](#6-cortex-authentication-architecture)
   * [6.1 Why Cortex Needs an Auth Proxy](#61-why-cortex-needs-an-auth-proxy)
   * [6.2 The 5-Layer Security Model](#62-the-5-layer-security-model)
   * [6.3 NGINX Dynamic RBAC Routing Logic](#63-nginx-dynamic-rbac-routing-logic)
   * [6.4 Cortex-to-S3 Authentication (AWS IAM & IMDSv2)](#64-cortex-to-s3-authentication-aws-iam--imdsv2)
   * [6.5 Authentication Sequence Diagram](#65-authentication-sequence-diagram)
   * [6.6 Automated Zero-Downtime Secret Rotation (`cortex-secret-rotator`)](#66-automated-zero-downtime-secret-rotation-cortex-secret-rotator)
   * [6.7 Hands-On Verification Commands](#67-hands-on-verification-commands)
7. [Operational Runbook & Troubleshooting](#7-operational-runbook--troubleshooting)
8. [Architectural Assessment: Cortex Advertised Features vs. 01-Sandbox Implementation](#8-architectural-assessment-cortex-advertised-features-vs-01-sandbox-implementation)
   * [8.1 Feature-by-Feature Alignment Matrix](#81-feature-by-feature-alignment-matrix)
   * [8.2 Why Monolithic Mode is the Right Choice for 01-Sandbox](#82-why-monolithic-mode-target-all-is-the-right-choice-for-01-sandbox)
   * [8.3 Path to Production Horizontal Scalability & Full HA](#83-path-to-production-horizontal-scalability--full-ha)

---

## 1. Executive Summary & Global Topology

The 01-Sandbox telemetry system decouples real-time monitoring from long-term metric persistence. Metric data collected from geographically distributed edge spoke clusters is simultaneously:
1. **Ingested into PrimaryHub Prometheus** for sub-second real-time dashboards and alerting (< 6-hour window).
2. **Streamed upstream to a dedicated Cortex instance** over an encrypted WireGuard mesh, where it is buffered, compacted into 2-hour immutable TSDB blocks, and uploaded to **AWS S3** (`01-sandbox-cortex-storage`) for multi-year durability.

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       01-SANDBOX GLOBAL TOPOLOGY                                       │
│                                                                                                        │
│   PRIMARYHUB (us-east-1)                 SECONDARYHUB (eu-west-1)             CORTEX NODE (us-east-1)  │
│   - Instance ID: i-0789b7609870c2216     - Instance ID: i-0510b983b7669c8ce   - Instance ID: i-00992e6b87f1461d8│
│   - Private IP: 10.20.134.219            - Private IP: 10.22.142.28           - Private IP: 10.20.154.244│
│   - WireGuard: 10.99.0.1                 - WireGuard: 10.99.0.2               - WireGuard: 10.99.0.50  │
│   - Port: :30900 (DNAT)                  - Role: Backup Hub / Scraper         - Role: S3 LTS Ingestion │
│                                                                                                        │
│   SPOKE 1 (eu-central-1)                 SPOKE 2 (ap-south-1)                 SPOKE 3 (ap-south-1)     │
│   - Instance ID: i-0432093a1a2619e62     - Instance ID: i-0c62a213344815af6   - Instance ID: Dynamic   │
│   - Private IP: 10.23.141.198            - Private IP: 10.21.129.126          - Private IP: Dynamic    │
│   - WireGuard: 10.99.0.3                 - WireGuard: 10.99.0.4               - WireGuard: 10.99.0.6   │
│   - Role: Frankfurt Edge Scraper         - Role: Mumbai Edge Scraper          - Role: Mumbai Edge Scraper│
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. End-to-End Telemetry Ingestion Pipeline

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. EDGE SPOKE CLUSTERS (spoke1, spoke2, secondaryhub)                                                 │
│    • In-Cluster Scrape: Scrapes pods, CoreDNS, cAdvisor, and node metrics locally every 15s.            │
│    • Local WAL Buffer: Writes samples to local disk WAL (/prometheus/wal) with 6-hour retention.      │
│    • Batching & Snappy: WAL Watcher batches 1,000 samples, adds `cluster="spoke1"`, compresses with    │
│      Google Snappy (~88% WAN bandwidth reduction), and pushes via HTTP POST.                           │
└───────────────────────────────────────────────────┬────────────────────────────────────────────────────┘
                                                    │ HTTP POST /api/v1/write (Basic Auth)
                                                    │ Over Encrypted WireGuard (10.99.0.0/24)
                                                    ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 2. PRIMARYHUB (10.20.134.219 / WireGuard 10.99.0.1:30900)                                              │
│    • Kernel Ingress: Netfilter DNAT forwards :30900 into KinD container (172.18.0.2).                 │
│    • Web Security Gate: Validates credentials against Dual-Auth registry (`web.yml`).                  │
│    • Dual Ingestion Routing:                                                                           │
│      ├── Path A (Real-Time Ingestion): Ingests into in-memory Head Chunks (6-hour retention on EBS).    │
│      │   └─► Queried by Central Grafana (< 15ms latency) for real-time fleet dashboards.               │
│      └── Path B (Remote-Write Stream): PrimaryHub Prometheus immediately streams every sample           │
│          upstream to Cortex via HTTPS: https://10.99.0.50:8443/api/v1/push                             │
└───────────────────────────────────────────────────┬────────────────────────────────────────────────────┘
                                                    │ HTTPS TLS 1.3 (Basic Auth: cortex-writer)
                                                    │ Over WireGuard Mesh (10.99.0.50)
                                                    ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 3. DEDICATED CORTEX NODE (10.20.154.244 / WireGuard 10.99.0.50)                                       │
│    • NGINX Reverse Proxy (Port 8443):                                                                 │
│      - Terminates TLS 1.3 and checks HTTP Basic Auth (`cortex-writer`).                                │
│      - Injects multi-tenant header: `X-Scope-OrgID: sandbox-metrics`.                                  │
│      - Proxies request to Cortex backend on `http://127.0.0.1:9009/api/v1/push`.                      │
│    • Cortex Distributor & Ingester:                                                                    │
│      - Validates timestamps and buffers samples in a 2-hour rolling WAL (`/var/lib/cortex/tsdb`).      │
│    • Cortex Compactor & Shipper Daemon:                                                                │
│      - Every 2 hours (or when idle), cuts sealed immutable TSDB blocks (`meta.json`, `index`, chunks).  │
│      - Ships the block directly to AWS S3 using instance IAM credentials.                              │
│      - Purges local disk chunks. Local working EBS stays completely flat (~8–16 GiB).                  │
└───────────────────────────────────────────────────┬────────────────────────────────────────────────────┘
                                                    │ AWS SigV4 HTTPS Multipart Upload
                                                    ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ 4. AWS S3 OBJECT STORAGE (s3://01-sandbox-cortex-storage/sandbox-metrics/)                             │
│    • Durable Long-Term Archive: 11 9s durability, KMS encryption at rest.                              │
│    • Transparent Querying: Central Grafana queries Cortex Store-Gateway, which streams historical       │
│      blocks directly from S3 without consuming PrimaryHub RAM or disk.                                 │
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### 2.1 Edge Spoke Scraper & Local WAL Buffer
* **Localized Scraping:** Edge nodes scrape in-cluster targets (pods, CoreDNS, node-exporter, cAdvisor) over the internal Pod CIDR (`10.244.0.0/16`) every 15 seconds. Scrapes finish in $< 2\text{ ms}$.
* **Crash-Safe Local WAL:** Scraped samples are appended immediately to a sequential write-ahead log on local disk (`/prometheus/wal`). If cross-region WAN connectivity is disrupted, edge scrapers continue recording without sample loss.
* **Batching & Compression:** A local WAL Watcher pools samples into batches of up to 1,000 samples (or 5s deadline), injects metadata (`cluster="spoke1"`, `region="eu-central-1"`), serializes to Protocol Buffers (`prompb.WriteRequest`), and compresses with Google Snappy, reducing bandwidth by **88.3%**.

---

### 2.2 PrimaryHub Dual Routing: Real-Time Ingest & Upstream Remote Write
When batches arrive at PrimaryHub (`10.99.0.1:30900`), Prometheus performs dual routing:
* **Path A (Real-Time Ingestion Buffer):** Decompresses and indexes samples into in-memory Head chunks. Retention is downsized to **6 hours** (`--storage.tsdb.retention.time=6h`) on local EBS, eliminating high-memory compaction spikes and preventing host disk fill-ups.
* **Path B (Upstream Streaming to Cortex):** PrimaryHub's `remote_write` engine batches all locally ingested samples and pushes them via HTTPS to Cortex on `https://10.99.0.50:8443/api/v1/push`.

---

### 2.3 Dedicated Cortex Node & NGINX RBAC Security Gate
Cortex runs on a dedicated, hardened EC2 instance (`i-00992e6b87f1461d8`) in `us-east-1`:
* **Zero Public IP:** The node resides strictly in a private VPC subnet (`10.20.154.244`).
* **Zero Port 22:** SSH is disabled. All operational access is conducted via AWS SSM Session Manager.
* **IMDSv2 Enforced:** `--http-put-response-hop-limit` is configured to `4` to permit containerized IAM credential discovery.
* **NGINX Security Gate (Port 8443):**
  * Terminates TLS 1.3.
  * Validates HTTP Basic Auth (`.htpasswd` with Bcrypt hashes).
  * Enforces RBAC: only `cortex-writer` can push to `/api/v1/push`; only `grafana-reader` can query `/prometheus/*`.
  * Injects `X-Scope-OrgID: sandbox-metrics` to enforce Cortex multi-tenant isolation.
  * Explicitly blocks sensitive internal endpoints (`/ring`, `/compactor`).

---

### 2.4 Cortex Compactor & Shipper Subsystem to AWS S3
Inside the Cortex pod (`cortex` in namespace `opensandbox-system`):
1. **Ingester:** Writes incoming metric samples into an active memory Head and logs them to `/var/lib/cortex/tsdb/wal`.
2. **Compactor:** Every 2 hours (or when idle), merges memory chunks and writes immutable TSDB blocks:
   * `meta.json`: Block metadata, min/max timestamps, series count.
   * `index`: Inverted index mapping metric labels to chunk offsets.
   * `chunks/000001`: Snappy-compressed metric samples.
3. **Shipper:** Uploads the sealed block directory directly into `s3://01-sandbox-cortex-storage/sandbox-metrics/<ULID>/`.
4. **Local Disk Truncation:** Once confirmed on S3, obsolete local blocks are deleted (`Deleting obsolete block`). The local working EBS volume stays bounded between 8 GiB and 16 GiB indefinitely.

---

### 2.5 Central Grafana Dual-Tier Datasource Integration
Central Grafana on PrimaryHub connects to two distinct datasources:
1. **`Prometheus` (Default Datasource):**
   * Target: `http://prometheus-server:80` (PrimaryHub local TSDB).
   * Scope: Real-time queries spanning the last 0 to 6 hours.
   * Performance: Ultra-low query latency ($< 15\text{ ms}$).
2. **`cortex-s3-longterm` (Long-Term Archive Datasource):**
   * Target: `https://10.99.0.50:8443/prometheus` (Cortex Store-Gateway).
   * Scope: Historical queries spanning 2 hours to multi-year archives.
   * Performance: Cortex scans block index headers in S3 and streams chunks directly, executing cross-month queries in $200\text{ ms} - 800\text{ ms}$.

---

## 3. Sequence Diagram: Edge Scrape to PrimaryHub, Cortex, and S3

```mermaid
sequenceDiagram
    autonumber
    participant Spoke as Edge Spoke Scraper (Spoke 1/2)
    participant HubProm as PrimaryHub Prometheus (Port 30900)
    participant Grafana as Central Grafana
    participant NginxProxy as Cortex NGINX Proxy (Port 8443)
    participant CortexEngine as Cortex Ingester & Compactor
    participant S3Bucket as AWS S3 (01-sandbox-cortex-storage)

    Note over Spoke,HubProm: 1. Edge Metrics Scrape & Ingress
    Spoke->>Spoke: Scrape in-cluster pods (CoreDNS, cAdvisor)
    Spoke->>Spoke: Commit to local disk WAL (6h buffer)
    Spoke->>HubProm: HTTP POST /api/v1/write (Basic Auth over WireGuard)
    HubProm->>HubProm: Authenticate via web.yml & commit to in-memory Head
    HubProm-->>Spoke: HTTP 204 No Content

    Note over HubProm,Grafana: 2. Real-Time Dashboard Queries
    Grafana->>HubProm: PromQL Query: rate(node_cpu_seconds_total[5m])
    HubProm-->>Grafana: Return immediate real-time metrics (< 15ms)

    Note over HubProm,CortexEngine: 3. Telemetry Stream to Cortex
    HubProm->>NginxProxy: HTTPS POST /api/v1/push (User: cortex-writer)
    NginxProxy->>NginxProxy: Authenticate Bcrypt & inject X-Scope-OrgID: sandbox-metrics
    NginxProxy->>CortexEngine: Forward HTTP to 127.0.0.1:9009
    CortexEngine->>CortexEngine: Ingest into active 2-hour WAL (/var/lib/cortex/tsdb)
    CortexEngine-->>HubProm: HTTP 200 OK

    Note over CortexEngine,S3Bucket: 4. TSDB Block Compaction & S3 Flush
    CortexEngine->>CortexEngine: Cut 2h immutable block (index, chunks, meta.json)
    CortexEngine->>S3Bucket: Shipper uploads block to S3: sandbox-metrics/<ULID>/
    CortexEngine->>CortexEngine: Truncate local memory & delete obsolete local block
    Note over S3Bucket: Metrics safely preserved in S3 with 11 9s durability!

    Note over Grafana,S3Bucket: 5. Historical Multi-Month / Multi-Year Queries
    Grafana->>NginxProxy: PromQL Query (Datasource: cortex-s3-longterm)
    NginxProxy->>CortexEngine: Query Store-Gateway
    CortexEngine->>S3Bucket: Scan & stream historical S3 block chunks
    CortexEngine-->>Grafana: Return multi-year trend data
```

---

## 4. Step-by-Step Manual Verification Runbook

### Step 1: Inspect TSDB Blocks in AWS S3
Verify that Cortex is continuously cutting and uploading 2-hour TSDB blocks to S3:
```bash
aws s3 ls s3://01-sandbox-cortex-storage/sandbox-metrics/ --recursive --human-readable
```
*Expected Output:*
```text
2.6 MiB   sandbox-metrics/01M6QKQK8115PZ1Q1CGPA3A7ZY/chunks/000001
871.8 KiB sandbox-metrics/01M6QKQK8115PZ1Q1CGPA3A7ZY/index
710 Bytes sandbox-metrics/01M6QKQK8115PZ1Q1CGPA3A7ZY/meta.json
439.4 KiB sandbox-metrics/01M6QKQKJEJ23G3KAFFYV8YEQ5/chunks/000001
866.5 KiB sandbox-metrics/01M6QKQKJEJ23G3KAFFYV8YEQ5/index
707 Bytes sandbox-metrics/01M6QKQKJEJ23G3KAFFYV8YEQ5/meta.json
```

---

### Step 2: Verify Active Ingest Across All Clusters on PrimaryHub
Verify that PrimaryHub Prometheus is receiving and indexing samples from all edge spoke clusters:
```bash
cmd_id=$(aws ssm send-command \
  --region us-east-1 \
  --instance-ids "i-0789b7609870c2216" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["export KUBECONFIG=/home/ec2-user/.kube/config; kubectl -n opensandbox-system exec deploy/prometheus -- wget -q -O - --header=\"Authorization: Basic cHJvbWV0aGV1cy1ydzpQcm9tZXRoZXVzUmVtb3RlV3JpdGVTZWN1cmUyMDI2IQ==\" \"http://localhost:9090/api/v1/query?query=count(up)%20by%20(cluster)\""]' \
  --query "Command.CommandId" --output text)

sleep 3
aws ssm get-command-invocation \
  --region us-east-1 \
  --command-id "$cmd_id" \
  --instance-id "i-0789b7609870c2216" \
  --query "StandardOutputContent" --output text
```
*Expected Output:*
```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      { "metric": {}, "value": [ 1793942134, "20" ] },
      { "metric": { "cluster": "secondaryhub" }, "value": [ 1793942134, "17" ] },
      { "metric": { "cluster": "spoke1" }, "value": [ 1793942134, "6" ] },
      { "metric": { "cluster": "spoke2" }, "value": [ 1793942134, "6" ] }
    ]
  }
}
```

---

### Step 3: Monitor Real-Time Streaming on Cortex NGINX Proxy
Confirm that PrimaryHub is continuously forwarding metrics into Cortex over HTTPS:
```bash
cmd_id=$(aws ssm send-command \
  --region us-east-1 \
  --instance-ids "i-00992e6b87f1461d8" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["export KUBECONFIG=/home/ec2-user/.kube/config; kubectl -n opensandbox-system logs deploy/cortex -c nginx-proxy --tail=10"]' \
  --query "Command.CommandId" --output text)

sleep 3
aws ssm get-command-invocation \
  --region us-east-1 \
  --command-id "$cmd_id" \
  --instance-id "i-00992e6b87f1461d8" \
  --query "StandardOutputContent" --output text
```
*Expected Output:* Incoming `POST /api/v1/push HTTP/2.0 200 0` requests authenticated as `cortex-writer` every few seconds:
```text
10.150.0.1 - cortex-writer [06/Nov/2026:05:13:26 +0000] "POST /api/v1/push HTTP/2.0" 200 0 "-" "Prometheus/2.54.1"
10.150.0.1 - cortex-writer [06/Nov/2026:05:13:30 +0000] "POST /api/v1/push HTTP/2.0" 200 0 "-" "Prometheus/2.54.1"
```

---

### Step 4: Verify Block Compaction & S3 Upload in Cortex Logs
Confirm that the Cortex ingester and compactor are sealing blocks and uploading them to S3:
```bash
cmd_id=$(aws ssm send-command \
  --region us-east-1 \
  --instance-ids "i-00992e6b87f1461d8" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["export KUBECONFIG=/home/ec2-user/.kube/config; kubectl -n opensandbox-system logs deploy/cortex -c cortex --tail=20"]' \
  --query "Command.CommandId" --output text)

sleep 3
aws ssm get-command-invocation \
  --region us-east-1 \
  --command-id "$cmd_id" \
  --instance-id "i-00992e6b87f1461d8" \
  --query "StandardOutputContent" --output text
```
*Expected Log Events:*
```text
caller=compact.go:580 level=info org_id=sandbox-metrics msg="write block" mint=1793872800000 maxt=1793880000000 ulid=01M6QKQK8115PZ1Q1CGPA3A7ZY duration=131.659101ms
caller=shipper.go:372 level=info org_id=sandbox-metrics msg="upload new block" id=01M6QKQK8115PZ1Q1CGPA3A7ZY
caller=db.go:1747 level=info org_id=sandbox-metrics msg="Deleting obsolete block" block=01M6N9B736Q5Q91VJGJEQPGRB6
```

---

### Step 5: Verify Active Credentials in AWS SSM Parameter Store
Ensure that the active credentials stored in SSM Parameter Store match the Dual-Auth configuration:
```bash
aws ssm get-parameter --name "/sandbox/monitoring/rw-username" --region us-east-1 --output json
aws ssm get-parameter --name "/sandbox/monitoring/rw-password" --with-decryption --region us-east-1 --output json
```

---

## 5. Configuration Blueprints

### 5.1 PrimaryHub Prometheus Remote Write Configuration
Configured inside PrimaryHub's Helm values to stream all metrics upstream to Cortex:

```yaml
remote_write:
  - url: "https://10.99.0.50:8443/api/v1/push"
    name: "cortex-s3-lts"
    remote_timeout: 30s
    tls_config:
      insecure_skip_verify: true
    basic_auth:
      username: "cortex-writer"
      password: "CORTEX_PROD_WRITER_SECRET"
    queue_config:
      capacity: 10000
      max_samples_per_send: 1000
      batch_send_deadline: 5s
      min_shards: 1
      max_shards: 10
```

---

### 5.2 Cortex NGINX Auth Proxy Configuration
Deployed as a sidecar container in `codeInspector/charts/cortex`:

```nginx
events { worker_connections 1024; }

http {
    ssl_protocols TLSv1.3;
    ssl_prefer_server_ciphers on;

    upstream cortex_backend {
        server 127.0.0.1:9009;
        keepalive 32;
    }

    server {
        listen 8443 ssl;
        server_name cortex.local 10.99.0.50;

        ssl_certificate     /etc/nginx/certs/cortex.crt;
        ssl_certificate_key /etc/nginx/certs/cortex.key;

        auth_basic "Cortex Gateway";
        auth_basic_user_file /etc/nginx/auth/.htpasswd;

        # RBAC: Write-Only Endpoint for Prometheus Upstream Push
        location = /api/v1/push {
            if ($remote_user != "cortex-writer") {
                return 403 "Forbidden: Cortex Ingestion requires cortex-writer.\n";
            }
            proxy_pass http://cortex_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Scope-OrgID "sandbox-metrics";
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            client_max_body_size 64M;
        }

        # RBAC: Read-Only Endpoint for Central Grafana Queries
        location /prometheus/ {
            if ($remote_user != "grafana-reader") {
                return 403 "Forbidden: Cortex Query requires grafana-reader.\n";
            }
            proxy_pass http://cortex_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Scope-OrgID "sandbox-metrics";
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_read_timeout 120s;
        }

        # Health Probe (Unauthenticated)
        location = /ready {
            auth_basic off;
            proxy_pass http://cortex_backend;
            proxy_http_version 1.1;
        }

        # Block Administrative Endpoints
        location ~ ^/(ring|compactor|all_user_stats) {
            deny all;
            return 403 "Forbidden.\n";
        }
    }
}
```

---

### 5.3 Cortex Monolithic Configuration (`cortex.yaml`)

```yaml
target: all
auth_enabled: true

server:
  http_listen_port: 9009
  http_listen_address: 0.0.0.0
  grpc_listen_port: 9095
  grpc_listen_address: 0.0.0.0
  log_level: info

# Sample validation, bounds, and rate limits (Distributor & Ingester limits)
limits:
  # Timestamp bounds
  reject_old_samples: true
  reject_old_samples_max_age: 168h
  creation_grace_period: 10m

  # Label limits
  max_label_names_per_series: 30
  max_label_name_length: 1024
  max_label_value_length: 2048

  # Ingestion rate and volume limits
  ingestion_rate: 25000
  ingestion_burst_size: 50000
  max_series_per_user: 5000000

distributor:
  ring:
    kvstore:
      store: memberlist

ingester:
  lifecycler:
    ring:
      kvstore:
        store: memberlist
      replication_factor: 1
    final_sleep: 0s

memberlist:
  bind_addr: ["127.0.0.1"]
  bind_port: 7946

blocks_storage:
  backend: s3
  s3:
    bucket_name: 01-sandbox-cortex-storage
    endpoint: s3.us-east-1.amazonaws.com
    region: us-east-1
    insecure: false
  tsdb:
    dir: /var/lib/cortex/tsdb
    block_ranges_period: [2h]
    retention_period: 6h
    ship_concurrency: 4

store_gateway:
  sharding_ring:
    replication_factor: 1
    kvstore:
      store: memberlist

compactor:
  data_dir: /var/lib/cortex/compactor
  compaction_interval: 10m
```

---

### 5.4 Grafana Datasource Configuration

```yaml
apiVersion: 1
datasources:
  # Real-Time Tier (0 to 6 Hours)
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://codeinspector-prometheus-server.opensandbox-system.svc.cluster.local:80
    isDefault: true
    jsonData:
      timeInterval: "15s"

  # Long-Term S3 Tier (2 Hours to Multi-Year)
  - name: cortex-s3-longterm
    type: prometheus
    access: proxy
    url: https://10.99.0.50:8443/prometheus
    isDefault: false
    basicAuth: true
    basicAuthUser: "grafana-reader"
    secureJsonData:
      basicAuthPassword: "GRAFANA_PROD_READER_SECRET"
    jsonData:
      tlsSkipVerify: true
      httpMethod: POST
      timeInterval: "1m"
```

---

## 6. Cortex Authentication Architecture

### 6.1 Why Cortex Needs an Auth Proxy

Cortex does **not** natively support TLS termination or HTTP Basic Auth. Its built-in authentication model is **multi-tenancy only** — it expects every incoming request to carry an HTTP header:

```
X-Scope-OrgID: <tenant-name>
```

If Cortex were exposed directly on the network, any client could:
* Inject arbitrary `X-Scope-OrgID` values to access or corrupt other tenants' data.
* Send writes to poison the metric store.
* Trigger destructive admin APIs (`/ring`, `/compactor`).

To solve this, we place a **hardened NGINX sidecar proxy** in front of Cortex. NGINX handles all transport security, identity verification, and route-level RBAC — Cortex only ever sees trusted, pre-authenticated loopback traffic from `127.0.0.1`.

---

### 6.2 The 5-Layer Security Model

```
                    INCOMING REQUEST (FROM PRIMARYHUB OR GRAFANA)
                                        │
                                        ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ LAYER 1: Transport & Network Perimeter                                      │
 │  • No Public IP on Cortex node — only reachable via WireGuard (10.99.0.0/24)│
 │  • Private VPC subnet: EC2 instance 10.20.154.244                           │
 │  • No SSH (port 22 closed) — admin via AWS SSM Session Manager only         │
 │  • TLS 1.2 / 1.3 only on port 8443 — plaintext HTTP is never accepted       │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │
                                        ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ LAYER 2: Identity Verification (HTTP Basic Auth via .htpasswd)              │
 │  • NGINX validates credentials against /etc/nginx/auth/.htpasswd            │
 │  • Passwords are stored as Bcrypt / Blowfish ($2b$) hashes — never plaintext│
 │  • Unknown or invalid credentials → HTTP 401 Unauthorized                  │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │
                                        ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ LAYER 3: Role-Based Authorization (Dynamic Regex RBAC + Automated Rotation)│
 │  • $remote_user is mapped dynamically via regex patterns:                   │
 │      "~^cortex-writer"  → $is_authorized = 1  (Write Role)                 │
 │      "~^grafana-reader" → $is_authorized = 2  (Read Role)                  │
 │      anything else      → $is_authorized = 0  (Denied)                     │
 │  • Fully automated zero-downtime secret rotation via cortex-secret-rotator  │
 │  • POST /api/v1/push  → requires $is_authorized == 1                       │
 │  • GET  /prometheus/* → requires $is_authorized == 2                       │
 │  • /ring, /compactor  → BLOCKED (HTTP 403) for all users                   │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │
                                        ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ LAYER 4: Tenant Header Injection (Automatic — Internal Loopback Only)      │
 │  • NGINX strips any client-supplied X-Scope-OrgID header                   │
 │  • NGINX injects: proxy_set_header X-Scope-OrgID "sandbox-metrics"         │
 │  • Cortex trusts only this injected value — tenant namespace is enforced    │
 │  • Forwards to Cortex backend on http://127.0.0.1:9009                     │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │
                                        ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ LAYER 5: S3 Cloud Storage Authorization (AWS IAM Role + IMDSv2)            │
 │  • Cortex uses EC2 IAM Instance Profile — no static AWS keys in config     │
 │  • IMDSv2 hop limit = 4 to traverse: Pod → KinD bridge → EC2 host          │
 │  • Uploads TSDB blocks to s3://01-sandbox-cortex-storage/ via SigV4 HTTPS  │
 │  • IAM policy grants only s3:GetObject, s3:PutObject, s3:ListBucket        │
 └─────────────────────────────────────────────────────────────────────────────┘
```

---

### 6.3 NGINX Dynamic RBAC Routing Logic

This is the exact configuration deployed via [`codeInspector/charts/cortex/templates/configmap.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/cortex/templates/configmap.yaml):

```nginx
http {
    # Step 1: Map the authenticated username dynamically via regex patterns
    map $remote_user $is_authorized {
        default              0;    # Unknown user → denied
        "~^cortex-writer"    1;    # Ingestion write-only role (accepts cortex-writer & rotated users)
        "~^grafana-reader"   2;    # Query read-only role (accepts grafana-reader & rotated users)
    }

    server {
        listen 8443 ssl;
        http2 on;

        ssl_certificate     /etc/nginx/certs/tls.crt;
        ssl_certificate_key /etc/nginx/certs/tls.key;
        ssl_protocols       TLSv1.2 TLSv1.3;

        # Step 2: Enforce HTTP Basic Auth for all requests (except /ready)
        auth_basic           "01-Sandbox Cortex Long-Term Storage Gate";
        auth_basic_user_file /etc/nginx/auth/.htpasswd;

        # Step 3: Inject multi-tenant header — Cortex trusts this exclusively
        proxy_set_header X-Scope-OrgID "sandbox-metrics";

        # Step 4a: WRITE GATEWAY — Only cortex-writer-* can push metrics
        location /api/v1/push {
            if ($is_authorized != 1) {
                return 403 "Forbidden: User is not authorized to write metrics\n";
            }
            proxy_pass http://127.0.0.1:9009/api/v1/push;
            proxy_http_version 1.1;
            proxy_buffering off;
        }

        # Step 4b: READ GATEWAY — Only grafana-reader-* can query metrics
        location /prometheus/ {
            if ($is_authorized != 2) {
                return 403 "Forbidden: User is not authorized to query metrics\n";
            }
            proxy_pass http://127.0.0.1:9009/prometheus/;
            proxy_http_version 1.1;
            proxy_read_timeout 120s;
        }

        # Step 4c: ADMIN LOCKDOWN — Blocked for everyone
        location ~* ^/(ring|compactor|all_user_stats) {
            deny all;
            return 403 "Forbidden: Administrative endpoints disabled\n";
        }

        # Step 4d: HEALTH PROBE — Unauthenticated (Kubernetes liveness/readiness)
        location /ready {
            auth_basic off;
            proxy_pass http://127.0.0.1:9009/ready;
        }
    }
}
```

**Key isolation properties:**
* **Zero-Downtime Dynamic Compatibility**: Using regex `"~^cortex-writer"` and `"~^grafana-reader"` allows rotated credentials (e.g., `cortex-writer-202611121005`) to be validated and authorized on-the-fly without modifying NGINX config.
* **Per-Request Evaluation**: NGINX reads `/etc/nginx/auth/.htpasswd` per incoming HTTP connection. When Kubernetes updates `secret/cortex-auth-secret`, NGINX picks up new credentials immediately without process restarts.
* A compromised `grafana-reader` credential **cannot inject fake metrics** (write endpoint returns 403).
* A compromised `cortex-writer` credential **cannot run expensive PromQL queries** that could degrade ingestion (read endpoint returns 403).
* No credential can access the internal admin ring or trigger compaction (admin block is unconditional).

---

### 6.4 Cortex-to-S3 Authentication (AWS IAM & IMDSv2)

Cortex uploads compacted 2-hour TSDB blocks to `s3://01-sandbox-cortex-storage/sandbox-metrics/` using **zero static AWS credentials**. Instead:

1. The EC2 instance `i-00992e6b87f1461d8` has an **IAM Instance Profile** attached (`cortex-s3-access-role`).
2. Cortex makes a standard AWS SDK call to the Instance Metadata Service (`http://169.254.169.254`) to obtain temporary credentials.
3. Because Cortex runs inside a Pod inside a KinD container, the credentials request traverses **3 network hops**: Pod → KinD bridge network → EC2 Host. IMDSv2 defaults to hop limit 1, which would drop the request at the first bridge.
4. We explicitly increased the IMDS hop limit to 4:
   ```bash
   aws ec2 modify-instance-metadata-options \
     --instance-id i-00992e6b87f1461d8 \
     --http-put-response-hop-limit 4 \
     --region us-east-1
   ```
5. All uploads are made over **AWS SigV4-signed HTTPS** via the VPC S3 Gateway endpoint — data never leaves the AWS network.

---

### 6.5 Authentication Sequence Diagram

```mermaid
sequenceDiagram
    autonumber
    actor Hub as PrimaryHub Prometheus
    actor Grafana as Central Grafana
    actor Attacker as Unauthorized Actor
    participant Nginx as Cortex NGINX Proxy (:8443)
    participant Cortex as Cortex Engine (:9009)
    participant S3 as AWS S3 Bucket

    Note over Hub,S3: Scenario A — Legitimate Metric Push (PrimaryHub → Cortex → S3)
    Hub->>Nginx: HTTPS POST /api/v1/push + Authorization: Basic cortex-writer:...
    Nginx->>Nginx: 1. TLS 1.3 handshake (verify cert)
    Nginx->>Nginx: 2. Validate bcrypt hash in .htpasswd
    Nginx->>Nginx: 3. Map $remote_user: cortex-writer → $is_authorized = 1
    Nginx->>Nginx: 4. Check /api/v1/push requires $is_authorized == 1 ✓
    Nginx->>Cortex: HTTP POST + X-Scope-OrgID: sandbox-metrics (loopback 127.0.0.1:9009)
    Cortex->>Cortex: Ingest into active 2-hour WAL
    Cortex-->>Nginx: 200 OK
    Nginx-->>Hub: 200 OK

    Note over Grafana,S3: Scenario B — Legitimate Historical Query (Grafana → Cortex → S3)
    Grafana->>Nginx: HTTPS GET /prometheus/api/v1/query + Authorization: Basic grafana-reader:...
    Nginx->>Nginx: Validate hash → $is_authorized = 2
    Nginx->>Nginx: Check /prometheus/ requires $is_authorized == 2 ✓
    Nginx->>Cortex: HTTP GET + X-Scope-OrgID: sandbox-metrics
    Cortex->>S3: Scan TSDB block index & stream chunk bytes
    S3-->>Cortex: Return compressed block data
    Cortex-->>Nginx: Return PromQL result vector
    Nginx-->>Grafana: 200 OK + metric data

    Note over Cortex,S3: Scenario C — Cortex S3 Upload via IAM (IMDSv2)
    Cortex->>Cortex: Compactor seals 2h TSDB block
    Cortex->>S3: AWS SigV4 HTTPS PUT sandbox-metrics/<ULID>/... (IAM Role creds via IMDSv2)
    S3-->>Cortex: 200 OK (block confirmed)
    Cortex->>Cortex: Delete obsolete local block (EBS stays flat)

    Note over Attacker,Nginx: Scenario D — Role Violation (grafana-reader tries to write)
    Attacker->>Nginx: HTTPS POST /api/v1/push + Authorization: Basic grafana-reader:...
    Nginx->>Nginx: Validate hash → $is_authorized = 2
    Nginx->>Nginx: Check /api/v1/push requires $is_authorized == 1, got 2 ✗
    Nginx-->>Attacker: 403 Forbidden: User is not authorized to write metrics

    Note over Attacker,Nginx: Scenario E — Admin API Probe
    Attacker->>Nginx: HTTPS GET /ring (any credentials)
    Nginx->>Nginx: Location ~* ^/(ring|compactor) → deny all
    Nginx-->>Attacker: 403 Forbidden: Administrative endpoints disabled

    Note over Attacker,Nginx: Scenario F — No Credentials
    Attacker->>Nginx: HTTPS GET /prometheus/api/v1/query (no Authorization header)
    Nginx->>Nginx: auth_basic check fails — no valid credential
    Nginx-->>Attacker: 401 Unauthorized

    Note over Hub,Nginx: Scenario G — Automated Zero-Downtime Secret Rotation
    Hub->>Nginx: PrimaryHub cortex-secret-rotator appends dynamic credentials to .htpasswd
    Nginx->>Nginx: NGINX evaluates .htpasswd per-request — both old and new users valid immediately
    Hub->>Hub: Prometheus reloads /etc/prometheus/prometheus.yml via SIGHUP (24.4ms)
    Hub->>Nginx: Prometheus pushes batches using new dynamic user (cortex-writer-YYYYMMDDHHMM)
    Nginx-->>Hub: 200 OK (Continuous ingestion, zero dropped metrics)
    Hub->>Nginx: After 600s Grace Period, rotator prunes expired credentials
```

---

### 6.6 Automated Zero-Downtime Secret Rotation (`cortex-secret-rotator`)

To eliminate static credentials in production, a dedicated Kubernetes CronJob [`cortex-secret-rotator`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/cortex-rotator-cronjob.yaml) runs autonomously on PrimaryHub on a monthly cadence (`0 2 1 * *`).

#### The 4-Phase Rotation Lifecycle:
1. **Phase 1: Dual-Auth Overlap on Cortex Node**
   - The rotator connects to the Cortex Kubernetes API server (`https://10.99.0.50:6443`) over WireGuard using scoped ServiceAccount token `cortex-auth-rotator`.
   - Generates unique timestamped credentials: `cortex-writer-YYYYMMDDHHMM` and `grafana-reader-YYYYMMDDHHMM` with 24-character cryptographically secure passwords.
   - Computes Bcrypt Blowfish ($2b$) hashes and appends them to `/etc/nginx/auth/.htpasswd` inside `secret/cortex-auth-secret` on the Cortex cluster.
   - **Zero NGINX Reload**: Because NGINX checks `.htpasswd` per request and matches regex `~^cortex-writer` and `~^grafana-reader`, **both old and new credentials become valid immediately** without requiring container restart.

2. **Phase 2: Client Distribution & Millisecond SIGHUP Reload**
   - Rotator patches `cm/prometheus-config` on PrimaryHub with new writer credentials.
   - Rotator patches `cm/grafana-datasources` on PrimaryHub with new reader credentials.
   - Sends `kill -HUP 1` to PrimaryHub Prometheus: Prometheus reloads its `remote_write` storage configuration in **24.4 milliseconds** (remote storage reloads in 508 µs) without terminating pods or dropping WAL queues.
   - Prometheus immediately begins sending metrics signed with the new credentials.
   - Optionally synchronizes new credentials to AWS SSM Parameter Store (`/sandbox/cortex/*`) for disaster recovery and standby SecondaryHub replication.

3. **Phase 3: Transition & Overlap Grace Period**
   - Rotator enters a **600-second Grace Period**.
   - During this window, all inflight Prometheus metric batches and active Grafana dashboard sessions clear smoothly.

4. **Phase 4: Pruning Deprecated Rotated Credentials**
   - Rotator reconnects to the Cortex cluster API.
   - Prunes older rotating users from `.htpasswd`, retaining only the active rotated users and baseline system users.
   - Commits the pruned `.htpasswd` to `secret/cortex-auth-secret`.

#### Live Production Performance Metrics
* **Prometheus Reload Latency**: `24.405909ms` total duration (`remote_storage=508.657µs`).
* **Ingestion Continuity**: Zero 401 Unauthorized errors, zero sample drops during cutover.
* **Cortex Ingestion Verification**: Incoming HTTP/2 `POST /api/v1/push` authenticated with rotated user every 2–3 seconds.

---

### 6.7 Hands-On Verification Commands

Run these from your local terminal or via AWS SSM against PrimaryHub (`i-0bcd35beefd16e3e3`), which has WireGuard connectivity to the Cortex node (`i-00345c93f722ba2f6`).

> **Note:** Active rotated credentials can be inspected directly on Cortex or via AWS SSM:
> ```bash
> # Fetch active Cortex credentials from SSM Parameter Store:
> aws ssm get-parameter --name "/sandbox/cortex/writer-username" --region us-east-1 --output text --query "Parameter.Value"
> aws ssm get-parameter --name "/sandbox/cortex/writer-password" --with-decryption --region us-east-1 --output text --query "Parameter.Value"
> ```

---

#### Test A — Valid Write (`cortex-writer` → `/api/v1/push`)
```bash
cmd_id=$(aws ssm send-command \
  --region us-east-1 \
  --instance-ids "i-0789b7609870c2216" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["curl -k -s -o /dev/null -w \"HTTP Status: %{http_code}\n\" -u cortex-writer:PrometheusRemoteWriteSecure2026! -X POST https://10.99.0.50:8443/api/v1/push"]' \
  --query "Command.CommandId" --output text)
sleep 3
aws ssm get-command-invocation --region us-east-1 --command-id "$cmd_id" --instance-id "i-0789b7609870c2216" --query "StandardOutputContent" --output text
```
**Expected:** `HTTP Status: 204` (empty push accepted) or `200`

---

#### Test B — Role Violation (`grafana-reader` trying to push)
```bash
cmd_id=$(aws ssm send-command \
  --region us-east-1 \
  --instance-ids "i-0789b7609870c2216" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["curl -k -s -u grafana-reader:PrometheusRemoteWriteSecure2026! -X POST https://10.99.0.50:8443/api/v1/push"]' \
  --query "Command.CommandId" --output text)
sleep 3
aws ssm get-command-invocation --region us-east-1 --command-id "$cmd_id" --instance-id "i-0789b7609870c2216" --query "StandardOutputContent" --output text
```
**Expected:** `Forbidden: User is not authorized to write metrics`

---

#### Test C — Blocked Admin Endpoint (`/ring`)
```bash
cmd_id=$(aws ssm send-command \
  --region us-east-1 \
  --instance-ids "i-0789b7609870c2216" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["curl -k -s -u cortex-writer:PrometheusRemoteWriteSecure2026! https://10.99.0.50:8443/ring"]' \
  --query "Command.CommandId" --output text)
sleep 3
aws ssm get-command-invocation --region us-east-1 --command-id "$cmd_id" --instance-id "i-0789b7609870c2216" --query "StandardOutputContent" --output text
```
**Expected:** `Forbidden: Administrative endpoints disabled`

---

#### Test D — Unauthenticated Access
```bash
cmd_id=$(aws ssm send-command \
  --region us-east-1 \
  --instance-ids "i-0789b7609870c2216" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["curl -k -s -o /dev/null -w \"HTTP Status: %{http_code}\n\" https://10.99.0.50:8443/prometheus/api/v1/query"]' \
  --query "Command.CommandId" --output text)
sleep 3
aws ssm get-command-invocation --region us-east-1 --command-id "$cmd_id" --instance-id "i-0789b7609870c2216" --query "StandardOutputContent" --output text
```
**Expected:** `HTTP Status: 401`

---

#### Test E — Verify Health Probe is Unauthenticated (Kubernetes Liveness)
```bash
cmd_id=$(aws ssm send-command \
  --region us-east-1 \
  --instance-ids "i-0789b7609870c2216" \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["curl -k -s https://10.99.0.50:8443/ready"]' \
  --query "Command.CommandId" --output text)
sleep 3
aws ssm get-command-invocation --region us-east-1 --command-id "$cmd_id" --instance-id "i-0789b7609870c2216" --query "StandardOutputContent" --output text
```
**Expected:** `ready` (200 OK, no credentials required)

---

## 7. Operational Runbook & Troubleshooting

| Symptom | Root Cause | Diagnosis Command | Resolution |
| :--- | :--- | :--- | :--- |
| **PrimaryHub returns 401 on scrape query** | Basic Auth credentials omitted from query command | Check `/etc/prometheus-web/web.yml` in secret | Pass `--header "Authorization: Basic <base64>"` using active SSM credentials |
| **Cortex NGINX returns 403 Forbidden on push** | Username is not `cortex-writer` or password mismatch | Check NGINX access log: `logs deploy/cortex -c nginx-proxy` | Verify Basic Auth username is `cortex-writer` with correct password |
| **S3 block uploads fail with 403 Access Denied** | EC2 IMDS hop limit set to 2 or 1 (KinD needs 4) | Check Cortex pod logs: `logs deploy/cortex -c cortex` | Run: `aws ec2 modify-instance-metadata-options --instance-id i-00992e6b87f1461d8 --http-put-response-hop-limit 4` |
| **Edge Spoke fails to push metrics** | WireGuard interface down or wrong credentials | On Spoke: `wg show`, `kubectl -n monitoring logs deploy/prometheus-scraper` | Verify WireGuard peer handshake and sync credentials from SSM |
| **Local disk fills up on Cortex node** | Shipper daemon failed to upload blocks to S3 | Run `df -h /var/lib/cortex` on Cortex instance | Inspect S3 IAM permissions; ensure shipper can delete uploaded local blocks |

---

## 8. Architectural Assessment: Cortex Advertised Features vs. 01-Sandbox Implementation

This section evaluates the current **single-node, monolithic** (`target: all`) Cortex deployment in 01-Sandbox against Cortex's core advertised features.

### 8.1 Feature-by-Feature Alignment Matrix

| Advertised Feature | 01-Sandbox Status | Technical Justification |
|:---|:---:|:---|
| **Multi-Tenant** | ✅ **Fully Valid** | `auth_enabled: true` enforced at the core engine. NGINX injects `X-Scope-OrgID: sandbox-metrics` on every TLS request. S3 storage is physically isolated under the `sandbox-metrics/` prefix. Queries and writes are strictly scoped to the tenant namespace. |
| **Long-Term Storage (S3)** | ✅ **Fully Valid** | `blocks_storage.backend: s3` streams to `01-sandbox-cortex-storage`. 2-hour WAL compactions cut immutable TSDB blocks (`meta.json`, `index`, `chunks/000001`) shipped to S3 with 11-nines durability. Local working EBS stays flat. |
| **Horizontally Scalable** | ⚠️ **Architecture-Ready** | Currently deployed with `replicaCount: 1`, `replication_factor: 1`, and `memberlist: bind_addr: ["127.0.0.1"]` (single-binary monolithic mode). Cortex officially recommends single-binary mode for smaller workloads. Horizontal scaling is an infrastructure scale-out trigger (splitting into distributor/ingester/querier microservices), requiring no architectural redesign. |
| **Highly Available** | ⚠️ **S3-Layer Durable** | S3-persisted historical blocks inherit AWS 11-nines availability and durability. The live in-flight buffer (2-hour rolling WAL) operates at `replication_factor: 1` on EBS storage. Pod crashes are recovered automatically via Kubernetes `Recreate` without data loss (WAL is on PVC), but a catastrophic EC2 hardware failure would risk uncommitted WAL segments. |

---

### 8.2 Why Monolithic Mode (`target: all`) is the Right Choice for 01-Sandbox

Cortex officially documents two deployment architectures:
1. **Microservices Mode:** Running separate deployments for distributors, ingesters, queriers, query-frontends, store-gateways, rulers, and compactors (requires Consul/etcd, multi-node gossip, and complex ingress routing).
2. **Single Binary Mode (`target: all`):** Running all components within a unified process while maintaining identical storage formats and tenant isolation.

For the 01-Sandbox scale:
* **Active Series:** ~15,000–25,000 active series across 5 nodes (PrimaryHub, SecondaryHub, Spoke 1, Spoke 2, Cortex).
* **Ingestion Rate:** ~2,500 samples/sec (well within a single core's processing capacity).
* **Memory Footprint:** ~1.2 GiB RAM on a single node (compared to > 6–8 GiB needed to run 6+ microservice replicas with quorum overhead).
* **Operational Simplicity:** Single ConfigMap, single Deployment, and no distributed consensus operational failure modes.

---

### 8.3 Path to Production Horizontal Scalability & Full HA

When metric volume scales beyond a single EC2 node (e.g. > 500,000 active series), the architecture scales out by tuning the following configuration knobs in Helm:

```yaml
# Evolution from Monolithic to Distributed HA
cortex:
  target: distributor,ingester,store-gateway  # Split into independent deployments
  replicaCount: 3                             # 3 ingester pods across AZs
  ingester:
    lifecycler:
      ring:
        replication_factor: 3                 # Quorum-based sample replication (W=2, R=2)
  memberlist:
    bind_addr: ["0.0.0.0"]                    # Open gossip across pod IP network
```
