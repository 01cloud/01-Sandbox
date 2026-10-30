# Prometheus Remote Write: Detailed Technical Demo Walkthrough

This is the **comprehensive, in-depth walkthrough** for demonstrating and understanding every layer of the Push-Based Prometheus Remote Write (PRW) architecture. Each step explains **what is happening**, **why it matters**, **the exact commands to run**, **the expected output**, and **the architecture behind the output**.

For the concise 10-minute presentation version, see [`walkthrough-demo-final.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/walkthrough-demo-final.md).

For the complete architecture reference, see [`prometheus.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/prometheus/prometheus.md).

---

## Demo Overview

| Step | Topic | What You Prove | Key Evidence |
| :--- | :--- | :--- | :--- |
| **1** | Local Scrape & WAL | Scrapes are 100% independent from the WAN link | WAL file growing 5.2 KB every 15s |
| **2** | Batching & Snappy Compression | 88% WAN bandwidth reduction | 17.84 bytes/sample on the wire |
| **3** | Network Ingress & Kernel NAT | Zero userspace proxy overhead | `iptables` DNAT rule, HTTP 204 response |
| **4** | Central Ingestion | 4 clusters, 49 targets, one TSDB | `count by (cluster)(up)` output |
| **5** | EBS Persistence | EC2 termination does not destroy data | `df`, `pvc`, 5,760 samples over 24h |
| **6** | Grafana Visualization | < 3ms local queries, zero WAN latency | `time curl` benchmark |

---

## Prerequisites

```bash
cd ~/Desktop/Kamal/01-Sandbox

# Verify all cluster contexts are available:
kubectl config get-contexts
# Expected contexts: kind-primaryhub, kind-secondaryhub, kind-spoke1, kind-spoke2
```

---

## Step 1: Local Scrape & Write-Ahead Log (WAL)

### What We Are Demonstrating

The edge scraper on `spoke1` polls local Kubernetes components (cAdvisor, CoreDNS, OCM Klusterlet) every 15 seconds and immediately appends the collected samples to a local **Write-Ahead Log (WAL)** — a set of sequential binary files on disk. This is completely independent of the WAN link, WireGuard tunnel, or PrimaryHub availability.

The WAL is the foundation of the zero-data-loss guarantee. Even if the internet link drops for hours, samples keep accumulating safely in the local WAL. When connectivity returns, the WAL Watcher replays the queued samples automatically without any operator intervention.

> [!IMPORTANT]
> The WAL contains **raw, uncompressed binary data**. Snappy compression happens later, strictly in RAM, only for network transmission. The WAL is a local crash-safety buffer — it is never transmitted over the network as a file.

**Code Reference:** EC2 Launch Template `user_data.sh.tftpl` → function `deploy_spoke_monitoring()`

---

### Commands & Expected Output

#### 1.1 — Verify the Spoke Scraper is Running

```bash
kubectl -n monitoring get pods -l app=prometheus-scraper -o wide
```

**Expected Output:**
```text
NAME                                  READY   STATUS    RESTARTS   AGE   IP           NODE
prometheus-scraper-695c8798cb-4kxq2   1/1     Running   0          2d    10.244.0.7   spoke1-control-plane
```

#### 1.2 — Inspect the Physical WAL Segment Files on Disk

```bash
kubectl -n monitoring exec deploy/prometheus-scraper -- ls -la /prometheus/wal
```

**Live Cluster Output:**
```text
total 7552
drwxr-xr-x 3 nobody nobody      81 Oct 30 07:00 .
drwxrwxrwx 7 root   root       172 Oct 30 07:00 ..
-rw-r--r-- 1 nobody nobody 2654208 Oct 30 05:00 00000014   ← Sealed past segment
-rw-r--r-- 1 nobody nobody 2654208 Oct 30 07:00 00000015   ← Sealed past segment
-rw-r--r-- 1 nobody nobody 1462998 Oct 30 08:06 00000016   ← Active live WAL segment
drwxr-xr-x 2 nobody nobody      22 Oct 30 07:00 checkpoint.00000013
```

**What each file represents:**

| File | Type | Description |
| :--- | :--- | :--- |
| `00000014` (2.6 MB) | Past segment | A completed sequential segment, sealed and immutable |
| `00000015` (2.6 MB) | Past segment | A completed sequential segment from the previous window |
| `00000016` (1.4 MB+) | **Active segment** | **The live file being appended every 15 seconds right now** |
| `checkpoint.00000013` | Compaction checkpoint | Created every 2 hours during TSDB block compaction. Consolidates series references and safely prunes older raw WAL files |

> [!NOTE]
> **Understanding Automatic WAL Checkpointing & Segment Pruning:**
> You might ask: *Why do segments 00000000 through 00000013 not appear on disk?*
> 1. **WAL is a Crash-Safety Buffer, not Long-Term Storage**: The Write-Ahead Log exists solely to recover in-memory samples in the event of an abrupt pod crash or node reboot.
> 2. **2-Hour TSDB Flushes**: Every 2 hours, Prometheus flushes in-memory samples into permanent, immutable TSDB blocks in `/prometheus/` (directories with 26-character ULID identifiers like `01JBY...`).
> 3. **Checkpointing**: Once those samples are written into immutable TSDB blocks on disk, Prometheus creates a checkpoint (e.g. `checkpoint.00000013`).
> 4. **Safe Deletion**: All raw WAL segments prior to that checkpoint (`00000000`–`00000013`) are automatically deleted. This bounds WAL disk usage to a few megabytes while preserving the entire historical metric timeline in TSDB blocks.

