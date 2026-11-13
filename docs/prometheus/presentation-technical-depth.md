# Multi-Region Observability Technical Deep-Dive & Architecture Specification

**Comprehensive Companion Manual to the Observability Presentation Deck**
**Document:** Technical Deep-Dive, Configuration Blueprints, Security Standards & Operational Runbooks
**File Reference:** [presentation-technical-depth.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/presentation-technical-depth.md)
**Slide Deck:** [presentation-slides.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/presentation-slides.md)
**Source Implementations:**
* Push Sequence & Routing: [prometheus-push-based-sequence.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/prometheus-push-based-sequence.md)
* Zero-Downtime Secret Rotation: [kubernetes-secret-rotation-implementation.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/kubernetes-secret-rotation-implementation.md)
* Cortex & S3 Long-Term Storage: [long-term-strorage-cortex-s3-implementation.md](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/long-term-strorage-cortex-s3-implementation.md)

---

## Table of Contents

1. [Architectural Overview & Global Topology](#1-architectural-overview--global-topology)
2. [Pillar 1: Push-Based Prometheus Remote Write Protocol](#2-pillar-1-push-based-prometheus-remote-write-protocol)
   * [2.1 The WAN Pull Failure Paradigm](#21-the-wan-pull-failure-paradigm)
   * [2.2 In-Cluster Scrape Engine & Crash-Safe Local WAL](#22-in-cluster-scrape-engine--crash-safe-local-wal)
   * [2.3 WAL Watcher, Batching & Google Snappy Compression](#23-wal-watcher-batching--google-snappy-compression)
   * [2.4 WireGuard Mesh Transit & Netfilter Kernel DNAT Ingress](#24-wireguard-mesh-transit--netfilter-kernel-dnat-ingress)
   * [2.5 PrimaryHub Remote Write Receiver Pipeline](#25-primaryhub-remote-write-receiver-pipeline)
3. [Pillar 2: Kubernetes-Native Zero-Downtime Secret Rotation](#3-pillar-2-kubernetes-native-zero-downtime-secret-rotation)
   * [2.1 Cryptographic & Security Rationale](#31-cryptographic--security-rationale)
   * [3.2 The Dual-Auth Overlap Pattern](#32-the-dual-auth-overlap-pattern)
   * [3.3 Rotator Lifecycle: 4-Phase Execution Engine](#33-rotator-lifecycle-4-phase-execution-engine)
   * [3.4 Edge Fleet Distribution via Open Cluster Management (OCM)](#34-edge-fleet-distribution-via-open-cluster-management-ocm)
   * [3.5 Greenfield Node Bootstrap via AWS SSM Parameter Store](#35-greenfield-node-bootstrap-via-aws-ssm-parameter-store)
4. [Pillar 3: Decoupled Long-Term Storage with Cortex & AWS S3](#4-pillar-3-decoupled-long-term-storage-with-cortex--aws-s3)
   * [4.1 Why EBS gp3 Fails at Scale: The Mathematical & Operational Bottleneck](#41-why-ebs-gp3-fails-at-scale-the-mathematical--operational-bottleneck)
   * [4.2 Dedicated Hardened EC2 Architecture & Network Topology](#42-dedicated-hardened-ec2-architecture--network-topology)
   * [4.3 NGINX Security & RBAC Reverse Proxy Gate](#43-nginx-security--rbac-reverse-proxy-gate)
   * [4.4 Cortex Engine Architecture (Ingester, Compactor, Shipper, Store-Gateway)](#44-cortex-engine-architecture-ingester-compactor-shipper-store-gateway)
   * [4.5 AWS S3 Bucket Configuration, Encryption & Lifecycle Tiering](#45-aws-s3-bucket-configuration-encryption--lifecycle-tiering)
   * [4.6 PrimaryHub Upstream Remote Write & Downsizing](#46-primaryhub-upstream-remote-write--downsizing)
5. [Unified Fleet Observability & Grafana Integration](#5-unified-fleet-observability--grafana-integration)
   * [5.1 Dual-Tier Datasource Topology (Real-Time vs Historical)](#51-dual-tier-datasource-topology-real-time-vs-historical)
   * [5.2 Dynamic Label Indexing & Dashboard Variable Templating](#52-dynamic-label-indexing--dashboard-variable-templating)
6. [Operational Runbooks & Incident Response](#6-operational-runbooks--incident-response)
   * [6.1 Validating Metric Ingestion Across All Tiers](#61-validating-metric-ingestion-across-all-tiers)
   * [6.2 Triggering On-Demand Manual Secret Rotation](#62-triggering-on-demand-manual-secret-rotation)
   * [6.3 Verifying Cortex S3 Block Creation & Retention](#63-verifying-cortex-s3-block-creation--retention)
   * [6.4 Failure Modes & Recovery Strategies](#64-failure-modes--recovery-strategies)

---

## 1. Architectural Overview & Global Topology

The 01-Sandbox infrastructure spans four AWS regions, supporting microVM orchestration, distributed databases, key-value caches, and edge application workloads. The telemetry plane must provide unbroken observability without adding latency or consuming memory on mission-critical hosts.

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                       01-SANDBOX GLOBAL TOPOLOGY                                       │
│                                                                                                        │
│   PRIMARYHUB (us-east-1)                 SECONDARYHUB (eu-west-1)             CORTEX NODE (us-east-1)  │
│   - Private IP: 10.20.133.217            - Private IP: 10.22.133.199          - Private IP: 10.20.149.67│
│   - WireGuard: 10.99.0.1                 - WireGuard: 10.99.0.2               - WireGuard: 10.99.0.50  │
│   - KinD Ingress: :30900                 - Role: Backup Hub / Scraper         - Role: S3 LTS Ingestion │
│                                                                                                        │
│   SPOKE 1 (eu-central-1)                 SPOKE 2 (ap-south-1)                 SPOKE 3 (ap-south-1)     │
│   - Private IP: 10.23.134.105            - Private IP: 10.21.138.131          - Private IP: 10.21.155.131│
│   - WireGuard: 10.99.0.3                 - WireGuard: 10.99.0.4               - WireGuard: 10.99.0.6   │
│   - Role: Frankfurt Edge Scraper         - Role: Mumbai Edge Scraper          - Role: Mumbai Edge Scraper│
└────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

All inter-cluster communication flows across a point-to-point **WireGuard VPN mesh (`10.99.0.0/24`)** configured with ChaCha20-Poly1305 AEAD symmetric encryption and Curve25519 key negotiation. Public IPv4 routing is completely bypassed for metric ingestion and cluster management.

---

## 2. Pillar 1: Push-Based Prometheus Remote Write Protocol

### 2.1 The WAN Pull Failure Paradigm

In a standard Prometheus architecture, a centralized server pulls (scrapes) targets by polling their HTTP `/metrics` endpoints. In cross-region multi-cloud deployments, this pattern exhibits four critical failure modes:

1. **Firewall Boundary Violations:** Edge spoke clusters reside behind regional NAT gateways and corporate/cloud firewalls. Scraping them directly requires either opening public ingress ports or maintaining fragile reverse SSH tunnels.
2. **WAN Latency Cascades & Timeouts:** Polling targets in Mumbai (`ap-south-1`) from Virginia (`us-east-1`) incurs a round-trip time (RTT) of $180\text{ ms} - 260\text{ ms}$. Network jitter frequently exceeds Prometheus' default $10\text{s}$ scrape timeout, creating synthetic target down states.
3. **Catastrophic Packet Loss (Zero Buffer):** If a trans-oceanic fiber link drops for 60 seconds, a central pull architecture loses all metrics generated during that window. Traditional pull scrapers have zero store-and-forward capability.
4. **Host Saturation on the Central Hub:** Managing thousands of simultaneous cross-continental HTTP persistent connections forces the central hub to maintain large socket pools, driving socket exhaustion and high kernel context switching.

---

### 2.2 In-Cluster Scrape Engine & Crash-Safe Local WAL

To eliminate these vulnerabilities, every edge spoke cluster runs a localized, lightweight **Prometheus Agent** (or Prometheus server in Agent mode).

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ EDGE SPOKE PROMETHEUS AGENT                                                            │
│                                                                                        │
│   [Target: CoreDNS] ──┐                                                                │
│   [Target: cAdvisor] ─┼──► Local Scrape Loop (15s Interval)                            │
│   [Target: OCM Agent] ┘         │                                                      │
│                                 ▼                                                      │
│                       ┌───────────────────┐                                            │
│                       │ Active Head Chunk │ (In-Memory Buffer)                         │
│                       └─────────┬─────────┘                                            │
│                                 │ Synchronous Direct I/O Append                        │
│                                 ▼                                                      │
│                       ┌───────────────────┐                                            │
│                       │ Local Disk WAL    │ (/prometheus/wal, 128 MB Segments)         │
│                       └───────────────────┘ (emptyDir / Fast NVMe)                     │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

#### Scraping Mechanics
* **Discovery:** Local targets are discovered dynamically via the Kubernetes API server using the `kubernetes_sd_configs` directive (`role: pod`, `role: endpoints`, `role: node`).
* **Scrape Latency:** Scrapes occur over the local Linux network loopback or in-cluster Pod CIDR (`10.244.0.0/16`), completing in **$< 1.5\text{ ms}$**.
* **Crash Safety:** Before any metric sample is exposed to the network, Prometheus appends it directly to an uncompressed, sequential write-ahead log segment (`/prometheus/wal/0000000X`).
* **Storage Footprint:** The edge WAL maintains a strict **6-hour rolling retention** window (`--storage.tsdb.retention.time=6h`). Local disk usage is capped at $< 500\text{ MB}$, allowing it to run entirely in Kubernetes `emptyDir` memory or ephemeral storage volumes.

---

### 2.3 WAL Watcher, Batching & Google Snappy Compression

Instead of forwarding metrics immediately upon scrape (which would create an inefficient flood of tiny TCP packets), Prometheus employs an asynchronous **WAL Watcher** subsystem:

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ WAL WATCHER SUBSYSTEM                                                            │
│                                                                                  │
│   ┌────────────────┐      Read Stream      ┌──────────────────────────────────┐  │
│   │ Local Disk WAL │ ────────────────────► │ WAL Watcher Worker Threads       │  │
│   └────────────────┘                       └────────────────┬─────────────────┘  │
│                                                             │                    │
│   Batching Parameters:                                      ▼                    │
│   - max_samples_per_send: 1000             ┌──────────────────────────────────┐  │
│   - batch_send_deadline: 5s                │ Batch Aggregator & Relabeler     │  │
│   - min_shards: 1 | max_shards: 10         │ - Injects: cluster="spoke1"      │  │
│                                            │ - Injects: region="eu-central-1" │  │
│                                            └────────────────┬─────────────────┘  │
│                                                             │                    │
│                                                             ▼                    │
│   Wire Serialization:                      ┌──────────────────────────────────┐  │
│   - Protocol Buffers: prompb.WriteRequest  │ Snappy Framing & Compression     │  │
│   - Raw Size: ~1,024 Bytes / Sample        │ - Compressed: ~120 Bytes/Sample  │  │
│   - Compression Ratio: 88.3%               └────────────────┬─────────────────┘  │
│                                                             │                    │
│                                                             ▼                    │
│                                            HTTP POST Payload to PrimaryHub       │
└──────────────────────────────────────────────────────────────────────────────────┘
```

#### Protocol Buffers Serialization Schema
Samples are formatted according to the official Prometheus Remote Write specification (`prompb.WriteRequest`):

```protobuf
syntax = "proto3";
package prometheus;

message WriteRequest {
  repeated TimeSeries timeseries = 1;
  repeated MetricMetadata metadata = 2;
}

message TimeSeries {
  repeated Label labels = 1;
  repeated Sample samples = 2;
  repeated Exemplar exemplars = 3;
}

message Label {
  string name = 1;
  string value = 2;
}

message Sample {
  double value = 1;
  int64 timestamp = 2;
}
```

#### Mathematical Bandwidth Reduction
Let $N$ denote the number of samples in a batch ($N = 1000$). Each raw uncompressed sample with its associated label set occupies approximately $S_{\text{raw}} \approx 1024\text{ bytes}$ on the wire:

$$\text{Payload}_{\text{raw}} = N \times S_{\text{raw}} \approx 1000 \times 1024\text{ B} = 1.024\text{ MB}$$

Google Snappy utilizes a byte-oriented LZ77 derivative that exploits the heavy repetition of label names (`__name__`, `job`, `instance`, `namespace`) across successive time series. The resulting compressed size $S_{\text{comp}}$ averages $120\text{ bytes}$ per sample:

$$\text{Payload}_{\text{snappy}} = N \times S_{\text{comp}} \approx 1000 \times 120\text{ B} = 120\text{ KB}$$

$$\text{Bandwidth Reduction} = \left( 1 - \frac{120\text{ KB}}{1024\text{ KB}} \right) \times 100\% = 88.28\%$$

This massive reduction ensures that even across high-cost cross-continental AWS egress connections, telemetry bandwidth remains negligible.

---

### 2.4 WireGuard Mesh Transit & Netfilter Kernel DNAT Ingress

The compressed protobuf payload is transmitted via HTTP POST:

```http
POST /api/v1/write HTTP/1.1
Host: 10.99.0.1:30900
User-Agent: Prometheus/2.51.0
Content-Type: application/x-protobuf
Content-Encoding: snappy
X-Prometheus-Remote-Write-Version: 0.1.0
Authorization: Basic cHJvbS1ydy0yMDI2MTEwMzA5MDg6Wmt4M...
```

#### Kernel Ingress Path on PrimaryHub
1. **WireGuard Decapsulation:** The packet hits physical interface `eth0`, is identified as UDP port `51820`, and is decrypted by the WireGuard Linux kernel module. The decrypted packet emerges on the virtual `wg0` interface with source IP `10.99.0.3` (Spoke 1) and destination IP `10.99.0.1:30900`.
2. **Netfilter PREROUTING Chain (DNAT):** Because PrimaryHub runs inside a Kubernetes-in-Docker (KinD) container, an iptables DNAT rule routes port `30900` from the host network into the KinD control-plane container:
   ```bash
   iptables -t nat -A PREROUTING -p tcp -d 10.99.0.1 --dport 30900 \
     -j DNAT --to-destination 172.18.0.2:30900
   ```
3. **Kube-Proxy NodePort Routing:** The KinD node's internal `kube-proxy` forwards the packet to the active `prometheus-server` pod (`10.244.0.5:9090`).

---

### 2.5 PrimaryHub Remote Write Receiver Pipeline

PrimaryHub Prometheus operates with the `--web.enable-remote-write-receiver` flag enabled.

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ PRIMARYHUB PROMETHEUS RECEIVER                                                         │
│                                                                                        │
│   HTTP POST /api/v1/write ──► TLS & Basic Auth Gate (web.yml)                          │
│                                       │                                                │
│                                       ├─► [Failure: 401 Unauthorized]                  │
│                                       ▼                                                │
│                              Snappy Decompression                                      │
│                                       │                                                │
│                                       ▼                                                │
│                              Protobuf Deserialization                                  │
│                                       │                                                │
│                                       ▼                                                │
│                        In-Memory Head Chunks Ingestion                                 │
│                                       │                                                │
│                                       ├─► Return HTTP 204 No Content                   │
│                                       ▼                                                │
│                          Local 6h Storage Buffer (EBS)                                 │
│                                       │                                                │
│                                       ▼                                                │
│                        Forward to Dedicated Cortex (S3)                                │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

1. **Authentication:** The inbound request is intercepted by the Prometheus web security engine. The `Authorization: Basic` header is decoded and compared against Bcrypt hashes in `/etc/prometheus-web/web.yml`.
2. **Ingestion Commit:** If authenticated, the payload is decompressed, deserialized, and appended to PrimaryHub's in-memory head chunks.
3. **Response:** Prometheus immediately returns **`HTTP 204 No Content`** to the edge scraper, closing the batch lifecycle in under **$4\text{ ms}$**.

---

## 3. Pillar 2: Kubernetes-Native Zero-Downtime Secret Rotation

### 3.1 Cryptographic & Security Rationale

Static credentials represent an acute threat vector in distributed edge architectures:
* If a single edge spoke is compromised, an attacker gaining access to the remote-write credentials could forge arbitrary metric streams, flood the central TSDB, or inject false health status.
* Compliance frameworks (SOC2 Type II, ISO/IEC 27001 Control A.9.4.3, NIST SP 800-53 IA-5) require periodic automated credential rotation.
* Traditional rotation schemes require a service restart or maintenance window, causing telemetry blind spots across the global fleet.

---

### 3.2 The Dual-Auth Overlap Pattern

Prometheus natively supports multiple concurrent credentials via its `--web.config.file=/etc/prometheus-web/web.yml` configuration:

```yaml
# /etc/prometheus-web/web.yml
basic_auth_users:
  prometheus-rw: "$2b$12$e0L57c91n9U8c8qjX4.EceMvO198v0P8PzE680vR8iN5F01.j03yK"   # User A (Active / Retiring)
  prom-rw-202611030908: "$2b$12$9F00Z8J...c8qjX4EceMvO198v0P8PzE680vR8iN5F01.j03yK" # User B (New / Deploying)
```

By leveraging this multi-user structure, PrimaryHub can simultaneously authenticate **both the outgoing credential and the incoming credential**. This creates an asynchronous migration window where edge nodes can be updated independently without coordinating millisecond-level lockstep cutovers.

---

### 3.3 Rotator Lifecycle: 4-Phase Execution Engine

The rotation process is driven by an automated CronJob running in PrimaryHub's `opensandbox-system` namespace.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                        SECRET ROTATION 4-PHASE LIFECYCLE                               │
│                                                                                        │
│  Phase 1: Dual-Auth Generation & Activation                                            │
│  - Generate new credential: prom-rw-YYYYMMDDHHMM + 24-char high-entropy secret         │
│  - Compute Blowfish/Bcrypt ($2b$) hash                                                 │
│  - Patch secret/prometheus-web-config: [User_A + User_B]                               │
│  - SIGHUP reload PrimaryHub Prometheus (< 15ms latency, 0 dropped TCP connections)     │
│                                   │                                                    │
│                                   ▼                                                    │
│  Phase 2: Fleet-Wide Spoke Distribution (OCM ManifestWork)                             │
│  - Distribute Secret with User_B to spoke namespaces: spoke1, spoke2, spoke3           │
│  - Edge klusterlet-work-agent syncs Secret to monitoring namespace                     │
│  - Edge prometheus-config-reloader reloads edge scraper via POST /-/reload             │
│  - Edge scraper seamlessly switches to User_B (Zero 401 errors)                        │
│                                   │                                                    │
│                                   ▼                                                    │
│  Phase 3: Greenfield Dynamic Node Sync (AWS SSM Parameter Store)                       │
│  - Rotator updates SSM parameters in us-east-1:                                        │
│      /sandbox/monitoring/rw-username                                                   │
│      /sandbox/monitoring/rw-password                                                   │
│  - Any newly launched EC2 instance retrieves User_B during cloud-init                  │
│                                   │                                                    │
│                                   ▼                                                    │
│  Phase 4: Graceful Legacy Revocation (Grace Period: 600 Seconds)                       │
│  - Rotator sleeps 600s to ensure global OCM delivery & edge propagation                │
│  - Prunes User_A from web.yml; commits [User_B only]                                   │
│  - Final SIGHUP reload on PrimaryHub Prometheus                                        │
│  - User_A permanently revoked; 100% telemetry continuity verified                      │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

#### Cryptographic Hash Generation (Zero-Dependency Python)
To avoid introducing heavyweight external dependencies or third-party binaries into the rotator container image, the Bcrypt hash is computed using Python's standard library `crypt` module:

```python
import crypt, secrets, string

# 1. Generate 24-character cryptographic password
alphabet = string.ascii_letters + string.digits
password = ''.join(secrets.choice(alphabet) for _ in range(24))

# 2. Generate Bcrypt ($2b$) hash with work factor 12
salt = crypt.mksalt(crypt.METHOD_BLOWFISH)
bcrypt_hash = crypt.crypt(password, salt)
```

#### Hot-Reload Performance
Prometheus supports hot configuration reloads via HTTP `POST /-/reload` or by delivering a `SIGHUP` signal to process ID 1 (`kill -HUP 1`). The web server replaces its internal authentication map in memory:
* **Reload Duration:** **$7.8\text{ ms} - 14.2\text{ ms}$**
* **Active Connections:** Preserved without termination.
* **HTTP Error Responses:** Exactly **$0$** across the reload boundary.

---

### 3.4 Edge Fleet Distribution via Open Cluster Management (OCM)

PrimaryHub functions as the **OCM Hub Cluster**, managing edge clusters via Open Cluster Management declarative APIs.

1. **ManifestWork CRD:** The rotator generates a `ManifestWork` resource inside each cluster's tracking namespace on the Hub (e.g., `spoke1`, `spoke2`, `spoke3`):
   ```yaml
   apiVersion: work.open-cluster-management.io/v1
   kind: ManifestWork
   metadata:
     name: spoke-prometheus-remote-write-auth
     namespace: spoke1
   spec:
     workload:
       manifests:
         - apiVersion: v1
           kind: Secret
           metadata:
             name: prometheus-remote-write-auth
             namespace: monitoring
           type: Opaque
           stringData:
             username: ""
             password: ""
   ```
2. **Klusterlet Agent Reconciliation:** The `klusterlet-work-agent` running on the edge spoke polls the Hub over the encrypted WireGuard link, detects the new `ManifestWork`, and applies the secret to the spoke's local `monitoring` namespace.
3. **Automated Pod Reload:** The edge Prometheus pod mounts this secret. A sidecar container (`prometheus-config-reloader`) monitors the mounted file system. When the secret volume updates, the reloader issues a local `POST /-/reload` call to the edge Prometheus container.

---

### 3.5 Greenfield Node Bootstrap via AWS SSM Parameter Store

To guarantee that newly provisioned EC2 instances can immediately join the telemetry plane without waiting for a manual credential push, Phase 3 of the rotator synchronizes credentials with AWS Systems Manager (SSM) Parameter Store in `us-east-1`:

* `/sandbox/monitoring/rw-username` (`Type: String`)
* `/sandbox/monitoring/rw-password` (`Type: SecureString`, encrypted with AWS KMS key `aws/ssm`)

#### Terraform Cloud-Init Integration (`user_data.sh.tftpl`)
When Terraform launches a new edge node in Frankfurt or Mumbai, the cloud-init bootstrap script uses the instance's IAM role to fetch the live credential:

```bash
# Extract active remote-write credentials from AWS SSM Parameter Store
RW_USER=$(aws ssm get-parameter \
  --name "/sandbox/monitoring/rw-username" \
  --region "us-east-1" \
  --query "Parameter.Value" \
  --output text)

RW_PASS=$(aws ssm get-parameter \
  --name "/sandbox/monitoring/rw-password" \
  --with-decryption \
  --region "us-east-1" \
  --query "Parameter.Value" \
  --output text)

# Deploy Kubernetes secret directly to edge node
kubectl create secret generic prometheus-remote-write-auth \
  --namespace monitoring \
  --from-literal=username="$RW_USER" \
  --from-literal=password="$RW_PASS" \
  --dry-run=client -o yaml | kubectl apply -f -
```

This guarantees 100% autonomous, zero-touch bootstrapping for all auto-scaled or newly provisioned compute nodes.

---

## 4. Pillar 3: Decoupled Long-Term Storage with Cortex & AWS S3

### 4.1 Why EBS gp3 Fails at Scale: The Mathematical & Operational Bottleneck

PrimaryHub originally retained metrics on a local AWS EBS `gp3` volume. While adequate for single-cluster environments, this design encounters severe constraints at scale:

#### 1. The TSDB Compaction Memory Explosion
Prometheus TSDB cuts 2-hour immutable blocks containing inverted index structures (`index`), chunk files (`chunks/00000X`), and metadata (`meta.json`). Every 2 hours, the **TSDB Compactor** merges smaller blocks into larger blocks (4h, 8h, 16h, 32h).

The compaction algorithm requires holding the index dictionary and postings lists in RAM. For $M$ active time series and $C$ clusters:

$$\text{RAM}_{\text{compaction}} \propto \mathcal{O}(C \times M \times \log M)$$

On PrimaryHub, with 5 clusters generating over 15,000 active series, compactor cycles induced RAM spikes exceeding **$3.8\text{ GB}$**. In a shared host environment, these spikes starved the collocated Valkey cache and PostgreSQL databases, risking kernel Out-Of-Memory (OOM) kills.

#### 2. Cost Analysis: EBS gp3 vs. AWS S3 Standard
Assume a telemetry retention requirement of **12 months** across 5 clusters, generating an average ingestion rate of $1,500\text{ samples/sec}$:

* Total samples per year:
  $$1500\text{ samples/sec} \times 86,400\text{ sec/day} \times 365\text{ days} \approx 4.73 \times 10^{10}\text{ samples}$$
* Compacted storage footprint at $1.5\text{ bytes/sample}$:
  $$4.73 \times 10^{10} \times 1.5\text{ bytes} \approx 70.95\text{ GB} \approx 71\text{ GB/year}$$
* With overhead, index caching, and write buffers: $\approx 250\text{ GB}$.

| Storage Tier | Monthly Cost per GB | 250 GB Monthly Storage Cost | Annual Cost | High-Availability & Durability |
| :--- | :--- | :--- | :--- | :--- |
| **AWS EBS gp3** | \$0.080 / GB | \$20.00 / month | \$240.00 / year | Single-AZ (99.9% durability) |
| **AWS S3 Standard** | \$0.023 / GB | \$5.75 / month | \$69.00 / year | Multi-AZ (99.999999999% / 11 9s durability) |
| **AWS S3 Standard-IA** | \$0.0125 / GB | \$3.12 / month | \$37.44 / year | Multi-AZ (11 9s durability) |

**Cost Reduction:** Moving long-term metric storage to AWS S3 delivers an immediate **71.25% cost reduction** compared to EBS, while increasing data durability from 3 nines to 11 nines across multiple availability zones.

---

### 4.2 Dedicated Hardened EC2 Architecture & Network Topology

To guarantee absolute isolation from PrimaryHub's database and microVM workloads, Cortex is provisioned on a dedicated, hardened EC2 instance:

* **Instance Identifier:** `cortex` (`i-03ab35c403b0014e6`)
* **Instance Type:** `t3.medium` (2 vCPU, 4 GiB RAM)
* **VPC Subnet:** Private Subnet (`10.20.149.67`) in `us-east-1`
* **Public IP Address:** **None** (Zero internet-facing IPv4 address)
* **WireGuard Tunnel IP:** `10.99.0.50/24`
* **Local EBS Working Volume:** 30 GiB gp3, AWS KMS encrypted (`aws/ebs`)
* **IMDSv2 Enforced:** HTTP PUT response hop limit set to **4** (`--http-put-response-hop-limit 4`) to allow credential traversal through host, Docker/KinD bridge, and container layers.
* **Remote Management:** SSH (port 22) is permanently disabled. Ingress administration is conducted exclusively via AWS Systems Manager (SSM) Session Manager.

---

### 4.3 NGINX Security & RBAC Reverse Proxy Gate

The Cortex service listens internally on loopback `127.0.0.1:9009`. All external traffic must ingress through an **NGINX reverse proxy** listening on port `8443`.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ NGINX SECURITY & RBAC PROXY (Port 8443)                                               │
│                                                                                        │
│   Ingress Request (TLS 1.3 Encrypted)                                                  │
│           │                                                                            │
│           ├─► 1. TLS 1.3 Termination (Self-Signed / Internal CA Certificate)           │
│           │                                                                            │
│           ├─► 2. HTTP Basic Auth Inspection (.htpasswd Bcrypt Hashes)                  │
│           │                                                                            │
│           ├─► 3. Role-Based Route Enforcement:                                         │
│           │      • POST /api/v1/push ──► Allowed ONLY for user: "cortex-writer"        │
│           │      • GET /prometheus/* ──► Allowed ONLY for user: "grafana-reader"       │
│           │      • GET /ring, /compactor ──► BLOCKED (HTTP 403 Forbidden)              │
│           │                                                                            │
│           ├─► 4. Tenant Header Injection:                                              │
│           │      proxy_set_header X-Scope-OrgID "sandbox-metrics";                     │
│           │                                                                            │
│           ▼                                                                            │
│   Forward to Localhost: http://127.0.0.1:9009                                          │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

#### Production NGINX Configuration Blueprint (`nginx.conf`)
```nginx
events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;
    sendfile      on;
    keepalive_timeout 65;

    # SSL / TLS 1.3 Hardening
    ssl_protocols TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    upstream cortex_backend {
        server 127.0.0.1:9009;
        keepalive 32;
    }

    server {
        listen 8443 ssl;
        server_name cortex.local 10.99.0.50;

        ssl_certificate     /etc/nginx/certs/cortex.crt;
        ssl_certificate_key /etc/nginx/certs/cortex.key;

        auth_basic "Cortex Secure Observability Gateway";
        auth_basic_user_file /etc/nginx/auth/.htpasswd;

        # 1. WRITE-ONLY RBAC GATE (PrimaryHub Telemetry Push)
        location = /api/v1/push {
            if ($remote_user != "cortex-writer") {
                return 403 "Forbidden: Cortex Ingestion requires cortex-writer credentials.\n";
            }
            proxy_pass http://cortex_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Scope-OrgID "sandbox-metrics";
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            client_max_body_size 64M;
        }

        # 2. READ-ONLY RBAC GATE (Central Grafana Historical Queries)
        location /prometheus/ {
            if ($remote_user != "grafana-reader") {
                return 403 "Forbidden: Cortex Query requires grafana-reader credentials.\n";
            }
            proxy_pass http://cortex_backend;
            proxy_set_header Host $host;
            proxy_set_header X-Scope-OrgID "sandbox-metrics";
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_read_timeout 120s;
        }

        # 3. BLOCK SENSITIVE ADMINISTRATIVE ENDPOINTS
        location ~ ^/(ring|compactor|all_user_stats) {
            deny all;
            return 403 "Forbidden: Administrative endpoints are blocked.\n";
        }

        # 4. UNPROTECTED HEALTH / READINESS PROBE
        location = /ready {
            auth_basic off;
            proxy_pass http://cortex_backend;
            proxy_http_version 1.1;
        }
    }
}
```

---

### 4.4 Cortex Engine Architecture (Ingester, Compactor, Shipper, Store-Gateway)

Cortex runs as a single unified binary (`target: all`) configured for block-storage mode.

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ CORTEX MONOLITHIC ENGINE                                                               │
│                                                                                        │
│   POST /api/v1/push                                                                    │
│          │                                                                             │
│          ▼                                                                             │
│   ┌───────────────┐     Write Buffer     ┌──────────────────────────────────────────┐  │
│   │  Distributor  │ ───────────────────► │ Ingester (In-Memory TSDB Head)            │  │
│   └───────────────┘                      └────────────────────┬─────────────────────┘  │
│                                                               │                        │
│                                          Flush 2h Blocks      │ Append to Local WAL    │
│                                                               ▼                        │
│   ┌───────────────┐                      ┌──────────────────────────────────────────┐  │
│   │ Store-Gateway │                      │ Local Working Disk (/var/lib/cortex/tsdb)│  │
│   │ (Index Cache) │                      └────────────────────┬─────────────────────┘  │
│   └───────┬───────┘                                           │                        │
│           │                                                   ▼                        │
│           │ Historical Queries                   ┌──────────────────────────────────┐  │
│           │                                      │ Compactor & Shipper Daemon       │  │
│           │                                      └────────────────────┬─────────────┘  │
│           ▼                                                           │ Ship Blocks    │
│   ┌───────────────────────────────────────────────────────────────────┼─────────────┐  │
│   │ AWS S3 BUCKET: 01-sandbox-cortex-storage                          ▼             │  │
│   │ Prefix: sandbox-metrics/                                                        │  │
│   │ Structure: [meta.json, index, chunks/000001, chunks/000002]                     │  │
│   └─────────────────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

#### Detailed Subsystem Roles:
1. **Distributor:** Receives incoming push payloads from NGINX. Validates sample timestamps against `creation_grace_period` (10m) and splits metrics by tenant (`X-Scope-OrgID`).
2. **Ingester:** Appends samples into active in-memory Head chunks. Maintains a local write-ahead log on `/var/lib/cortex/tsdb/wal` to ensure crash recovery.
3. **Shipper & Compactor:** Every 2 hours, the ingester cuts an immutable TSDB block. The Shipper daemon uploads the block directly to AWS S3:
   ```
   s3://01-sandbox-cortex-storage/sandbox-metrics/
   ├── 01HMGW8414P0Z8.../
   │   ├── meta.json          # Block metadata, min/max time, series count
   │   ├── index              # Inverted symbol index table
   │   └── chunks/
   │       └── 000001         # Snappy-compressed chunk data
   └── cortex-shipper-active-series.json
   ```
   Once confirmed on S3, local block chunks are deleted from disk. **Local EBS storage usage never accumulates.**
4. **Store-Gateway:** When Grafana queries data older than 2 hours, the Store-Gateway scans S3 block index headers, caches frequently accessed indices in memory, and streams chunk bytes directly into the Querier without loading full blocks to disk.

---

### 4.5 AWS S3 Bucket Configuration, Encryption & Lifecycle Tiering

The S3 storage backend is provisioned with zero public access and strict encryption:

* **Bucket Name:** `01-sandbox-cortex-storage`
* **Region:** `us-east-1`
* **Server-Side Encryption:** AES-256 (`aws:kms` or `AES256`)
* **Bucket Policy:** Denies unencrypted HTTP uploads (`aws:SecureTransport: false`).
* **S3 Lifecycle Rules:**
  * **Day 0–90:** S3 Standard storage (\$0.023/GB) for maximum query performance.
  * **Day 91–365:** Automatic transition to **S3 Standard-Infrequent Access (IA)** (\$0.0125/GB).
  * **Day 366+:** Automatic transition to **S3 Glacier Flexible Retrieval** (\$0.0036/GB) for compliance archiving.

---

### 4.6 PrimaryHub Upstream Remote Write & Downsizing

With Cortex handling long-term storage, PrimaryHub's local TSDB retention was downsized from 7 days to **6 hours**:

```bash
# PrimaryHub Prometheus arguments
--storage.tsdb.retention.time=6h
--storage.tsdb.min-block-duration=2h
--storage.tsdb.max-block-duration=2h
```

PrimaryHub streams all ingested metrics to Cortex via its `remote_write` configuration:

```yaml
remote_write:
  - url: "https://10.99.0.50:8443/api/v1/push"
    name: "cortex-s3-lts"
    remote_timeout: 30s
    tls_config:
      insecure_skip_verify: true  # Secure over encrypted WireGuard mesh
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

## 5. Unified Fleet Observability & Grafana Integration

### 5.1 Dual-Tier Datasource Topology (Real-Time vs Historical)

Central Grafana connects to two distinct datasources to balance latency and historical depth:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ CENTRAL GRAFANA (PRIMARYHUB)                                                           │
│                                                                                        │
│   Query: rate(node_cpu_seconds_total[5m])                                              │
│          │                                                                             │
│          ├─► Time Range: Last 1 Hour ──────► Datasource: "Prometheus"                  │
│          │                                   URL: http://prometheus-server:80          │
│          │                                   Target: PrimaryHub In-Memory TSDB         │
│          │                                   Latency: < 15ms                           │
│          │                                                                             │
│          └─► Time Range: Last 6 Months ────► Datasource: "cortex-s3-longterm"          │
│                                              URL: https://10.99.0.50:8443/prometheus   │
│                                              Target: Cortex Store-Gateway & AWS S3     │
│                                              Latency: 280ms - 850ms                    │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

#### Datasource Blueprint (`configmap-datasources.yaml`)
```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://codeinspector-prometheus-server.opensandbox-system.svc.cluster.local:80
    isDefault: true
    jsonData:
      timeInterval: "15s"

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

### 5.2 Dynamic Label Indexing & Dashboard Variable Templating

Grafana dashboards utilize dynamic label reflection to auto-discover edge clusters without hard-coding dropdown menus:

* **Variable Name:** `$cluster`
* **Query Type:** Label Values
* **Label:** `cluster`
* **Metric Query:** `label_values(up, cluster)`
* **Refresh:** `On Dashboard Load` & `On Time Range Change`

When a new edge spoke (e.g., `spoke3`) pushes its first sample batch with `cluster="spoke3"`, PrimaryHub indexes the label. Within 10 seconds, `spoke3` appears automatically in Grafana's cluster selector dropdown.

---

## 6. Operational Runbooks & Incident Response

### 6.1 Validating Metric Ingestion Across All Tiers

#### Step 1: Verify Edge Spoke Outbound Push
On the edge node (e.g., Spoke 1 or Spoke 2):
```bash
# Check remote-write queue status and sample transmission rate
kubectl -n monitoring exec -it deploy/prometheus-scraper -- \
  wget -q -O - http://localhost:9090/api/v1/query?query=rate\(prometheus_remote_storage_samples_total\[5m\]\)
```
*Expected Output:* Non-zero rate (e.g., `45.2` samples/sec committed).

#### Step 2: Verify PrimaryHub Dual-Auth Acceptance
On PrimaryHub:
```bash
# Verify active series indexed from all spokes
curl -s http://localhost:30900/api/v1/query?query=count%20by%20\(cluster\)\(up\) | jq .
```
*Expected Output:*
```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      { "metric": { "cluster": "primaryhub" }, "value": [ 1730800000, "18" ] },
      { "metric": { "cluster": "secondaryhub" }, "value": [ 1730800000, "12" ] },
      { "metric": { "cluster": "spoke1" }, "value": [ 1730800000, "8" ] },
      { "metric": { "cluster": "spoke2" }, "value": [ 1730800000, "8" ] },
      { "metric": { "cluster": "spoke3" }, "value": [ 1730800000, "8" ] }
    ]
  }
}
```

#### Step 3: Verify Cortex NGINX Auth & Ingestion
From PrimaryHub, test write access against Cortex:
```bash
curl -k -u cortex-writer:YOUR_PASSWORD -X POST \
  https://10.99.0.50:8443/api/v1/push
```
*Expected Output:* `HTTP 204 No Content` or `empty response` (not 401 or 403).

---

### 6.2 Triggering On-Demand Manual Secret Rotation

To force an immediate, out-of-band credential rotation:
```bash
# 1. Trigger the CronJob manually
kubectl -n opensandbox-system create job \
  --from=cronjob/prometheus-secret-rotator \
  manual-rotation-$(date +%s)

# 2. Follow rotation execution logs
kubectl -n opensandbox-system logs -f job/manual-rotation-*

# 3. Verify Dual-Auth web.yml configuration on PrimaryHub
kubectl -n opensandbox-system get secret prometheus-web-config \
  -o jsonpath='{.data.web\.yml}' | base64 -d
```

---

### 6.3 Verifying Cortex S3 Block Creation & Retention

To inspect blocks shipped to AWS S3:
```bash
# List TSDB blocks in S3
aws s3 ls s3://01-sandbox-cortex-storage/sandbox-metrics/ --recursive --human-readable

# Check local EBS disk usage on Cortex node (guaranteed to be flat)
df -h /var/lib/cortex
```
*Expected Disk Usage:* Local disk remains bounded between **$8\text{ GiB}$** and **$16\text{ GiB}$**, never filling the 30 GiB root volume.

---

### 6.4 Failure Modes & Recovery Strategies

| Failure Scenario | Root Cause | Automatic Mitigation | Manual Recovery Procedure |
| :--- | :--- | :--- | :--- |
| **Edge Spoke returns HTTP 401** | Spoke received new secret before Hub Dual-Auth activated | Dual-Auth overlap guarantees Hub accepts old + new simultaneously | Verify `prometheus-web-config` secret on Hub contains both users; trigger `kill -HUP 1` on Hub Prometheus |
| **Cortex reports S3 Access Denied** | EC2 IMDS hop limit too low for pod-to-host traversal | Terraform enforces hop limit 4 | Run `aws ec2 modify-instance-metadata-options --instance-id <ID> --http-put-response-hop-limit 4` |
| **WAN Network Partition (WireGuard Down)** | Trans-Atlantic ISP fiber break or routing outage | Edge TSDB WAL buffers all metrics locally for up to 6 hours | When WireGuard link recovers, WAL Watcher automatically streams back-buffered metrics with zero data loss |
| **PrimaryHub TSDB Lock Error on Restart** | Old Prometheus container held lock on `/prometheus` EBS directory | StatefulSet single-replica guarantees exclusive write access | Force delete old pod: `kubectl -n opensandbox-system delete pod <pod-name> --force --grace-period=0` |
| **New Edge Node missing metrics** | Cloud-init failed to pull credentials from AWS SSM | IAM instance profile allows `ssm:GetParameter` with decrypt | Verify instance role `EC2-SSM-Monitoring-Role` is attached; test via `aws ssm get-parameter --name /sandbox/monitoring/rw-username` |

---

## 7. Summary & Architectural Validation

The integration of **Push-Based Prometheus Remote Write**, **Kubernetes-Native Dual-Auth Secret Rotation**, and **Decoupled Cortex AWS S3 Storage** establishes an enterprise-grade observability posture for 01-Sandbox:

1. **Security:** Zero open inbound edge ports, 100% encrypted in-transit (WireGuard ChaCha20 + TLS 1.3), automated 30-day credential lifecycle, and multi-tenant isolation in S3.
2. **Reliability:** 6-hour edge WAL crash-buffering eliminates WAN jitter loss, and isolated Cortex compute eliminates host memory contention on PrimaryHub.
3. **Economics & Scale:** Over **71% reduction in storage expenditure** with unbounded multi-year historical retention.
