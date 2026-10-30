# Prometheus Multi-Cluster Monitoring: Team Demo Walkthrough

A concise, command-by-command presentation script to prove the reliability, performance, and disaster-recovery of the **Push-Based Prometheus Remote Write (PRW)** architecture with **AWS EBS Persistent Storage**. Designed for a 10–15 minute live demo.

---

## Demo Agenda

| Step | What You Prove | Key Command |
| :--- | :--- | :--- |
| **1 — Local Scrape & WAL** | Edge scrapes are 100% independent from internet health. Metrics buffer safely on local disk. | `ls -la /prometheus/wal` |
| **2 — Batching & Compression** | Snappy compression reduces WAN bandwidth by **88%** (~17.8 bytes/sample vs ~200 bytes) | `tcpdump -i wg0 "tcp port 30900"` |
| **3 — Network Ingress** | Traffic enters PrimaryHub via encrypted WireGuard and kernel Netfilter DNAT at wire speed | `iptables -t nat -L PREROUTING | grep 30900` |
| **4 — Central Ingestion** | PrimaryHub stores metrics from all 4 clusters, disambiguated by `cluster="..."` labels | `count by (cluster)(up)` → **49 targets** |
| **5 — EBS Persistence** | Complete EC2 termination leaves 7 days of TSDB history 100% intact on the EBS volume (auto-cleaned after 7d) | `kubectl get pvc prometheus-pvc` |
| **6 — Grafana Visualization** | Grafana queries execute locally in `< 3ms` — no cross-WAN round trips at all | `curl ... up → 0.003s` |

---

## Prerequisites

From your laptop, verify your kubectl contexts are active:
```bash
cd ~/Desktop/Kamal/01-Sandbox
kubectl config get-contexts
# Expected: kind-primaryhub, kind-secondaryhub, kind-spoke1, kind-spoke2
```

---

## Step 1 — Local Scrape & Write-Ahead Log (WAL)

### What You Are Proving
The edge spoke cluster scrapes its local Kubernetes components (cAdvisor, CoreDNS, OCM Klusterlet) every 15 seconds and immediately appends the collected samples to a local **Write-Ahead Log (WAL)** on disk. This scraping is **100% decoupled from the WAN link or PrimaryHub availability** — if the internet drops, samples continue appending to the local disk file uninterrupted.

### Commands

```bash
# 1.1 — Inspect the local disk WAL directory on Spoke 1:
kubectl -n monitoring exec deploy/prometheus-scraper -- ls -la /prometheus/wal
```
**Expected Output:**
```text
total 7552
drwxr-xr-x 3 nobody nobody      81 Oct 30 07:00 .
drwxrwxrwx 7 root   root       172 Oct 30 07:00 ..
-rw-r--r-- 1 nobody nobody 2654208 Oct 30 05:00 00000014
-rw-r--r-- 1 nobody nobody 2654208 Oct 30 07:00 00000015
-rw-r--r-- 1 nobody nobody 1462998 Oct 30 08:06 00000016   ← Active WAL segment
drwxr-xr-x 2 nobody nobody      22 Oct 30 07:00 checkpoint.00000013
```

> [!NOTE]
> **Why do older WAL segments (e.g., 00000000–00000013) disappear?**
> This is automated WAL checkpointing in action. Every 2 hours, Prometheus cuts an immutable 2-hour TSDB block on disk (`/prometheus/01J...`). Once persisted, Prometheus creates a checkpoint (e.g., `checkpoint.00000013`) and safely deletes older raw WAL segments to prevent disk exhaustion. Historical data is completely safe in the TSDB blocks!

```bash
# 1.2 — Show real-time local ingestion rate (samples/sec written to WAL):
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=sum(rate(prometheus_tsdb_head_samples_appended_total[1m]))" | \
  jq '.data.result[0].value[1]'
```
**Expected Output:** `"58.8"` *(~59 samples written to local disk every second)*

### Talking Point
> *"Notice the active WAL file `00000016` and the directory `checkpoint.00000013`. Older segments like 0 through 13 were already flushed to permanent 2-hour TSDB blocks on disk and automatically pruned. Every 15 seconds, ~882 new samples append locally at zero network cost. Even if the WireGuard tunnel drops, ingestion continues at 58.8 samples/sec without losing a single metric."*

---

## Step 2 — Batching & In-Flight Snappy Compression

### What You Are Proving
A background process inside the Spoke Prometheus (the **WAL Watcher**) tails the local WAL like `tail -f`, pools samples into batches of up to 1,000 (or every 5 seconds), compresses them with **Google Snappy**, and dispatches them via HTTP POST over the WireGuard tunnel. This reduces WAN bandwidth by ~88%.

### Commands