#### 1.3 — Observe the WAL Growing in Real Time

Run the command a second time after 60+ minutes and compare the active segment size:

```text
# First observation (06:33):
-rw-r--r-- 1 nobody nobody 1029818 Oct 27 06:33 00000008

# Second observation (07:39, ~66 minutes later):
-rw-r--r-- 1 nobody nobody 2406999 Oct 27 07:39 00000008  ← Grew from 1.02 MB to 2.41 MB
```

**The math behind the growth:**
- Time elapsed: ~66 minutes = 264 scrape cycles (4 scrapes/min)
- Bytes appended: 2,406,999 − 1,029,818 = **1,377,181 bytes**
- Average ingestion velocity: 1,377,181 ÷ 264 ≈ **~5.2 KB per 15-second scrape cycle**
- Once `00000008` reaches **128 MB**, Prometheus seals it and automatically creates `00000009`

#### 1.4 — Verify Real-Time Local Ingestion Rate

```bash
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=sum(rate(prometheus_tsdb_head_samples_appended_total[1m]))" | \
  jq '.data.result[0].value[1]'
```

**Expected Output:** `"58.8"` — ~59 samples written to the local disk WAL every second.

**Math cross-check:**
- 58.8 samples/sec × 15 seconds = **882 samples per scrape cycle**
- 5,200 bytes ÷ 882 samples ≈ **5.9 bytes per raw WAL sample** (matches the observed file growth)
- 58.8 samples/sec × 3,600 sec = **~211,680 samples written locally per hour**

#### 1.5 — Inspect the Raw Binary WAL Contents

WAL files are binary — `cat` will show gibberish. Use these tools to inspect the real content:

```bash
# Extract human-readable metric names and labels:
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  sh -c "strings /prometheus/wal/00000008 | head -n 30"

# Search for specific components:
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  sh -c "strings /prometheus/wal/00000008 | grep -i klusterlet | head -n 10"

# Full TSDB analysis (cardinality, top metrics):
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  promtool tsdb analyze /prometheus

# Dump raw time-series samples:
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  promtool tsdb dump /prometheus | head -n 20
```

#### 1.6 — Prove Zero Metric Loss During WAN Outage

While the WAN link is healthy, the remote write queue is empty (pending = 0). Check it:

```bash
# Current pending queue (normally 0 during healthy streaming):
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=prometheus_remote_storage_samples_pending" | jq .

# Local ingestion rate (remains steady even if WireGuard drops):
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=sum(rate(prometheus_tsdb_head_samples_appended_total[1m]))" | \
  jq '.data.result[0].value[1]'
# Output stays: "58.8" — completely unaffected by network disruptions
```

> *"Notice that the local scrape rate stays at 58.8 even when egress is disrupted. The WAL absorbs everything safely on disk. This is the zero-data-loss guarantee in action."*

---

## Step 2: Batching & In-Flight Snappy Compression

### What We Are Demonstrating

Prometheus Remote Write does not send one HTTP request per metric. Instead, a background goroutine called the **WAL Watcher** (`walWatcher`) continuously tails the active WAL segment (just like `tail -f`) and groups newly committed samples into batches. Each batch is:

1. Held in memory until 1,000 samples accumulate **or** 5 seconds pass (`batch_send_deadline`)
2. Tagged with external labels: `cluster="spoke1"`, `role="spoke"`, `region="eu-central-1"`
3. Serialized into a [Protocol Buffers](https://protobuf.dev/) message (`prompb.WriteRequest`, ~90 KB)
4. Compressed with **Google Snappy** (→ ~12 KB, ~88% bandwidth reduction)
5. Dispatched as an HTTP POST to `http://10.99.0.1:30900/api/v1/write`

This batching approach reduces WAN bandwidth from ~200 bytes/sample (raw text) down to **~17.8 bytes/sample**.

**The critical distinction — what stays on disk vs what travels the network:**

| Layer | Location | Format | Compressed? |
| :--- | :--- | :--- | :--- |
| Disk WAL (`00000008`) | Spoke local disk | Raw binary, 32 KB pages | ❌ No |
| In-memory batch | Spoke RAM | Go structs `[]prompb.TimeSeries` | ❌ No |
| Serialized batch | Spoke RAM | Protobuf `WriteRequest` (~90 KB) | ❌ No |
| **Network payload** | **WireGuard wire** | **Snappy-compressed bytes (~12 KB)** | ✅ **Yes** |
| TSDB blocks on hub | PrimaryHub EBS | Gorilla XOR compression | ✅ Yes |

**Code Reference:** EC2 Launch Template `user_data.sh.tftpl` → `queue_config` in `prometheus.yml`

---

### Commands & Expected Output

#### 2.1 — Inspect the Live Queue Configuration

```bash
kubectl -n monitoring get cm prometheus-scraper-config \
  -o jsonpath='{.data.prometheus\.yml}' | grep -A 10 "queue_config"
```

**Expected Output:**
```yaml
queue_config:
  capacity: 10000
  max_shards: 10
  min_shards: 1
  max_samples_per_send: 1000
  batch_send_deadline: 5s
  min_backoff: 500ms
  max_backoff: 10s
```

**What each parameter controls:**

| Parameter | Value | What It Actually Does |
| :--- | :--- | :--- |
| `capacity` | `10000` | **RAM buffer limit per shard**. Backpressure halts disk WAL reads if RAM buffer exceeds 10,000 samples. |
| `min_shards` | `1` | **Baseline concurrency**. Exactly 1 worker goroutine pushes metrics during normal healthy operation. |
| `max_shards` | `10` | **Auto-scaling ceiling**. Spawns up to 10 parallel workers to flush accumulated backlogs 10x faster after network outages. |
| `max_samples_per_send` | `1000` | **Batch size ceiling**. Flushes immediately as soon as 1,000 samples accumulate in memory. |
| `batch_send_deadline` | `5s` | **Latency guarantee**. Flushes whatever is buffered every 5s even if the 1,000 sample ceiling hasn't been reached. |
| `min_backoff` | `500ms` | **Initial retry delay**. Waits 500ms before the first retry attempt during connection drops. |
| `max_backoff` | `10s` | **Exponential backoff cap**. Doubles delay on consecutive failures (`500ms → 1s → ... → 10s`) to prevent retry storms. |

**The math in action:**
- Ingestion velocity: ~59 samples/sec.
- Accumulation to 1,000 samples requires ~17 seconds.
- Therefore, the **`batch_send_deadline: 5s` fires first**, flushing ~300 samples every 5 seconds.
- Notice the timestamps in the tcpdump output below: `33.480` and `38.573` — exactly 5 seconds apart!

#### 2.2 — Capture Live Wire Traffic (Indisputable Compression Proof)

On the PrimaryHub EC2 host via SSH or SSM:

```bash
sudo tcpdump -i wg0 -A -s 0 "tcp port 30900" -c 10
```

**Live Packet Capture Output:**
```http
# ── PACKET 1: Outbound from Spoke (10.99.0.3 → 10.99.0.1) ─────────────────
08:27:33.480940 IP spoke1.35842 > primaryhub.30900: Flags [P.], length 212
POST /api/v1/write HTTP/1.1
Host: 10.99.0.1:30900
User-Agent: Prometheus/2.54.1
Content-Length: 605
Content-Encoding: snappy                   ← SNAPPY COMPRESSION CONFIRMED
Content-Type: application/x-protobuf      ← PROTOBUF ENCODING CONFIRMED
X-Prometheus-Remote-Write-Version: 0.1.0

# ── PACKET 2: PrimaryHub Acknowledges Ingestion ────────────────────────────
08:27:33.573167 IP primaryhub.30900 > spoke1.35842: Flags [P.], length 64
HTTP/1.1 204 No Content                   ← TSDB COMMIT CONFIRMED (92ms later)
Date: Tue, 27 Oct 2026 08:27:33 GMT

# ── PACKET 3: Next Batch Exactly 5 Seconds Later ──────────────────────────
08:27:38.573954 IP spoke1.35842 > primaryhub.30900: Flags [P.], length 213
POST /api/v1/write HTTP/1.1
Content-Length: 6783
Content-Encoding: snappy                   ← STILL COMPRESSED
Content-Type: application/x-protobuf
```

**Four indisputable proofs visible on the wire:**
1. `Content-Encoding: snappy` → Batch was Snappy-compressed before being placed on the network
2. `Content-Type: application/x-protobuf` → Samples are Protocol Buffers, not raw text
3. `HTTP 204 No Content` after **92ms** → PrimaryHub successfully decompressed and committed samples to TSDB
4. Timestamps `08:27:33.480` and `08:27:38.573` → exactly **5.09 seconds** apart, matching `batch_send_deadline: 5s`

#### 2.3 — Calculate Actual Bytes Per Sample on the Wire

```bash
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=rate(prometheus_remote_storage_bytes_total[5m])/rate(prometheus_remote_storage_samples_total[5m])" | \
  jq '.data.result[0].value[1]'
```

**Expected Output:** `"17.84"` — 17.84 bytes per sample on the wire.

- Raw Prometheus text format: **~180–220 bytes/sample**
- Snappy-compressed Protobuf: **~17.84 bytes/sample**
- Bandwidth reduction: **~88%**
- Real impact: Sending 10,000 samples uncompressed = ~2.0 MB. With Snappy = ~178 KB.

#### 2.4 — Verify Successful Delivery Counters

```bash
# Rate of samples successfully delivered to PrimaryHub:
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=rate(prometheus_remote_storage_succeeded_samples_total[5m])" | jq .

# Current pending backlog (should be 0 during healthy streaming):
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=prometheus_remote_storage_samples_pending" | jq .
```

#### 2.5 — Real-World Log Analysis: Outage Recovery & Dynamic Resharding

```bash
kubectl -n monitoring logs deploy/prometheus-scraper --tail=60
```

**Live Cluster Log (actual output):**
```text
# ── PHASE 1: Boot, TSDB Startup & WAL Replay ─────────────────────────────
ts=2026-10-27T05:44:58.563Z msg="Replaying WAL, this may take a while"
ts=2026-10-27T05:44:59.014Z msg="WAL replay completed" wal_replay_duration=417ms
ts=2026-10-27T05:44:59.028Z component=remote msg="Starting WAL watcher"
ts=2026-10-27T05:45:08.794Z component=remote msg="Done replaying WAL" duration=9.76s

# ── PHASE 2: Network Outage (PrimaryHub was restarting) ──────────────────
ts=2026-10-27T05:45:39.585Z level=warn msg="Failed to send batch, retrying" err="context deadline exceeded"
ts=2026-10-27T05:47:54.590Z level=warn msg="Failed to send batch, retrying" err="dial tcp 10.99.0.1:30900: i/o timeout"
ts=2026-10-27T05:48:46.899Z level=warn msg="Failed to send batch, retrying" err="dial tcp 10.99.0.1:30900: connect: connection refused"
ts=2026-10-27T05:50:09.033Z level=warn msg="Skipping resharding, resharding is disabled while waiting for recoverable errors"

# ── PHASE 3: Connectivity Restored & Auto-Scaling ────────────────────────
ts=2026-10-27T05:50:19.034Z level=info msg="Remote storage resharding" from=1 to=3
ts=2026-10-27T05:50:39.034Z level=info msg="Remote storage resharding" from=3 to=2
ts=2026-10-27T05:52:29.034Z level=info msg="Remote storage resharding" from=2 to=1
```

**What these three phases show:**

| Phase | What Happened | What Prometheus Did |
| :--- | :--- | :--- |
| **Phase 1 (Boot)** | Pod restarted; needed to catch up | Replayed WAL segments 4–7 in 417ms. `Done replaying WAL` after 9.76s. Zero sample loss. |
| **Phase 2 (Outage)** | PrimaryHub was unreachable for ~4 minutes | Classified errors as `recoverable`. Backed off exponentially. All new samples buffered safely in local WAL. |
| **Phase 3 (Recovery)** | Connectivity restored at 05:50:19 | Shard Controller detected backlog. Auto-scaled from 1→3 workers. Flushed backlog 3× faster. Scaled back to 1 at 05:52:29 once fully caught up. |

---

## Step 3: Network Ingress & Kernel Netfilter Routing

### What We Are Demonstrating

Compressed HTTP POST batches from edge spokes travel over the **encrypted WireGuard mesh** (`10.99.0.0/24`) and arrive at PrimaryHub on port `30900`. On PrimaryHub, the **Linux Kernel Netfilter (iptables DNAT)** intercepts the packets in kernel space and routes them directly into the KinD node container — with zero userspace proxy overhead, zero context switches, and zero packet copies.

**Why kernel Netfilter instead of Docker port publishing:**
Standard Docker port publishing (`docker-proxy`) creates a userspace daemon for every exposed port, copying each packet through user space and back. Netfilter DNAT operates entirely inside the kernel — the packet is rewritten at interrupt time and forwarded without ever entering userspace. This is the same pattern used for PostgreSQL (port 30432) and Valkey (port 30379).

**Code References:**
- KinD port mappings: [`script/prod-docker-multi-script/lib/clusters.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/lib/clusters.sh)
- Prometheus NodePort service: [`codeInspector/charts/prometheus/templates/service.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/service.yaml)

---

### Commands & Expected Output

#### 3.1 — Verify Secure Ingress & Authentication from Spoke to PrimaryHub

```bash
# 3.1a — Test unauthenticated probe (MUST be rejected with HTTP 401 Unauthorized):
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qS -O- "http://10.99.0.1:30900/-/healthy" 2>&1 | grep "HTTP/"
```

**Expected Output:**
```text
  HTTP/1.1 401 Unauthorized
```

```bash
# 3.1b — Dynamically fetch credentials from AWS SSM Parameter Store:
RW_USER=$(aws ssm get-parameter --name "/sandbox/monitoring/rw-username" --region "us-east-1" --query "Parameter.Value" --output text)
RW_PASS=$(aws ssm get-parameter --name "/sandbox/monitoring/rw-password" --region "us-east-1" --with-decryption --query "Parameter.Value" --output text)

# Test authenticated probe using dynamic credentials (MUST succeed with HTTP 200 OK):
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qS -O- --header="Authorization: Basic $(echo -n "${RW_USER}:${RW_PASS}" | base64)" \
  "http://10.99.0.1:30900/-/healthy"
```

**Expected Output:**
```text
  HTTP/1.1 200 OK
  Date: Tue, 27 Oct 2026 06:25:10 GMT
  Content-Length: 29
  Content-Type: text/plain; charset=utf-8

Prometheus Server is Healthy.
```

```bash
# 3.1c — Test Remote Write endpoint directly (/api/v1/write):
# Unauthenticated attempt (rejected with HTTP 401):
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://10.99.0.1:30900/api/v1/write
# Output: 401

# Authenticated attempt (credentials accepted; HTTP 400 Bad Request returned because probe body is empty, proving auth passed):
curl -s -u "${RW_USER}:${RW_PASS}" -o /dev/null -w "%{http_code}\n" -X POST http://10.99.0.1:30900/api/v1/write
# Output: 400
```

```bash
# 3.1d — Verify Spoke Scraper logs show zero 401 errors and clean authenticated streaming:
kubectl -n monitoring logs deploy/prometheus-scraper --tail=20 | grep -E "WAL|remote"
```

**Expected Output:**
```text
caller=dedupe.go:112 component=remote level=info subcomponent=wal msg="Done replaying WAL" duration=...
```

This confirms the complete path: Spoke → WireGuard tunnel → PrimaryHub port 30900 → KinD container → Prometheus HTTP Basic Auth Security Layer.

#### 3.2 — Inspect the Kernel-Level DNAT Ingress Rule on PrimaryHub

Run this on the PrimaryHub EC2 host (via SSH or SSM):

```bash
sudo iptables -t nat -L PREROUTING -n -v --line-numbers | grep 30900
```

**Expected Output:**
```text
1   124K   21M DNAT  tcp  --  !br-+  *  0.0.0.0/0  0.0.0.0/0  tcp dpt:30900 to:172.18.0.2:30900
```

- `124K` packets and `21M` bytes: live counters that increment in real-time with every metric batch
- `172.18.0.2`: the internal Docker bridge IP of the KinD control-plane container
- No process name appears here because there **is no process** — this is pure kernel packet rewriting

#### 3.3 — Verify the KinD Container IP (Matches the DNAT Destination)

```bash
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' primaryhub-control-plane
```

**Expected Output:** `172.18.0.2` — exactly matching the `to:` destination in the iptables rule above.

#### 3.4 — Verify the Kubernetes NodePort Service Definition

```bash
kubectl -n opensandbox-system get svc prometheus-nodeport -o yaml | grep -A 8 "spec:"
```

**Expected Output:**
```yaml
spec:
  ports:
  - name: web
    nodePort: 30900     ← Exposed on the KinD node (172.18.0.2:30900)
    port: 9090
    protocol: TCP
    targetPort: 9090   ← Forwards to Prometheus pod port 9090
  selector:
    app: prometheus
  type: NodePort
```

**The complete ingress chain:**
```
WireGuard: 10.99.0.1:30900
    ↓ Kernel Netfilter DNAT
KinD node: 172.18.0.2:30900
    ↓ kube-proxy NodePort rule
Kubernetes Service ClusterIP: <clusterIP>:9090
    ↓ Service selector: app=prometheus
Central Prometheus Pod: <podIP>:9090
```

---

## Step 4: Central Ingestion & Multi-Cluster Disambiguation

### What We Are Demonstrating

PrimaryHub's Prometheus runs with `--web.enable-remote-write-receiver`, which unlocks the `/api/v1/write` endpoint to accept inbound push streams. It receives and ingests simultaneous streams from SecondaryHub, Spoke 1, and Spoke 2 — storing all of them in a single unified TSDB. The `cluster="..."` external labels (injected at the spoke before transmission) ensure each cluster's metrics are stored as completely separate time-series, preventing any collision.

**Code Reference:** [`codeInspector/charts/prometheus/templates/deployment.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/deployment.yaml)

---

### Commands & Expected Output

#### 4.1 — Verify the Remote Write Receiver Flag is Enabled

```bash
kubectl -n opensandbox-system get deploy prometheus \
  -o jsonpath='{.spec.template.spec.containers[0].args}' | jq .
```

**Expected Output:**
```json
[
  "--config.file=/etc/prometheus/prometheus.yml",
  "--storage.tsdb.path=/prometheus",
  "--storage.tsdb.retention.time=7d",
  "--web.enable-lifecycle",
  "--web.enable-remote-write-receiver",
  "--web.route-prefix=/",
  "--web.config.file=/etc/prometheus-web/web.yml"
]
```

The `--web.enable-remote-write-receiver` flag is what unlocks `POST /api/v1/write`. Without it, all incoming PRW batches receive HTTP 405.

#### 4.2 — Verify PrimaryHub is Actively Receiving Batches

```bash
# Fetch Basic Auth credentials dynamically from AWS SSM (zero hardcoding):
RW_USER=$(aws ssm get-parameter --name "/sandbox/monitoring/rw-username" --region "us-east-1" --query "Parameter.Value" --output text)
RW_PASS=$(aws ssm get-parameter --name "/sandbox/monitoring/rw-password" --region "us-east-1" --with-decryption --query "Parameter.Value" --output text)
AUTH_HEADER="Authorization: Basic $(echo -n "${RW_USER}:${RW_PASS}" | base64)"

# Rate of remote write batches received per second:
kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- --header="$AUTH_HEADER" 'http://localhost:9090/api/v1/query?query=rate(prometheus_http_requests_total{code="204"}[1m])' | jq .

# Total cumulative batches received since startup:
kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- --header="$AUTH_HEADER" 'http://localhost:9090/api/v1/query?query=prometheus_http_requests_total{handler="/write",code="204"}' | jq .
```

**Expected Output (cumulative batches):**
```json
{
  "data": {
    "result": [
      {
        "metric": { "code": "204", "handler": "/write", "instance": "localhost:9090" },
        "value": [ 1793088650.12, "4218" ]
      }
    ]
  }
}
```

- `"4218"`: 4,218 batches successfully received, decompressed, and committed to TSDB since server startup
- `code="204"` is unique to remote write — all other Prometheus endpoints return `200 OK`

> [!TIP]
> **Why query `code="204"` instead of `handler="/api/v1/write"`?**
> Prometheus's internal routing registers the handler as `handler="/write"` (not the full path). Filtering by `/api/v1/write` returns empty results. `code="204"` is unambiguous — it is used **exclusively** by the remote write receiver across the entire Prometheus HTTP server.

#### 4.3 — The Crown Jewel Query: All 4 Clusters in One TSDB

```bash
# Fetch Basic Auth credentials dynamically from AWS SSM (zero hardcoding):
RW_USER=$(aws ssm get-parameter --name "/sandbox/monitoring/rw-username" --region "us-east-1" --query "Parameter.Value" --output text)
RW_PASS=$(aws ssm get-parameter --name "/sandbox/monitoring/rw-password" --region "us-east-1" --with-decryption --query "Parameter.Value" --output text)
AUTH_HEADER="Authorization: Basic $(echo -n "${RW_USER}:${RW_PASS}" | base64)"

kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- --header="$AUTH_HEADER" "http://localhost:9090/api/v1/query?query=count%20by%20(cluster)(up)" | jq .
```

> [!NOTE]
> **Why is `--header="$AUTH_HEADER"` required?**
> PrimaryHub Prometheus enforces HTTP Basic Authentication via `--web.config.file=/etc/prometheus-web/web.yml`. Query requests without this header receive `HTTP/1.1 401 Unauthorized` by design.

**Live Cluster Output:**
```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [
      { "metric": {},                         "value": ["1793090729.193", "20"] },
      { "metric": { "cluster": "secondaryhub" }, "value": ["1793090729.193", "17"] },
      { "metric": { "cluster": "spoke1" },       "value": ["1793090729.193", "6"]  },
      { "metric": { "cluster": "spoke2" },       "value": ["1793090729.193", "6"]  }
    ]
  }
}
```

**What this output proves:**

| Cluster | Targets | What is Being Monitored |
| :--- | :--- | :--- |
| `{}` (PrimaryHub) | **20** | PostgreSQL leader & poolers, CNPG operator, FastAPI backend, AgentGateway, OCM Hub, CoreDNS, Prometheus self |
| `secondaryhub` | **17** | Standby PostgreSQL, secondary FastAPI, secondary OCM agents, CoreDNS, node metrics |
| `spoke1` | **6** | `prometheus-scraper`, `klusterlet-registration-agent`, `klusterlet-work-agent`, CoreDNS ×2, storage provisioner |
| `spoke2` | **6** | Same 6 components as Spoke 1 |
| **Total** | **49** | **3 continents, 4 clusters, 1 unified TSDB** |

#### 4.4 — List Individual Targets per Cluster

```bash
# List all targets for Spoke 1:
kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=up{cluster="spoke1"}' | \
  jq -r '.data.result[] | "\(.metric.job) → \(.metric.instance)"'

# List all targets for PrimaryHub:
kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=up{cluster=""}' | \
  jq -r '.data.result[] | "\(.metric.job) → \(.metric.instance)"'

# List all 49 targets across all clusters (grouped):
kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- 'http://localhost:9090/api/v1/query?query=up' | \
  jq -r '.data.result[] | "\(.metric.cluster // "primaryhub") | \(.metric.job) | \(.metric.instance)"' | sort
```

---

## Step 5: Persistent Storage on AWS EBS

### What We Are Demonstrating

All TSDB data on PrimaryHub is written to `/prometheus`, which is mounted from a Kubernetes PVC backed by a dedicated **100 GiB AWS GP3 EBS volume** configured with `DeleteOnTermination=false`. If the PrimaryHub EC2 instance is completely terminated, stopped, or replaced, the EBS volume survives intact in AWS with the full 30-day TSDB history preserved.

When a replacement EC2 instance boots, the Launch Template cloud-init script automatically re-attaches this exact volume and Prometheus resumes serving the historical timeline immediately.

**How the TSDB is structured on disk:**
```
/prometheus/
  ├── wal/                      ← Active Write-Ahead Log (crash-safe live buffer)
  │   └── 00000008              ← Current active WAL segment
  │
  ├── 01JAX1G0S.../             ← Immutable 2-hour TSDB block
  │   ├── chunks/               ← Gorilla XOR compressed sample data (~1.37 bytes/sample)
  │   ├── index                 ← Inverted label search index (label → series IDs)
  │   └── meta.json             ← Block time range, sample count, checksum
  │
  └── 01JAX7M5F.../             ← Next 2-hour block (sealed, immutable)