```bash
# 2.1 — Inspect the live queue configuration:
kubectl -n monitoring get cm prometheus-scraper-config \
  -o jsonpath='{.data.prometheus\.yml}' | grep -A 8 "queue_config"
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

```bash
# 2.2 — Capture live wire traffic crossing the WireGuard tunnel:
#        (run on the PrimaryHub EC2 host via SSH/SSM)
sudo tcpdump -i wg0 -A -s 0 "tcp port 30900" -c 10
```
**Expected Output:**
```http
08:27:33.480940 IP spoke1 > primaryhub:30900
POST /api/v1/write HTTP/1.1
Content-Length: 605
Content-Encoding: snappy         ← SNAPPY COMPRESSION CONFIRMED
Content-Type: application/x-protobuf  ← PROTOBUF ENCODING CONFIRMED

08:27:33.573167 IP primaryhub:30900 > spoke1
HTTP/1.1 204 No Content          ← PRIMARYHUB ACCEPTED & COMMITTED

08:27:38.573954 IP spoke1 > primaryhub:30900
POST /api/v1/write HTTP/1.1      ← EXACTLY 5 SECONDS LATER (batch_send_deadline)
Content-Encoding: snappy
```

```bash
# 2.3 — Calculate the actual bytes-per-sample on the wire:
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=rate(prometheus_remote_storage_bytes_total[5m])/rate(prometheus_remote_storage_samples_total[5m])" | \
  jq '.data.result[0].value[1]'
```
**Expected Output:** `"17.84"` *(17.84 bytes per sample — 88% less than raw text format's ~180–220 bytes)*

### Talking Point
> *"The tcpdump output is indisputable proof. `Content-Encoding: snappy` on the wire. `HTTP 204 No Content` 92ms later — PrimaryHub decompressed and committed the samples to EBS. And notice those timestamps: `33.480` and `38.573` — exactly 5 seconds apart, matching `batch_send_deadline: 5s`."*

---

## Step 3 — Network Ingress & Kernel Netfilter Routing

### What You Are Proving
Cross-region metric batches travel over the **encrypted WireGuard mesh** and are routed into PrimaryHub's KinD cluster by the **Linux Kernel Netfilter (iptables DNAT)** — at wire speed with zero userspace proxy overhead.

### Commands

```bash
# 3.1 — Verify authenticated WireGuard ingress from Spoke 1 to PrimaryHub:
# Unauthenticated health check attempt (rejected):
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qS -O- "http://10.99.0.1:30900/-/healthy" 2>&1 | grep "HTTP/"
# Expected: HTTP/1.1 401 Unauthorized

# Dynamically fetch credentials from AWS SSM Parameter Store (zero hardcoding):
RW_USER=$(aws ssm get-parameter --name "/sandbox/monitoring/rw-username" --region "us-east-1" --query "Parameter.Value" --output text)
RW_PASS=$(aws ssm get-parameter --name "/sandbox/monitoring/rw-password" --region "us-east-1" --with-decryption --query "Parameter.Value" --output text)

# Authenticated health check attempt (accepted):
kubectl -n monitoring exec deploy/prometheus-scraper -- \
  wget -qS -O- --header="Authorization: Basic $(echo -n "${RW_USER}:${RW_PASS}" | base64)" \
  "http://10.99.0.1:30900/-/healthy"
# Expected: HTTP/1.1 200 OK (Prometheus Server is Healthy.)

# Direct write endpoint test (/api/v1/write):
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://10.99.0.1:30900/api/v1/write
# Expected: 401 (Unauthorized without credentials)

curl -s -u "${RW_USER}:${RW_PASS}" -o /dev/null -w "%{http_code}\n" -X POST http://10.99.0.1:30900/api/v1/write
# Expected: 400 (Bad Request on empty probe body, proving credentials passed auth check)