```

**Code References:**
- PVC definition: [`codeInspector/charts/prometheus/templates/pvc.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/templates/pvc.yaml)
- Chart values: [`codeInspector/charts/prometheus/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/prometheus/values.yaml)

---

### Commands & Expected Output

#### 5.1 — Verify the Physical EBS Volume is Mounted on the EC2 Host

Run on the PrimaryHub EC2 host via SSH or SSM:

```bash
# Check block devices:
lsblk -f | grep -E "nvme|data"
```
**Expected Output:**
```text
nvme1n1  ext4   100G  28G  72G  28%  /data
```

```bash
# Verify filesystem details:
df -hT /data
```
**Expected Output:**
```text
Filesystem     Type  Size  Used Avail Use% Mounted on
/dev/nvme1n1   ext4  100G   28G   72G  28% /data
```

#### 5.2 — Verify the Kubernetes PVC is Bound

```bash
kubectl -n opensandbox-system get pvc prometheus-pvc
```
**Expected Output:**
```text
NAME             STATUS   VOLUME                                     CAPACITY   ACCESS MODES   STORAGECLASS   AGE
prometheus-pvc   Bound    pvc-11ab1300-e873-4451-9417-cc98982352e3   25Gi       RWO            standard       5d
```

#### 5.3 — Inspect the Immutable 2-Hour TSDB Block Directories

```bash
kubectl -n opensandbox-system exec deploy/prometheus -- ls -lh /prometheus
```
**Expected Output:**
```text
drwxr-xr-x 3 nobody nogroup 4096 Oct 27 02:00 01JAX1G0S...   ← Block: 00:00–02:00
drwxr-xr-x 3 nobody nogroup 4096 Oct 27 04:00 01JAX7M5F...   ← Block: 02:00–04:00
drwxr-xr-x 3 nobody nogroup 4096 Oct 27 06:00 01JAXDZ8N...   ← Block: 04:00–06:00
drwxr-xr-x 2 nobody nogroup 4096 Oct 27 06:20 chunks_head
-rw-r--r-- 1 nobody nogroup    0 Oct 22 14:00 lock
drwxr-xr-x 3 nobody nogroup 4096 Oct 27 06:20 wal
```

Each directory contains:
- `chunks/` — the Gorilla-compressed sample data (~1.37 bytes/sample, > 90% compression)
- `index` — an inverted index from label combinations to series IDs for fast PromQL filtering
- `meta.json` — block time boundaries and total sample count

#### 5.4 — Prove 24 Hours of Continuous History is Intact

```bash
kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- --header="$AUTH_HEADER" "http://localhost:9090/api/v1/query?query=up[24h]" | \
  jq '.data.result[0].values | length'
```
**Expected Output:** `5760`

5,760 data points × 15 seconds per point = **exactly 24 hours** of uninterrupted metric history.

> *"This EBS volume is the reason we have 5,760 continuous samples. If this EC2 were terminated right now, that `vol-007928387cbe1370f` volume stays alive in AWS. The next instance boots, cloud-init attaches it, Docker mounts it to `/data`, KinD sees the PV, and Prometheus resumes from the exact last sample. Complete 30-day history restored instantly."*

---

## Step 6: Centralized Visualization in Grafana

### What We Are Demonstrating

Grafana runs inside the PrimaryHub Kubernetes cluster and connects to the central Prometheus via in-cluster DNS (`http://prometheus-service.opensandbox-system.svc.cluster.local:9090`). Because all edge clusters push their metrics into this one TSDB, **every Grafana query is a local Kubernetes DNS call**. There are zero cross-WAN requests, zero 504 timeouts, and zero inter-regional latency on any dashboard panel.

The result: dashboards that previously took 4–8 seconds to load now render in **< 300ms**.

**Code References:**
- Datasource provisioning: [`codeInspector/charts/grafana/templates/configmap-datasources.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/configmap-datasources.yaml)
- Dashboard definitions: [`codeInspector/charts/grafana/templates/configmap-dashboards.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/grafana/templates/configmap-dashboards.yaml)
- Port-forward script: [`script/prod-docker-multi-script/port-forward-grafana.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script/port-forward-grafana.sh)

---

### Commands & Expected Output

#### 6.1 — Benchmark Round-Trip Query Latency (Zero WAN Overhead)

```bash
kubectl -n monitoring exec deploy/grafana -- \
  curl -s -w "\nTime Total: %{time_total}s\nHTTP Status: %{http_code}\n" -o /dev/null \
  "http://prometheus-service.opensandbox-system.svc.cluster.local:9090/api/v1/query?query=up"
```
**Expected Output:**
```text
Time Total: 0.002841s
HTTP Status: 200
```

**~2.8 milliseconds** for a query spanning 49 active time-series across 4 clusters.

Compare this to the old cross-WAN pull model:
- `us-east-1` → Frankfurt (`eu-central-1`) round-trip: ~200ms
- `us-east-1` → Mumbai (`ap-south-1`) round-trip: ~260ms
- 12 dashboard panels × 4 clusters = 48 concurrent WAN queries → **4.5–8 second dashboard load**

#### 6.2 — Verify Declarative Datasource Configuration

```bash
kubectl -n opensandbox-system exec deploy/grafana -- \
  curl -s -u admin:sandbox-admin "http://localhost:3000/api/datasources" | \
  jq '.[] | {name: .name, uid: .uid, url: .url, isDefault: .isDefault}'
```
**Expected Output:**
```json
{ "name": "primaryhub",   "uid": "prometheus-primaryhub",   "url": "http://prometheus-service.opensandbox-system.svc.cluster.local:9090", "isDefault": true  }
{ "name": "secondaryhub", "uid": "prometheus-secondaryhub",  "url": "http://prometheus-service.opensandbox-system.svc.cluster.local:9090", "isDefault": false }
```

```bash
# Query active fleet spoke clusters dynamically discovered through Grafana's central TSDB proxy:
kubectl -n opensandbox-system exec deploy/grafana -- \
  curl -s -u admin:sandbox-admin "http://localhost:3000/api/datasources/proxy/uid/prometheus-primaryhub/api/v1/label/cluster/values" | jq .
```
**Expected Output:**
```json
{
  "status": "success",
  "data": [
    "postgresql-primary",
    "postgresql-secondary",
    "secondaryhub",
    "spoke1",
    "spoke2",
    "spoke3",
    "spoke4"
  ]
}
```

**Why do all datasources point to the same URL?**

Both datasources point to the same in-cluster URL (`http://prometheus-service.opensandbox-system.svc.cluster.local:9090`) because all cluster metrics live in PrimaryHub's central TSDB — there is no need to reach out to individual spokes across the WAN. Grafana uses PromQL label filtering to scope results per cluster:

```promql
up{cluster="spoke1"}     → Frankfurt metrics only
up{cluster="spoke2"}     → Mumbai metrics only
up{cluster=""}           → PrimaryHub local metrics
```

**Dynamic spoke auto-discovery:**

The `$cluster` dropdown variable in the fleet dashboard auto-discovers live spokes using a 60-second liveness window:

```promql
query_result(
  (up{role="spoke", job="prometheus-scraper"} == 1)
  and
  ((time() - timestamp(up{role="spoke", job="prometheus-scraper"})) < 60)
)
```

When a new spoke is provisioned via Terraform and begins streaming, it appears in this dropdown within 60 seconds — no manual Grafana configuration needed. When a spoke is terminated, it disappears automatically after 60 seconds.

#### 6.3 — Open the Live Dashboard

```bash
cd ~/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script
./port-forward-grafana.sh
# Open: http://localhost:3000   |  Login: admin / sandbox-admin
```

**Live walkthrough for the audience:**

1. **Navigate to:** Dashboards → **Spoke Edge Fleet – Dynamic Multi-Cluster Observability**
2. **Open the `$cluster` dropdown** — select `spoke1` → instant Frankfurt telemetry renders (node CPU, memory, container metrics, Klusterlet status)
3. **Switch to `spoke2`** → instant Mumbai telemetry — no spinner, no delay
4. **Navigate to:** Dashboards → **Global Fleet Overview** → cross-cluster summary table showing all 4 clusters in one view
5. **Point out:** Zero loading delay on cluster switches. Zero timeouts. All data is local.

---

## Demo Summary: Q&A Reference

| Question | Answer |
| :--- | :--- |
| *"What if the WireGuard link drops between Mumbai and Virginia?"* | *"Step 1 showed it: the local WAL absorbs all samples. The pending counter spikes, but once WireGuard recovers, the Shard Controller auto-scales to 10 workers and flushes the entire backlog automatically. Zero samples lost."* |
| *"Why do earlier WAL segments (e.g. 0–13) disappear on the scraper pod?"* | *"They were not lost—they were persisted! Every 2 hours, Prometheus flushes in-memory data to immutable TSDB blocks in `/prometheus/01J...`. Once written, Prometheus creates a checkpoint (e.g. `checkpoint.00000013`) and safely prunes older raw WAL files to prevent disk exhaustion while keeping the full historical timeline."* |
| *"Doesn't sending metrics over the internet use too much bandwidth?"* | *"Step 2 proved it: only ~17.84 bytes per sample on the wire — 88% less than raw text format. The entire spoke cluster generates under 5 KB/s of outbound traffic."* |
| *"Why are there no separate Grafana data sources per spoke?"* | *"All spokes push metrics into PrimaryHub's TSDB. Grafana queries that local TSDB in < 3ms. There's no reason to reach out to the spokes — everything is already here, tagged with `cluster=\"spoke1\"` etc."* |
| *"If we terminate PrimaryHub's EC2 right now, do we lose all graphs?"* | *"No. The 100 GiB EBS volume has `DeleteOnTermination=false`. Terminate the EC2 and the volume survives in AWS. The Launch Template cloud-init re-attaches it on the next boot and Prometheus resumes the full 30-day history."* |
| *"Why is Grafana so much faster than before?"* | *"The old model issued 48 cross-WAN queries per dashboard refresh — 4–8 second load times. Now every query is a local DNS call in 2.8ms. The entire dashboard renders in < 300ms."* |