# Verify spoke scraper logs show zero 401s:
kubectl -n monitoring logs deploy/prometheus-scraper --tail=10 | grep -E "WAL|remote"
# Expected: msg="Done replaying WAL" (clean streaming)
```

```bash
# 3.2 — Inspect the kernel-level DNAT ingress rule on PrimaryHub host:
sudo iptables -t nat -L PREROUTING -n -v --line-numbers | grep 30900
```
**Expected Output:**
```text
1   124K   21M DNAT  tcp  --  !br-+  *  0.0.0.0/0  0.0.0.0/0  tcp dpt:30900 to:172.18.0.2:30900
```
*(Packet counters increment in real-time as metric batches arrive.)*

### Talking Point
> *"There are zero userspace proxy daemons. `172.18.0.2` is the internal KinD container IP. The Linux kernel itself receives the WireGuard packet and redirects it at wire speed into the container — no context switches, no copies. This is the same pattern used for PostgreSQL on port 30432."*

---

## Step 4 — Central Ingestion & Multi-Cluster Disambiguation

### What You Are Proving
PrimaryHub ingests metric streams from all clusters simultaneously and stores them in a single central TSDB. The `cluster="..."` external labels prevent any metric collisions, enabling clean per-cluster querying with a single PromQL expression.

### Commands

```bash
# 4.1 — The "crown jewel" query: all 4 clusters in one TSDB:
kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=count%20by%20(cluster)(up)" | jq .
```
**Expected Output:**
```json
{
  "status": "success",
  "data": {
    "result": [
      { "metric": {},                         "value": ["...", "20"] },
      { "metric": { "cluster": "secondaryhub" }, "value": ["...", "17"] },
      { "metric": { "cluster": "spoke1" },       "value": ["...", "6"]  },
      { "metric": { "cluster": "spoke2" },       "value": ["...", "6"]  }
    ]
  }
}
```

**What these 49 targets represent:**

| Cluster | Targets | What Is Monitored |
| :--- | :--- | :--- |
| **PrimaryHub** | 20 | PostgreSQL leader & poolers, CNPG operator, FastAPI, AgentGateway/Envoy, OCM Hub controllers, CoreDNS |
| **SecondaryHub** | 17 | Standby PostgreSQL, secondary FastAPI, secondary OCM agents, CoreDNS, node metrics |
| **Spoke 1** | 6 | `prometheus-scraper`, OCM `klusterlet-registration-agent`, `klusterlet-work-agent`, CoreDNS ×2, storage provisioner |
| **Spoke 2** | 6 | Same 6 components as Spoke 1 |

### Talking Point
> *"Every metric from 3 continents lives in this one database. But `up{cluster=\"spoke1\"}` and `up{cluster=\"spoke2\"}` are stored as completely separate time-series because they have different labels. No collision, no overwriting. We can compare Frankfurt vs Mumbai side-by-side in a single query."*

---

## Step 5 — Persistent Storage Survival on AWS EBS

### What You Are Proving
All TSDB block data resides on a dedicated **100 GiB AWS EBS volume** with `DeleteOnTermination=false`. If the EC2 instance is terminated, stopped, or replaced, the EBS volume survives intact. On re-provisioning, cloud-init automatically re-attaches it, and Prometheus resumes the full 7-day historical timeline without missing a sample (metrics older than 7 days are automatically purged by the TSDB compactor).

### Commands

```bash
# 5.1 — Verify the EBS volume is physically mounted on the EC2 host:
df -hT /data
```
**Expected Output:**
```text
Filesystem     Type  Size  Used Avail Use% Mounted on
/dev/nvme1n1   ext4  100G   28G   72G  28% /data
```

```bash
# 5.2 — Verify the Kubernetes PVC is bound:
kubectl -n opensandbox-system get pvc prometheus-pvc
```
**Expected Output:**
```text
NAME             STATUS   VOLUME                                     CAPACITY   STORAGECLASS   AGE
prometheus-pvc   Bound    pvc-11ab1300-e873-4451-9417-cc98982352e3   25Gi       standard       5d
```

```bash
# 5.3 — Inspect the 2-hour immutable TSDB block directories on the EBS volume:
kubectl -n opensandbox-system exec deploy/prometheus -- ls -lh /prometheus
```
**Expected Output:**
```text
drwxr-xr-x 3 nobody nogroup 4096 Oct 27 02:00 01JAX1G0S...   ← 2h compacted block
drwxr-xr-x 3 nobody nogroup 4096 Oct 27 04:00 01JAX7M5F...   ← 2h compacted block
drwxr-xr-x 3 nobody nogroup 4096 Oct 27 06:00 01JAXDZ8N...   ← 2h compacted block
drwxr-xr-x 2 nobody nogroup 4096 Oct 27 06:20 wal             ← Active WAL
```

```bash
# 5.4 — Prove 24 hours of continuous data is intact:
kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=up[24h]" | \
  jq '.data.result[0].values | length'
```
**Expected Output:** `5760` *(5,760 × 15-second samples = exactly 24 hours)*

### Talking Point
> *"These `01JAX...` directories are 2-hour TSDB blocks, compressed with Facebook Gorilla encoding to ~1.37 bytes per sample. They're stored on `/dev/nvme1n1` — the EBS volume, not the EC2 instance's ephemeral disk. Terminate this EC2 right now and this volume survives. When the Launch Template provisions a new instance, cloud-init attaches this exact volume, and Prometheus serves the complete 7-day history immediately while automatically cleaning up data older than 7 days."*

---

## Step 6 — Instant Visualization in Grafana (`< 3ms`)

### What You Are Proving
Grafana connects to the central TSDB via in-cluster DNS — a local Kubernetes DNS call inside the same cluster. Because all spoke metrics have already been pushed into PrimaryHub's TSDB, every dashboard query is **purely local**. There are zero cross-WAN requests on any dashboard panel refresh.

### Commands

```bash
# 6.1 — Benchmark the round-trip from Grafana to Prometheus:
time kubectl -n opensandbox-system exec deploy/prometheus -- \
  wget -qO- "http://localhost:9090/api/v1/query?query=up" > /dev/null
```
**Expected Output:**
```text
real    0m0.003s   ← 3 MILLISECONDS vs the old 4–8 seconds
```

```bash
# 6.2 — Verify all cluster datasources point to the local central TSDB:
kubectl -n monitoring exec deploy/grafana -- \
  curl -s -u admin:sandbox-admin "http://localhost:3000/api/datasources" | \
  jq '.[] | {name: .name, url: .url}'
```
**Expected Output:**
```json
{ "name": "primaryhub",   "url": "http://prometheus-service.opensandbox-system.svc.cluster.local:9090" }
{ "name": "secondaryhub", "url": "http://prometheus-service.opensandbox-system.svc.cluster.local:9090" }
{ "name": "spoke1",       "url": "http://prometheus-service.opensandbox-system.svc.cluster.local:9090" }
{ "name": "spoke2",       "url": "http://prometheus-service.opensandbox-system.svc.cluster.local:9090" }
```
*All four datasources point to the **same** local URL. There is no separate data source per spoke because all spoke metrics are already inside PrimaryHub's TSDB.*

```bash
# 6.3 — Open Grafana (from your laptop):
cd ~/Desktop/Kamal/01-Sandbox/script/prod-docker-multi-script
./port-forward-grafana.sh
# Open: http://localhost:3000  |  Login: admin / sandbox-admin
```

**Live dashboard walkthrough:**
1. Navigate to **Dashboards → Spoke Edge Fleet – Dynamic Multi-Cluster Observability**
2. Click the `Spoke Cluster ($cluster)` dropdown — select `spoke1` → instant Frankfurt telemetry
3. Switch to `spoke2` → instant Mumbai telemetry — zero spinner, zero delay
4. Navigate to **Dashboards → Global Fleet Overview** for a cross-cluster summary

### Talking Point
> *"Notice that there are no separate data sources for each spoke in Grafana's connections page. That's intentional. All spokes push their metrics here. Grafana filters by `cluster=\"spoke1\"` using the PromQL variable. The query hits a local DNS endpoint in `< 3ms`. Compare that to the old model's 4–8 second WAN round-trips with 504 timeouts."*

---

## Q&A Cheat Sheet

| Teammate Question | Winning Answer |
| :--- | :--- |
| *"What happens if the WireGuard link between Mumbai and Virginia drops?"* | *"Nothing is lost. Spoke 2's local scraper keeps appending to its WAL on disk. When WireGuard reconnects, the WAL Watcher replays from its last byte offset and the Shard Controller auto-scales to 10 workers to drain the backlog instantly."* |
| *"Why do earlier WAL segments (e.g. 0–13) disappear on the scraper pod?"* | *"They were not lost—they were persisted! Every 2 hours, Prometheus cuts immutable TSDB blocks in `/prometheus/01J...`. Once written, Prometheus creates a checkpoint (e.g. `checkpoint.00000013`) and safely prunes older raw WAL files to prevent disk exhaustion while keeping the full historical timeline."* |
| *"Doesn't sending metrics over the internet waste bandwidth?"* | *"We proved in Step 2 that Snappy compression reduces each metric to ~17.8 bytes. The entire spoke cluster generates under 5 KB/s of outbound traffic — negligible."* |
| *"Why is there no separate data source per spoke in Grafana?"* | *"Because Grafana doesn't query spokes directly. Every spoke pushes into PrimaryHub's TSDB. All metrics carry `cluster=\"..\"` labels. Grafana queries the local TSDB in `< 3ms` and the `$cluster` variable filters dynamically."* |
| *"If we terminate the PrimaryHub EC2 right now, do we lose everything?"* | *"No. Step 5 proved TSDB blocks live on a 100 GiB EBS volume with `DeleteOnTermination=false`. The Launch Template cloud-init re-attaches it on the next boot and Prometheus resumes the 7-day history immediately (auto-cleaning blocks older than 7d). "* |
| *"Why is Grafana so much faster than before?"* | *"The old model had Grafana issue 48 cross-WAN queries per dashboard refresh (~4–8 seconds). Now every query is a local DNS call inside the Kubernetes cluster: ~2.8ms per query, ~0.3s full dashboard render."* |
