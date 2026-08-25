# 01-Sandbox: Updated Multi-Cluster Architecture, Component Strategy & High Availability Guide

---

### Option: 1: Real-Time Data Synchronization Across Hub Nodes (Node 1 ↔ Node 2 ↔ Node 3)

To ensure that **if Node 1 goes down, Node 2 (or Node 3) immediately takes over and serves the application with zero data loss**, data is continuously synchronized across all 3 nodes in real-time using native in-cluster replication engines:

```
                  ┌─────────────────────────────────────────────────────────┐
                  │          SINGLE HUB CLUSTER (bb-mp-plat-03)           │
                  │              (3-Node RKE2 HA Cluster)                   │
                  ├─────────────────────────────────────────────────────────┤
                  │                                                         │
                  │  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐ │
                  │  │ Node 1       │   │ Node 2       │   │ Node 3       │ │
                  │  │ (Master/Wrk) │   │ (Master/Wrk) │   │ (Master/Wrk) │ │
                  │  ├──────────────┤   ├──────────────┤   ├──────────────┤ │
                  │  │ API Pod 1    │   │ API Pod 2    │   │ API Pod 3    │ │
                  │  │ CNPG Primary │◄─►│ CNPG Standby │◄─►│ CNPG Standby │ │
                  │  │ RMQ Leader   │◄─►│ RMQ Follower │◄─►│ RMQ Follower │ │
                  │  └──────────────┘   └──────────────┘   └──────────────┘ │
                  └────────────────────────────┬────────────────────────────┘
                                               │
                                               │ Cilium ClusterMesh (WireGuard)
                                               ▼
                                  ┌──────────────────────────┐
                                  │   SPOKE WORKER CLUSTERS  │
                                  │   (Pure Stateless Workers│
                                  │    No Local Fallback)    │
                                  └──────────────────────────┘
```

#### 1. PostgreSQL Real-Time Data Sync (CloudNativePG Operator)
*   **How Data Syncs:** CloudNativePG runs **1 Primary instance** (on Node 1) and **2 Standby Replicas** (on Node 2 and Node 3).
*   **Replication Engine:** Every `INSERT`, `UPDATE`, or scan result committed on Node 1 is continuously streamed in real-time to Node 2 and Node 3 via PostgreSQL **Streaming Replication (Write-Ahead Logs - WAL)**.
*   **Automatic Failover When Node 1 Fails:**
    1. The CNPG controller detects Node 1 failure within **~3 to 5 seconds**.
    2. CNPG automatically promotes the Standby Replica on Node 2 (or Node 3) to become the new **Primary Master**.
    3. The internal Service IP (`postgresql-service`) updates in real time to point to Node 2.
    4. All worker reads and writes resume instantly with **zero data loss**.

#### 2. RabbitMQ Real-Time Data Sync (Quorum Queues / Raft Consensus)
*   **How Data Syncs:** RabbitMQ runs 3 pods (1 on Node 1, 1 on Node 2, 1 on Node 3) configured with **Quorum Queues**.
*   **Replication Engine:** When a scan task message is published to the leader queue on Node 1, it is synchronously replicated to the follower queue instances on Node 2 and Node 3 using the **Raft consensus algorithm** before sending an acknowledgment.
*   **Automatic Failover When Node 1 Fails:**
    1. The Raft consensus protocol detects Node 1 failure instantly.
    2. The follower pod on Node 2 (or Node 3) is automatically elected as the new **Queue Leader**.
    3. Zero pending scan messages are lost, and Spoke workers continue consuming tasks seamlessly.

#### 3. Redis Real-Time Data Sync (Redis Sentinel / Replication)
*   **How Data Syncs:** Redis Master runs on Node 1 with active replication to Redis Replicas on Node 2 and Node 3.
*   **Automatic Failover When Node 1 Fails:** Redis Sentinel detects Master node loss, executes automated failover, and promotes Node 2 to Master.

#### 4. API Server Real-Time High Availability (`sandbox-api`)
*   **Stateless Execution:** `sandbox-api` runs `replicas: 3` (1 pod on each node).
*   **Automatic Failover When Node 1 Fails:** MetalLB Ingress instantly routes incoming client HTTP traffic to the active pods on Node 2 and Node 3 with **zero client downtime**.
---

### In-Cluster High Availability Summary

| Core Component | Replication Strategy Across Nodes (Node 1 ↔ Node 2 ↔ Node 3) | Failover Behavior (If Node 1 Fails) | Data Loss Risk |
|:---|:---|:---|:---:|
| **PostgreSQL** | Continuous Streaming Replication (CNPG WAL sync) | Node 2/3 promoted to Primary in ~3–5s | **Zero Data Loss** |
| **RabbitMQ** | Synchronous Raft Consensus (Quorum Queues) | Node 2/3 follower promoted to Leader instantly | **Zero Message Loss** |
| **Redis** | Redis Sentinel active replication | Node 2/3 promoted to Master automatically | Minimal (Ephemeral cache) |
| **API Server** | Stateless Pod Anti-Affinity (`replicas: 3`) | MetalLB routes to active Node 2/3 pods | **Zero Downtime** |
| **Spoke Workers** | Pure Stateless Execution (Direct Hub Connection) | Retries connection to Hub during 5s failover | None (Stateless) |

---
## Option 2: Active-Passive Dual-Hub Cluster Architecture Blueprint

### 1 High-Level Dual-Hub System Topology

In **Option 2**, the architecture deploys two independent Hub clusters:
1. **Primary Hub Cluster (`bb-mp-plat-03` — Active):** Serves 100% of live client API traffic and manages active OCM workload placement to Spokes.
2. **Secondary Standby Hub Cluster (`bb-mp-plat-04` — Passive / Backup):** Equipped with identical core services (`sandbox-api`, PostgreSQL, RabbitMQ, Redis, OCM Control Plane). Continuously receives real-time data replication from the Primary Hub and remains on hot-standby.

```
                    ┌─────────────────────────────────────────┐
                    │   GLOBAL INGRESS / GSLB LOAD BALANCER   │
                    │   (Cloudflare GSLB / Anycast BGP DNS)   │
                    └───────────┬─────────────────┬───────────┘
                                │                 │
                      Active    │ Primary         │ Backup   Passive
                      Route     │ (Healthy)       │ (Hot Standby)
                                ▼                 ▼
                   ┌───────────────────┐   ┌───────────────────┐
                   │  PRIMARY HUB      │   │  SECONDARY HUB    │
                   │  bb-mp-plat-03    │   │  bb-mp-plat-04    │
                   │  ├── sandbox-api  │   │  ├── sandbox-api  │
                   │  ├── PostgreSQL   ├──►│  ├── PostgreSQL   │ (CNPG Cross-Cluster Replica)
                   │  ├── RabbitMQ     ├──►│  ├── RabbitMQ     │ (RabbitMQ Federation/Shovel)
                   │  ├── Redis Master ├──►│  ├── Redis Replica│ (Async Replication)
                   │  └── OCM Leader   │   │  └── OCM Standby  │ (Multi-Hub Klusterlet)
                   └─────────┬─────────┘   └─────────┬─────────┘
                             │                       │
                             │ Primary WireGuard     │ Backup WireGuard
                             │ Mesh Tunnel           │ Mesh Tunnel
                             ▼                       ▼
                   ┌───────────────────────────────────────────┐
                   │           SPOKE WORKER CLUSTERS           │
                   │   (kind-east, kind-west, RKE2 Spokes)     │
                   └───────────────────────────────────────────┘
```

---

### 2. Core Components & Synchronization Strategies for Dual-Hub Setup

To make the Secondary Hub cluster fully ready to take over if the Primary Hub goes down, five core synchronization strategies are implemented:

#### 1. Ingress & Traffic Failover Strategy (Global Ingress / GSLB Anycast DNS)
*   **Components:** Cloudflare GSLB / Route53 DNS / ExternalDNS + MetalLB Ingress Gateway on both Hubs (`10.0.8.9` for Primary, `10.0.8.10` for Secondary).
*   **How it Works:**
    - Client applications send API requests to `api.sandbox.local`.
    - GSLB monitors the health of Primary Hub (`https://10.0.8.9/healthz`) every 5 seconds.
    - **Failover Action:** If Primary Hub fails 3 consecutive health probes (15 seconds total), GSLB automatically updates DNS resolution so `api.sandbox.local` points to Secondary Hub Ingress (`10.0.8.10`). Clients seamlessly redirect to Secondary `sandbox-api`.

#### 2. PostgreSQL Cross-Cluster Real-Time Data Sync (CloudNativePG Cross-Cluster Replica)
*   **Components:** CloudNativePG (CNPG) Operator running on both Hubs connected via Cilium ClusterMesh WireGuard tunnel (`cilium_wg0`).
*   **How it Works:**
    - **Primary Hub:** Runs CNPG `Cluster` named `postgresql-primary` (Read-Write Mode).
    - **Secondary Hub:** Runs CNPG `replica` mode `Cluster` named `postgresql-secondary`.
    - Every transaction committed on Primary PostgreSQL is continuously streamed in near-real-time across the cross-cluster WireGuard tunnel to Secondary PostgreSQL using **Write-Ahead Log (WAL) Streaming Replication**.
    - **Failover Action:** When Primary Hub crashes, an automated script (or operator trigger) on Secondary Hub executes: `cnpg promote postgresql-secondary`. The Secondary database immediately transitions from standby to active Read-Write Primary.

#### 3. RabbitMQ Queue Synchronization Strategy (RabbitMQ Federation & Shovel)
*   **Components:** RabbitMQ Federation Plugin / Shovel Plugin over TLS (`amqps://`).
*   **How it Works:**
    - Primary Hub RabbitMQ handles live scan task publishing.
    - Secondary Hub RabbitMQ runs a matching queue structure configured with the **RabbitMQ Federation Plugin**. The federation plugin mirrors published messages across clusters to the backup queue.
    - **Failover Action:** When clients redirect to Secondary Hub, `sandbox-api` publishes new task messages to Secondary RabbitMQ. Spoke workers switch to consuming from Secondary RabbitMQ endpoints (`rabbitmq-backup.opensandbox-system.svc.clusterset.local`).

#### 4. Redis Active State Replication Strategy
*   **Components:** Redis Operator with Cross-Cluster Replication.
*   **How it Works:** Primary Hub Redis Master replicates rate limit counters and API key quota states asynchronously to Secondary Hub Redis Replica over encrypted WireGuard tunnel. Upon failover, Secondary Redis is promoted to Master.

#### 5. Open Cluster Management (OCM) Multi-Hub Registration & Workload Dispatch Failover
*   **Components:** OCM Multi-Hub Registration (`Klusterlet` dual-registration).
*   **How it Works:**
    - Spoke `Klusterlet` agents are registered with **both** Primary Hub (`bb-mp-plat-03`) and Secondary Hub (`bb-mp-plat-04`).
    - Under normal operation, Primary Hub OCM evaluates `Placement` rules and dispatches `ManifestWork` specs to Spokes.
    - **Failover Action:** When Primary Hub becomes unreachable, Secondary Hub OCM takes over placement scoring, dispatching microVM worker specs (`ManifestWork`) to Spokes.

---

### 3. End-to-End Failover Sequence (Primary Hub Crash → Secondary Hub Activation)

```mermaid
sequenceDiagram
    autonumber
    participant Client as External Client / UI
    participant GSLB as Global Ingress / GSLB DNS
    participant PriHub as Primary Hub (bb-mp-plat-03)
    participant SecHub as Secondary Hub (bb-mp-plat-04)
    participant Spokes as Spoke Worker Clusters

    Note over PriHub: Primary Hub operates normally (100% traffic)
    Client->>GSLB: HTTP POST /api/v1/scan
    GSLB->>PriHub: Route to Primary API (10.0.8.9)
    PriHub->>PriHub: Write DB & publish task to RabbitMQ
    PriHub-->>SecHub: WAL Streaming DB sync & RabbitMQ Federation

    Note over PriHub: 💥 CRASH: Primary Hub hardware / network failure!
    GSLB->>PriHub: Health check probe https://10.0.8.9/healthz (FAILED)
    GSLB->>GSLB: Failover threshold reached (3 failed probes / 15s)

    Note over GSLB: GSLB updates DNS: api.sandbox.local → 10.0.8.10 (Secondary)
    SecHub->>SecHub: CNPG promote postgresql-secondary → Read/Write Primary
    SecHub->>SecHub: Activate Secondary RabbitMQ broker & OCM Placement engine

    Client->>GSLB: HTTP POST /api/v1/scan
    GSLB->>SecHub: Route to Secondary API (10.0.8.10)
    SecHub-->>Client: HTTP 202 Accepted {job_id: "scan-9920"}

    SecHub->>Spokes: Secondary OCM dispatches ManifestWork to Spokes
    Spokes->>SecHub: Workers consume tasks from Secondary RabbitMQ & write to Secondary DB
```

---

### Summary of Dual-Hub High Availability

| Core Component | Primary Hub (`bb-mp-plat-03`) | Secondary Hub (`bb-mp-plat-04`) | Synchronization Engine | Failover Trigger & Action |
|:---|:---|:---|:---|:---|
| **API Ingress** | Active (`10.0.8.9`) | Hot Standby (`10.0.8.10`) | GSLB / Anycast DNS | GSLB probes fail 3x → Shifts DNS to `10.0.8.10` |
| **PostgreSQL** | Primary (Read-Write) | Standby Replica | CNPG Streaming WAL Replication | CNPG promote command → Promotes replica to Read-Write |
| **RabbitMQ** | Active Broker Leader | Active Federation Backup | RabbitMQ Federation / Shovel | Ingress redirection → Clients/Workers switch to Sec AMQP |
| **Redis** | Active Master | Standby Replica | Redis Cross-Cluster Replication | Redis Sentinel / Operator promotes Standby to Master |
| **OCM Control Plane**| Active Fleet Dispatcher | Standby Fleet Dispatcher | Dual-Hub Klusterlet Registration | Primary unreachable → Sec OCM dispatches `ManifestWork` |

---

## Comprehensive Multi-Cluster Technology Comparison Matrix

Below is the side-by-side evaluation comparing **Option 2: Active-Passive Dual-Hub Stack** against **Option 1: Single-Hub 3-Node HA**:

| Evaluation Dimension | Option 2: Active-Passive Dual-Hub Stack (Recommended) | Option 1: Single-Hub 3-Node In-Cluster HA |
|:---|:---|:---|
| **Layer 1: Network Fabric** | **Cilium ClusterMesh (eBPF + WireGuard)** | Cilium ClusterMesh (eBPF + WireGuard) |
| **Layer 2: Service Discovery** | **Kubernetes MCS API (KEP-1645)** | Kubernetes MCS API (KEP-1645) |
| **Layer 3: Fleet Scheduler** | **Open Cluster Management (OCM)** | Open Cluster Management (OCM) |
| **Data Path Latency** | **< 0.2 ms (Kernel eBPF)** | < 0.2 ms (Kernel eBPF) |
| **Hub SPOF Resilience** | **✅ Maximum (Full Secondary Standby Hub)** | ✅ High (3-Node In-Cluster HA)|
| **Database Sync** | **✅ CNPG Cross-Cluster WAL Streaming** | In-Cluster CNPG WAL Sync |
| **Queue Sync** | **✅ RabbitMQ Federation / Shovel** | In-Cluster Quorum Queues |
| **Operational Footprint** | **Medium-High (2 Hub Clusters)** | Low (1 Hub Cluster) |
| **Key Pros** | • **Datacenter/Site Loss Protection:** Withstands complete primary datacenter crash.<br>• **Cross-Region DR:** Allows active-passive placement across separate facilities.<br>• **Zero Job Loss:** Cross-cluster WAL and federation sync preserve all scan state. | • **Low Cost & Complexity:** Single cluster footprint using native K8s primitives.<br>• **Fast Local Failover:** Node crash failover in ~3–5 seconds over local LAN.<br>• **Zero Cross-Cluster Sync Latency:** Data sync happens locally within Hub. |
| **Key Cons** | • **Higher Resource Cost:** Requires 2 full Hub cluster footprints.<br>• **Cross-Cluster Link Reliance:** Sync relies on stable WireGuard mesh connection.<br>• **Setup Overhead:** Requires GSLB health check and federation config. | • **Vulnerable to Total Site Loss:** Datacenter outage brings platform down.<br>• **No Cross-Region DR:** Cannot fail over to secondary facility without backups. |

---

### Detailed Pros and Cons Breakdown

#### Option 2: Active-Passive Dual-Hub Stack (Recommended)

##### 🟢 Pros:
1. **Maximum High Availability & Site Fault Tolerance:** If the entire Primary Hub cluster (`bb-mp-plat-03`) or its hosting datacenter suffers a catastrophic outage, the Secondary Hub (`bb-mp-plat-04`) takes over seamlessly.
2. **Cross-Region / Multi-Datacenter Disaster Recovery:** Primary and Secondary Hubs can be geographically separated across cloud providers or physical facilities.
3. **Automated Client & Worker Redirection:** GSLB DNS health probes automatically shift client API calls to Secondary Hub (`10.0.8.10`) within 15 seconds.
4. **Data Protection:** PostgreSQL WAL streaming replication and RabbitMQ Federation ensure scan reports and task payloads are safely replicated to the Secondary Hub before failover.

##### 🔴 Cons:
1. **Higher Infrastructure Overhead & Cost:** Requires deploying and maintaining two independent Hub clusters, doubling control plane resource consumption.
2. **Cross-Cluster Link Dependency:** Data replication depends on an active, encrypted WireGuard mesh link (`cilium_wg0`) between Hubs.
3. **Operational Configuration:** Requires setting up GSLB health monitoring probes, CNPG cross-cluster replica manifests, and RabbitMQ federation plugins.

---

#### Option 1: Single-Hub 3-Node In-Cluster HA

##### 🟢 Pros:
1. **Minimal Operational Footprint:** Standard single-cluster RKE2 setup (`bb-mp-plat-03`) running 3 control-plane/worker nodes with embedded etcd.
2. **Low Cost:** Requires only one Hub cluster footprint without needing a second standby cluster.
3. **Sub-Second Local Failover:** Node failures trigger in-cluster pod rescheduling and CNPG master promotion in ~3 to 5 seconds over local high-speed LAN.
4. **Zero Cross-Cluster Sync Overhead:** PostgreSQL WAL streaming and RabbitMQ Raft consensus operate locally within the Hub network.

##### 🔴 Cons:
1. **Vulnerable to Total Datacenter Outage:** If the entire physical server facility or hosting rack goes offline, 01-Sandbox stops scanning until hardware is restored.
2. **No Multi-Facility Failover:** Lacks a standby cluster in a secondary facility to serve traffic during primary site maintenance or disasters.


---

## 1. Clean Hub & Spoke Traffic Flow Diagram

Below is the strictly downward-flowing Mermaid flowchart built with standard syntax:

```mermaid
flowchart TD
    %% Node Styling Classes
    classDef hubNode fill:#FFF8E1,stroke:#FFA000,stroke-width:2px,color:#000;
    classDef spokeNode fill:#E8F5E9,stroke:#388E3C,stroke-width:2px,color:#000;
    classDef mcsNode fill:#E1F5FE,stroke:#0288D1,stroke-width:2px,color:#000;
    classDef meshNode fill:#EDE7F6,stroke:#7B1FA2,stroke-width:2px,color:#000;

    %% ------------------------------------------------───────────
    %% STAGE 1: INGESTION & SCHEDULING (HUB)
    %% ------------------------------------------------───────────
    subgraph HUB_INGRESS ["1. HUB CLUSTER (bb-mp-plat-03) — Ingestion & Scheduling"]
        API["Step 1: sandbox-api<br/>(FastAPI Request Ingestion)"]
        RMQ[("Step 2: Hub RabbitMQ Queue<br/>(repo_scan_queue)")]
        OCM["Step 3: OCM Placement
        Engine<br/>(Evaluates Free CPU/RAM
        Telemetry)"]

        API -->|Publish Task| RMQ
        RMQ -.->|Trigger Placement| OCM
    end

    %% ------------------------------------------------───────────
    %% STAGE 2: WORKER PROVISIONING (SPOKE)
    %% ------------------------------------------------───────────
    subgraph SPOKE_LAYER ["2. SPOKE WORKER CLUSTERS — Isolated Hardware Execution"]
        KLUSTERLET["Step 4a: Spoke East Klusterlet Agent"]
        WORKER_E["Step 4b: Kata / gVisor MicroVM Worker<br/>(Executes Scan in KVM Sandbox)"]
        IDLE_W["Spoke West Cluster<br/>(Standby / Spared)"]

        KLUSTERLET -->|Boots Sandbox| WORKER_E
    end

    %% ------------------------------------------------───────────
    %% STAGE 3: SERVICE DISCOVERY & CONNECTIVITY
    %% ------------------------------------------------───────────
    subgraph MESH_LAYER ["3. CROSS-CLUSTER SERVICE DISCOVERY & DATA MESH"]
        MCS_DNS["Step 5a: MCS API DNS (.svc.clusterset.local)"]
        VIP_IP["Step 5b: Virtual ClusterSetIP (10.96.5.100)"]
        EBPF_TUNNEL["Step 5c: Cilium eBPF WireGuard Tunnel (cilium_wg0)"]

        MCS_DNS -->|Returns VIP| VIP_IP
        VIP_IP -->|Intercepts Socket| EBPF_TUNNEL
    end

    %% ------------------------------------------------───────────
    %% STAGE 4: PERSISTENCE (HUB RESULT STORE)
    %% ------------------------------------------------───────────
    subgraph HUB_STORE ["4. HUB CLUSTER — Data Persistence & Teardown"]
        DB[("Step 6: Hub PostgreSQL Database
        <br/>(Stores Scan Reports & Findings)")]
    end

    %% ------------------------------------------------───────────
    %% DOWNWARD ARROWS ONLY (STRICT RANK ORDERING)
    %% ------------------------------------------------───────────
    OCM -->|Dispatch ManifestWork Spec| KLUSTERLET


    WORKER_E -->|Resolve rabbitmq-service| MCS_DNS
    EBPF_TUNNEL -.->|Pull AMQP Task Payload| RMQ
    WORKER_E -->|Write Scan Report| EBPF_TUNNEL
    EBPF_TUNNEL -->|Save Findings| DB

    %% Apply Styles
    class API,RMQ,OCM,DB hubNode;
    class KLUSTERLET,WORKER_E,IDLE_W spokeNode;
    class MCS_DNS,VIP_IP mcsNode;
    class EBPF_TUNNEL meshNode;
```

---

## 2. Text Architecture Diagram (Guaranteed 100% Clean Rendering)

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ [STEP 1] CLIENT SUBMISSION                                                              │
│ User HTTP Request ──► Hub sandbox-api (10.0.8.9) ──► Validates Rate Limits & Quotas    │
└──────────────────────────────────────────┬──────────────────────────────────────────────┘
                                           │ [Step 1.1] Publishes Task Payload
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ [STEP 2] HUB RABBITMQ QUEUE                                                             │
│ Holds persistent task payloads in repo_scan_queue (ServiceExport declared)              │
└──────────────────────────────────────────┬──────────────────────────────────────────────┘
                                           │ [Step 2.1] Telemetry Trigger
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ [STEP 3] OCM FLEET SCHEDULER                                                            │
│ Evaluates Spoke heartbeats (East 23% CPU vs West 71% CPU) ──► Selects Spoke East        │
└──────────────────────────────────────────┬──────────────────────────────────────────────┘
                                           │ [Step 3.1] Dispatches ManifestWork Spec
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ [STEP 4] SPOKE EAST (kind-east) & KATA MICROVM                                          │
│ Klusterlet receives ManifestWork ──► Boots Firecracker KVM MicroVM sandbox (<800ms)     │
└──────────────────────────────────────────┬──────────────────────────────────────────────┘
                                           │ [Step 5.1] Resolves MCS DNS (.svc.clusterset.local)
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ [STEP 5] MCS API DISCOVERY & EBPF WIREGUARD MESH                                        │
│ MCS DNS returns VIP 10.96.5.100 ──► Cilium eBPF translates VIP to Hub Pod IP 10.42.3.15│
│ Transport: Encrypted kernel WireGuard tunnel (cilium_wg0) ──► Pulls AMQP task payload  │
└──────────────────────────────────────────┬──────────────────────────────────────────────┘
                                           │ [Step 6.1] Writes Scan Results
                                           ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ [STEP 6] HUB POSTGRESQL DATABASE & TEARDOWN                                             │
│ Report inserted into DB ──► Worker sends AMQP ACK ──► MicroVM pod auto-destroys        │
└──────────────────────────────────────────────────────────┬──────────────────────────────┘
```

---

## 3. Numbered Traffic Flow Breakdown

| Step # | Traffic Stage | Source ──► Destination | Flow Action & Technical Description |
|:---:|:---|:---|:---|
| **`[Step 1]`** | **Client Ingestion** | External Client ──► Hub `sandbox-api` | User sends HTTP POST `/api/v1/scan`. `sandbox-api` validates Redis rate limits & key quotas. |
| **`[Step 2]`** | **Task Enqueueing** | Hub `sandbox-api` ──► Hub RabbitMQ | `sandbox-api` publishes persistent task payload to `repo_scan_queue` and returns `202 Accepted` to client. |
| **`[Step 3]`** | **Spoke Placement** | Hub OCM ──► Spoke East (`kind-east`) | OCM evaluates Spoke heartbeats (East 23% CPU vs West 71% CPU), selects `kind-east`, and dispatches `ManifestWork`. |
| **`[Step 4]`** | **Sandbox Provisioning**| Spoke `Klusterlet` ──► Firecracker MicroVM | Containerd invokes Kata shim + Firecracker, booting a clean KVM microVM (`kata-qemu`) in **<800ms**. |
| **`[Step 5a]`**| **MCS Service Discovery**| MicroVM Worker ──► MCS API DNS Zone | MicroVM queries `rabbitmq-service.opensandbox-system.svc.clusterset.local`. MCS API DNS returns Virtual `ClusterSetIP` (`10.96.5.100`). |
| **`[Step 5b]`**| **eBPF Mesh Connection**| MicroVM Worker ──► Hub RabbitMQ | Cilium `sock_ops` eBPF intercepts connection to `10.96.5.100`, maps it to Hub Pod (`10.42.3.15`), and pulls tasks via WireGuard (`basic.qos=3`). |
| **`[Step 6]`** | **Result Persistence** | MicroVM Worker ──► Hub PostgreSQL DB | Worker resolves `postgresql-service...svc.clusterset.local`, connects via eBPF WireGuard mesh, and inserts findings into DB. |
| **`[Step 7]`** | **Teardown & Cleanup** | MicroVM Worker ──► Hub OCM | Worker sends AMQP ACK. OCM deletes `ManifestWork` spec, destroying the Firecracker microVM (0 idle footprint). |

---

# Option 2: Active-Passive Dual-Hub Stack — Full Setup Guide

> This guide walks you through building the Active-Passive Dual-Hub architecture from scratch. **Neither hub cluster needs to exist yet** — we will provision both `bb-mp-plat-03` (Primary Hub) and `bb-mp-plat-04` (Secondary Hub) step by step.

---

## 🗺️ Big Picture: What We're Building

```
  CLIENTS / API USERS
         │
         ▼
  ┌─────────────────────┐
  │  GSLB / DNS         │  ← Watches health. Switches traffic on failover.
  │  api.sandbox.local  │
  └─────┬─────────┬─────┘
        │         │
        ▼         ▼ (only if Primary fails)
  ┌───────────┐  ┌───────────┐
  │ PRIMARY   │  │ SECONDARY │
  │ HUB       │  │ HUB       │
  │ plat-03   │  │ plat-04   │
  │ (ACTIVE)  │  │ (STANDBY) │
  └─────┬─────┘  └─────┬─────┘
        │               │
        └───────┬───────┘
                │
                ▼
     ┌─────────────────────┐
     │  SPOKE CLUSTERS     │
     │  kind-east          │
     │  kind-west          │
     │  (Registered to     │
     │   BOTH Hubs)        │
     └─────────────────────┘
```

---

## 📋 Phase Overview (Detailed Implementation Breakdown)

| Phase | Core Action & Target Clusters | Key Technology / Tooling | Operational & High-Availability Details |
|:---:|:---|:---|:---|
| **Phase 1** | **Bootstrap Hub Control Planes**<br/>• Primary Hub (`plat-03` / `10.0.8.9`)<br/>• Secondary Hub (`plat-04` / `10.0.8.10`) | RKE2 (`rke2-server`), Cilium CNI, `kubectl` | Provision two independent Kubernetes control rooms from scratch. Configure isolated `kubeconfig` contexts (`config-plat-03`, `config-plat-04`), verify node health, and create core namespaces (`opensandbox-system`, `open-cluster-management`). |
| **Phase 2** | **Install Open Cluster Management (OCM)**<br/>• Both Hubs (`plat-03` & `plat-04`) | OCM `clusteradm` CLI, Registration Operator | Initialize dual independent OCM control planes on both Hubs using `clusteradm init`. Generate unique `jointoken`s for Spoke onboarding and establish fleet management services. |
| **Phase 3** | **Dual-Hub Spoke Registration**<br/>• Spokes (`kind-east`, `kind-west`) → Both Hubs | OCM `Klusterlet`, `clusteradm join`, `clusteradm accept` | Register Spoke `Klusterlet` agents to **BOTH** Primary and Secondary Hubs simultaneously. Spokes operate in zero-maintenance mode, continuously streaming real-time CPU/RAM allocatable capacity telemetry to both Hubs for placement scoring. |
| **Phase 4** | **Cilium ClusterMesh & WireGuard Tunnel**<br/>• Primary Hub ↔ Secondary Hub network fabric | Cilium CLI, WireGuard Encryption, ClusterMesh | Establish encrypted, high-performance inter-cluster Pod-to-Pod and Service-to-Service network routing across Hubs. Exchange cluster identities, issue CA certs, and validate cross-cluster ping (`10.0.8.9` ↔ `10.0.8.10`). |
| **Phase 5** | **CloudNativePG (CNPG) PostgreSQL Replication**<br/>• Primary DB (`plat-03`) → Secondary DB (`plat-04`) | CloudNativePG Operator, WAL Streaming, ClusterMesh DNS | Deploy Primary CNPG PostgreSQL cluster. Deploy Secondary standby CNPG cluster with `bootstrap.recovery.source` pointing across ClusterMesh to Primary DB for continuous real-time WAL streaming replication. |
| **Phase 6** | **RabbitMQ Cross-Cluster Federation**<br/>• Primary Broker (`plat-03`) ↔ Secondary Downstream (`plat-04`) | RabbitMQ Federation Plugin, AMQP via ClusterMesh | Enable `rabbitmq_federation` plugin. Configure Secondary Hub as a downstream federated exchange to mirror task queue messages (`repo_scan_queue`) from Primary to Secondary in real-time. |
| **Phase 7** | **Redis Cross-Cluster State Replication**<br/>• Primary Master (`plat-03`) → Secondary Replica (`plat-04`) | Redis `replicaof`, ClusterMesh TCP routing | Configure Secondary Redis instance as a live read-only replica (`replicaof redis-primary... 6379`). Continuously mirrors rate-limiting counters, key quotas, and session tokens from Primary to Secondary. |
| **Phase 8** | **Deploy CodeInspector Stack via Helm**<br/>• Primary Hub (`plat-03` - Active)<br/>• Secondary Hub (`plat-04` - Hot Standby) | Helm 3 (`codeInspector/` chart), `values.yaml` | Execute `helm upgrade --install codeinspector ./codeInspector` on both Hubs. Provisions `sandbox-api` (`apiServer`), Agent Gateway, resource pools, and monitoring. Secondary runs warm on standby for zero cold-start failover. |
| **Phase 9** | **GSLB / Health-Check Automated DNS Failover**<br/>• Client Endpoint (`api-sandbox.01security.com`) | CoreDNS / Cloudflare GSLB / Failover CronJob | Point GSLB DNS to Primary IP (`10.0.8.9`). Configure continuous 5s `/healthz` HTTP probes. Automatically update DNS resolution to Secondary IP (`10.0.8.10`) if Primary fails 3 consecutive probes (~15s). |
| **Phase 10** | **End-to-End Failover & Recovery Verification**<br/>• Primary Crash Simulation & Spoke Dispatch | `kubectl cnpg promote`, `curl`, `kubectl get manifestworks` | Simulate Primary failure (`kubectl scale deployment sandbox-api --replicas=0`), confirm GSLB switches to `10.0.8.10`, promote Secondary CNPG DB (`kubectl cnpg promote`), submit `/api/v1/scan`, and verify Spokes receive and execute job from Secondary Hub. |

---

## 🔧 Phase 1: Prepare Both Hub Clusters

### What you're doing
You need two separate, working RKE2 Kubernetes clusters. Think of them as two independent control rooms. **Neither needs to exist yet** — you will build both from scratch in this phase. Run the Primary Hub steps on `bb-mp-plat-03`, then repeat on `bb-mp-plat-04` for the Secondary Hub.

**Machines you need:**
- `bb-mp-plat-03` (`10.0.8.9`) = Primary Hub — built first, serves all live traffic
- `bb-mp-plat-04` (`10.0.8.10`) = Secondary Hub — built second, sits on hot standby

---

### 🖥️ PRIMARY HUB — `bb-mp-plat-03` (`10.0.8.9`)

#### Step 1.1 — Bootstrap RKE2 on Primary Hub (`bb-mp-plat-03`)

SSH into `bb-mp-plat-03` and run:

```bash
# Download and install RKE2
curl -sfL https://get.rke2.io | sh -

# Enable the RKE2 server service
systemctl enable rke2-server.service

# Write the RKE2 config file
mkdir -p /etc/rancher/rke2
cat > /etc/rancher/rke2/config.yaml <<EOF
cluster-name: bb-mp-plat-03
bind-address: 10.0.8.9
advertise-address: 10.0.8.9
cni: cilium
disable-cloud-controller: true
tls-san:
  - 10.0.8.9
  - bb-mp-plat-03
  - kubernetes.default.svc
EOF

# Start RKE2
systemctl start rke2-server.service

# Watch it come up (wait until you see "Node bb-mp-plat-03 status updated")
journalctl -u rke2-server -f
```

#### Step 1.2 — Get the kubeconfig for Primary Hub

Run this on `bb-mp-plat-03` (or copy the file to your local machine):

```bash
mkdir -p ~/.kube
cp /etc/rancher/rke2/rke2.yaml ~/.kube/config-plat-03

# Replace 127.0.0.1 with the real Primary Hub IP
sed -i 's/127.0.0.1/10.0.8.9/g' ~/.kube/config-plat-03
chmod 600 ~/.kube/config-plat-03

# Test connectivity
KUBECONFIG=~/.kube/config-plat-03 kubectl get nodes
```

Expected output:
```
NAME           STATUS   ROLES                  AGE   VERSION
bb-mp-plat-03  Ready    control-plane,master   1m    v1.30.x
```

#### Step 1.3 — Create the application namespace on Primary Hub

```bash
KUBECONFIG=~/.kube/config-plat-03 kubectl create namespace opensandbox-system
```

---

### 🖥️ SECONDARY HUB — `bb-mp-plat-04` (`10.0.8.10`)

#### Step 1.4 — Bootstrap RKE2 on Secondary Hub (`bb-mp-plat-04`)

SSH into `bb-mp-plat-04` and run:

```bash
# Download and install RKE2
curl -sfL https://get.rke2.io | sh -

# Enable the RKE2 server service
systemctl enable rke2-server.service

# Write the RKE2 config file
mkdir -p /etc/rancher/rke2
cat > /etc/rancher/rke2/config.yaml <<EOF
cluster-name: bb-mp-plat-04
bind-address: 10.0.8.10
advertise-address: 10.0.8.10
cni: cilium
disable-cloud-controller: true
tls-san:
  - 10.0.8.10
  - bb-mp-plat-04
  - kubernetes.default.svc
EOF

# Start RKE2
systemctl start rke2-server.service

# Watch it come up
journalctl -u rke2-server -f
```

#### Step 1.5 — Get the kubeconfig for Secondary Hub

```bash
mkdir -p ~/.kube
cp /etc/rancher/rke2/rke2.yaml ~/.kube/config-plat-04

# Replace 127.0.0.1 with the real Secondary Hub IP
sed -i 's/127.0.0.1/10.0.8.10/g' ~/.kube/config-plat-04
chmod 600 ~/.kube/config-plat-04

# Test connectivity
KUBECONFIG=~/.kube/config-plat-04 kubectl get nodes
```

Expected output:
```
NAME           STATUS   ROLES                  AGE   VERSION
bb-mp-plat-04  Ready    control-plane,master   1m    v1.30.x
```

#### Step 1.6 — Create the application namespace on Secondary Hub

```bash
KUBECONFIG=~/.kube/config-plat-04 kubectl create namespace opensandbox-system
```

---

### ✅ Phase 1 Verification — Both Hubs Ready

Run this quick check from your local machine before moving to Phase 2:

```bash
echo "=== Primary Hub Nodes ==="
KUBECONFIG=~/.kube/config-plat-03 kubectl get nodes

echo "=== Secondary Hub Nodes ==="
KUBECONFIG=~/.kube/config-plat-04 kubectl get nodes

echo "=== Primary Hub Namespace ==="
KUBECONFIG=~/.kube/config-plat-03 kubectl get namespace opensandbox-system

echo "=== Secondary Hub Namespace ==="
KUBECONFIG=~/.kube/config-plat-04 kubectl get namespace opensandbox-system
```

All four commands should return `Ready` / `Active` status before proceeding.

---

## 🔧 Phase 2: Install OCM on Both Hubs

### What you're doing
Open Cluster Management (OCM) is the brain that decides which Spoke cluster gets workloads. You need it running on **both** Hubs so either one can take over dispatching jobs to Spokes.

### Step 2.1 — Install clusteradm CLI

```bash
curl -L https://raw.githubusercontent.com/open-cluster-management-io/clusteradm/main/install.sh | bash
clusteradm version
```

### Step 2.2 — Initialize OCM on Primary Hub (`bb-mp-plat-03`)

```bash
KUBECONFIG=~/.kube/config-plat-03 clusteradm init \
  --wait \
  --output-join-command-file /tmp/join-primary.txt

cat /tmp/join-primary.txt
```

### Step 2.3 — Initialize OCM on Secondary Hub (`bb-mp-plat-04`)

```bash
KUBECONFIG=~/.kube/config-plat-04 clusteradm init \
  --wait \
  --output-join-command-file /tmp/join-secondary.txt

cat /tmp/join-secondary.txt
```

---

## 🔧 Phase 3: Prepare Spoke Clusters & Install OCM Klusterlet Agent

### What you're doing

This phase has **two parts**:

1. **Create the Spoke clusters** (`kind-east` and `kind-west`) — these are the actual worker clusters that run microVM sandbox jobs.
2. **Install the OCM Klusterlet agent** on each Spoke and register them to **both** Hub clusters.

> 💡 **OCM on Spokes ≠ OCM Hub.** You do **not** install the full OCM Hub on Spokes. Instead, `clusteradm join` installs a lightweight agent called the **Klusterlet** on the Spoke. The Klusterlet's only job is to:
> - Register itself with the Hub and send heartbeats (CPU/RAM/status)
> - Watch for `ManifestWork` objects dispatched from the Hub
> - Apply those manifests locally (e.g., boot a Kata microVM worker pod)
>
> The Hub does all the intelligence (placement, scheduling). The Spoke just executes.

---

### 🖥️ PART A — Create the Spoke Clusters

#### Step 3.1 — Install `kind` on your local machine (if not already installed)

```bash
# Download and install kind
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.23.0/kind-linux-amd64
chmod +x ./kind
sudo mv ./kind /usr/local/bin/kind

# Verify
kind version
```

#### Step 3.2 — Create the `kind-east` Spoke cluster

```bash
# Create the kind-east cluster
kind create cluster \
  --name kind-east \
  --config - <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
EOF

# Export its kubeconfig to a dedicated file
kind get kubeconfig --name kind-east > ~/.kube/config-kind-east
chmod 600 ~/.kube/config-kind-east

# Verify
KUBECONFIG=~/.kube/config-kind-east kubectl get nodes
```

Expected output:
```
NAME                      STATUS   ROLES           AGE   VERSION
kind-east-control-plane   Ready    control-plane   1m    v1.30.x
kind-east-worker          Ready    <none>          1m    v1.30.x
kind-east-worker2         Ready    <none>          1m    v1.30.x
```

#### Step 3.3 — Create the `kind-west` Spoke cluster

```bash
kind create cluster \
  --name kind-west \
  --config - <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
  - role: worker
  - role: worker
EOF

kind get kubeconfig --name kind-west > ~/.kube/config-kind-west
chmod 600 ~/.kube/config-kind-west

# Verify
KUBECONFIG=~/.kube/config-kind-west kubectl get nodes
```

---

### 🔗 PART B — Install OCM Klusterlet on Each Spoke (Dual-Hub Registration)

#### How `clusteradm join` & the CSR Actually Work

> ⚠️ **Important clarification:** `clusteradm join` **installs the Klusterlet FIRST**, then the Klusterlet sends the CSR. The CSR is not what triggers the installation — it is a security handshake that happens *after* the agent is running.

Here is the exact sequence of events when you run `clusteradm join` followed by `clusteradm accept`:

```
YOUR MACHINE                    SPOKE (kind-east)              PRIMARY HUB (plat-03)
     │                               │                               │
     │── clusteradm join ──────────► │                               │
     │                               │                               │
     │                    [1] Klusterlet pods are installed          │
     │                        into open-cluster-management-agent     │
     │                        namespace on kind-east                 │
     │                               │                               │
     │                    [2] Klusterlet sends a CSR ──────────────► │
     │                        "Hi Hub, I am kind-east.               │
     │                         Here is my cert request.              │
     │                         Please approve me."                   │
     │                               │                               │
     │                               │          [3] Hub holds CSR    │
     │                               │              in PENDING state │
     │                               │                               │
     │── clusteradm accept ──────────────────────────────────────── ►│
     │   (you run this on Hub)       │                               │
     │                               │       [4] Hub signs the CSR   │
     │                               │           and sends back a    │
     │                               │           signed certificate  │
     │                               │                               │
     │                    [5] Klusterlet receives ◄──────────────── │
     │                        a trusted signed cert.                 │
     │                        Secure channel established.            │
     │                               │                               │
     │                    [6] Klusterlet starts sending              │
     │                        heartbeats + watching for              │
     │                        ManifestWork from Hub ─────────────── ►│
```

#### Summary of the 3 Actor Roles

| Step | Who runs it | What happens |
|:---:|:---|:---|
| `clusteradm join` | **You, targeting the Spoke kubeconfig** | Installs Klusterlet pods onto the Spoke. Klusterlet then sends a CSR to the Hub. |
| `clusteradm accept` | **You, targeting the Hub kubeconfig** | Hub admin approves the CSR and signs a trusted certificate for this Spoke. |
| Post-accept (automatic) | **Klusterlet (self-managed)** | Klusterlet receives the signed cert, opens a secure channel, begins sending CPU/RAM heartbeats to the Hub. |

> 💡 **Why two commands are always needed:** `join` installs the agent. `accept` completes the trust handshake. Without `accept`, the Klusterlet is installed but stuck in `Pending` — the Hub will not send it any work until the CSR is approved.

---

#### Step 3.4 — Register `kind-east` to the Primary Hub

Run against the **kind-east** kubeconfig (this installs Klusterlet on `kind-east` and connects to Primary Hub):

```bash
KUBECONFIG=~/.kube/config-kind-east \
  clusteradm join \
  --hub-token <TOKEN_FROM_PRIMARY_join-primary.txt> \
  --hub-apiserver https://10.0.8.9:6443 \
  --cluster-name kind-east \
  --wait
```

#### Step 3.5 — Accept `kind-east` CSR on Primary Hub

On the **Primary Hub**, approve the Spoke's join request:

```bash
KUBECONFIG=~/.kube/config-plat-03 \
  clusteradm accept --clusters kind-east --wait

# Verify kind-east appears as a ManagedCluster on Primary Hub
KUBECONFIG=~/.kube/config-plat-03 kubectl get managedclusters
```

Expected output:
```
NAME        HUB ACCEPTED   MANAGED CLUSTER URLS         JOINED   AVAILABLE   AGE
kind-east   true           https://127.0.0.1:XXXXX      True     True        1m
```

#### Step 3.6 — Verify Klusterlet is running on `kind-east`

Confirm the Klusterlet agent pods are actually running inside the Spoke:

```bash
KUBECONFIG=~/.kube/config-kind-east \
  kubectl get pods -n open-cluster-management-agent
```

Expected output:
```
NAME                                             READY   STATUS    RESTARTS   AGE
klusterlet-XXXXXXXXX-XXXXX                       1/1     Running   0          2m
klusterlet-registration-agent-XXXXXXXXX-XXXXX    1/1     Running   0          2m
klusterlet-work-agent-XXXXXXXXX-XXXXX            1/1     Running   0          2m
```

---

#### Step 3.7 — Register `kind-east` to Secondary Hub (Dual Registration)

Now register the **same** `kind-east` Spoke to the Secondary Hub as well.
This is what enables failover — Secondary Hub already knows about this Spoke and can dispatch to it without any reconfiguration:

```bash
KUBECONFIG=~/.kube/config-kind-east \
  clusteradm join \
  --hub-token <TOKEN_FROM_SECONDARY_join-secondary.txt> \
  --hub-apiserver https://10.0.8.10:6443 \
  --cluster-name kind-east \
  --wait
```

#### Step 3.8 — Accept `kind-east` CSR on Secondary Hub

```bash
KUBECONFIG=~/.kube/config-plat-04 \
  clusteradm accept --clusters kind-east --wait

# Verify kind-east appears on Secondary Hub too
KUBECONFIG=~/.kube/config-plat-04 kubectl get managedclusters
```

---

> 🔁 **Repeat Steps 3.4–3.8 for `kind-west`**, replacing `kind-east` with `kind-west` in every command.

---

### ✅ Phase 3 Verification — All Spokes Dual-Registered

Run this full check to confirm both Spokes are registered to both Hubs:

```bash
echo "=== Primary Hub — Managed Clusters ==="
KUBECONFIG=~/.kube/config-plat-03 kubectl get managedclusters

echo "=== Secondary Hub — Managed Clusters ==="
KUBECONFIG=~/.kube/config-plat-04 kubectl get managedclusters

echo "=== kind-east Klusterlet Agent Pods ==="
KUBECONFIG=~/.kube/config-kind-east \
  kubectl get pods -n open-cluster-management-agent

echo "=== kind-west Klusterlet Agent Pods ==="
KUBECONFIG=~/.kube/config-kind-west \
  kubectl get pods -n open-cluster-management-agent
```

All Spokes should show `JOINED=True` and `AVAILABLE=True` on **both** Hubs before moving to Phase 4.

---

## 🔧 Phase 4: Set Up Cilium ClusterMesh + WireGuard Tunnel

### What you're doing

**both Hub clusters** need Cilium ClusterMesh enabled, and WireGuard encryption turned on for each. Here is exactly what happens across both Hubs:

```
  PRIMARY HUB (plat-03)                      SECONDARY HUB (plat-04)
  ─────────────────────                      ───────────────────────
  [Already installed in Phase 1]             [Already installed in Phase 1]
  Cilium CNI (cni: cilium in RKE2)           Cilium CNI (cni: cilium in RKE2)
          │                                           │
  [Step 4.2] cilium clustermesh enable       [Step 4.3] cilium clustermesh enable
  → Deploys clustermesh-apiserver pod        → Deploys clustermesh-apiserver pod
  → Exposes it via NodePort                  → Exposes it via NodePort
          │                                           │
          └──────── [Step 4.4] cilium clustermesh connect ─────────┘
                    → Exchanges TLS certs between both apiservers
                    → Establishes the bidirectional peer mesh
          │                                           │
  [Step 4.5] enable-wireguard true           [Step 4.5] enable-wireguard true
  → All cross-cluster traffic encrypted      → All cross-cluster traffic encrypted
    via kernel WireGuard (cilium_wg0)          via kernel WireGuard (cilium_wg0)
```

> 💡 **Cilium CNI vs ClusterMesh:** In Phase 1, you set `cni: cilium` in the RKE2 config. This installed Cilium as the **network plugin** inside each Hub cluster — but only for traffic *within* that cluster. **ClusterMesh is a separate feature on top** that connects two separate Cilium clusters together, letting their pods and services discover and reach each other across cluster boundaries.

| Component | Scope | Installed when |
|:---|:---:|:---|
| **Cilium CNI** | Within a single cluster | Phase 1 (via `cni: cilium` in RKE2 config) |
| **Cilium ClusterMesh** | Across two or more clusters | Phase 4 (this phase) |
| **WireGuard encryption** | All cross-cluster traffic | Phase 4 (this phase) |

---

### Step 4.1 — Verify Cilium is already running on both Hubs

Before enabling ClusterMesh, confirm Cilium CNI is healthy on both clusters (it was installed automatically in Phase 1 by RKE2):

```bash
# Install the Cilium CLI tool (if not already installed)
CILIUM_CLI_VERSION=$(curl -s https://raw.githubusercontent.com/cilium/cilium-cli/main/stable.txt)
curl -L --remote-name-all \
  https://github.com/cilium/cilium-cli/releases/download/${CILIUM_CLI_VERSION}/cilium-linux-amd64.tar.gz
tar -xzf cilium-linux-amd64.tar.gz -C /usr/local/bin
cilium version

# Check Cilium CNI status on Primary Hub
KUBECONFIG=~/.kube/config-plat-03 cilium status --wait
# Expected: All components healthy ✅

# Check Cilium CNI status on Secondary Hub
KUBECONFIG=~/.kube/config-plat-04 cilium status --wait
# Expected: All components healthy ✅
```

---

### Step 4.2 — Set up kubeconfig contexts (needed for the connect command)

The `cilium clustermesh connect` command needs named **kubeconfig contexts** to identify both clusters. Set them up now:

```bash
# Add Primary Hub context named "plat-03"
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl config rename-context \
  $(kubectl --kubeconfig ~/.kube/config-plat-03 config current-context) \
  plat-03

# Add Secondary Hub context named "plat-04"
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl config rename-context \
  $(kubectl --kubeconfig ~/.kube/config-plat-04 config current-context) \
  plat-04

# Merge both into a single kubeconfig file for convenience
KUBECONFIG=~/.kube/config-plat-03:~/.kube/config-plat-04 \
  kubectl config view --flatten > ~/.kube/config-hubs

# Verify both contexts are visible
KUBECONFIG=~/.kube/config-hubs kubectl config get-contexts
```

Expected output:
```
CURRENT   NAME      CLUSTER        AUTHINFO        NAMESPACE
          plat-03   bb-mp-plat-03  default-admin   default
*         plat-04   bb-mp-plat-04  default-admin   default
```

---

### Step 4.3 — Enable ClusterMesh on Primary Hub (`bb-mp-plat-03`)

This deploys the `clustermesh-apiserver` pod inside `plat-03` and exposes it so `plat-04` can reach it:

```bash
KUBECONFIG=~/.kube/config-plat-03 \
  cilium clustermesh enable \
  --service-type NodePort \
  --wait

# Confirm the clustermesh-apiserver pod is running on Primary Hub
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl get pods -n kube-system -l app=clustermesh-apiserver
```

Expected output:
```
NAME                                  READY   STATUS    RESTARTS   AGE
clustermesh-apiserver-XXXXXXXX-XXXXX  1/1     Running   0          1m
```

---

### Step 4.4 — Enable ClusterMesh on Secondary Hub (`bb-mp-plat-04`)

Repeat the same on Secondary Hub — this deploys its own `clustermesh-apiserver`:

```bash
KUBECONFIG=~/.kube/config-plat-04 \
  cilium clustermesh enable \
  --service-type NodePort \
  --wait

# Confirm the clustermesh-apiserver pod is running on Secondary Hub
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl get pods -n kube-system -l app=clustermesh-apiserver
```

---

### Step 4.5 — Connect Primary ↔ Secondary ClusterMesh (Bidirectional)

Now that both Hubs have their ClusterMesh apiservers running, connect them together. This exchanges TLS certificates between both sides and establishes the peer mesh link:

```bash
# Use the merged kubeconfig with named contexts
KUBECONFIG=~/.kube/config-hubs \
  cilium clustermesh connect \
  --context plat-03 \
  --destination-context plat-04

# Wait and confirm the tunnel is up
KUBECONFIG=~/.kube/config-hubs \
  cilium clustermesh status \
  --context plat-03 \
  --wait
```

Expected output:
```
✅ Service "clustermesh-apiserver" of type "NodePort" found
✅ Cluster Connections: 1
✅ All 2 nodes are connected. cilium_wg0 tunnel operational.
```

---

### Step 4.6 — Enable WireGuard encryption on BOTH Hubs

This turns on transparent kernel-level WireGuard encryption for **all** traffic crossing the `cilium_wg0` tunnel between the two Hubs — including PostgreSQL WAL, RabbitMQ, and Redis replication streams:

```bash
# Enable WireGuard on Primary Hub
KUBECONFIG=~/.kube/config-plat-03 \
  cilium config set enable-wireguard true

# Enable WireGuard on Secondary Hub
KUBECONFIG=~/.kube/config-plat-04 \
  cilium config set enable-wireguard true
```

To enable **Node-to-Node host encryption** and persist configuration across VM restarts:

```bash
# Enable Node Encryption on Primary Hub
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl -n kube-system patch configmap cilium-config --type merge -p '{"data":{"encrypt-node":"true"}}'
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl -n kube-system rollout restart daemonset/cilium

# Enable Node Encryption on Secondary Hub
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl -n kube-system patch configmap cilium-config --type merge -p '{"data":{"encrypt-node":"true"}}'
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl -n kube-system rollout restart daemonset/cilium
```

> ⚠️ **Both Hubs must have WireGuard enabled.** If only one side has it on, the tunnel negotiation fails and cross-cluster traffic drops.

---

### ✅ Phase 4 Verification — ClusterMesh + WireGuard Active

```bash
# Full tunnel status from Primary Hub's perspective
KUBECONFIG=~/.kube/config-hubs \
  cilium clustermesh status --context plat-03

# Confirm WireGuard is active on Primary Hub nodes
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl -n kube-system exec ds/cilium -- cilium-dbg status | grep -i wireguard

# Confirm WireGuard is active on Secondary Hub nodes
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl -n kube-system exec ds/cilium -- cilium-dbg status | grep -i wireguard
```

Both should show:
```
WireGuard:   OK, node encryption: Enabled (or OptedOut), cilium_wg0 interface active
```

> 💡 **NodeEncryption Status Note:**
> - **`Disabled`**: Node encryption is off. Run the `encrypt-node: "true"` patch commands above to enable.
> - **`OptedOut`**: Node encryption is enabled, but Control Plane nodes are safely excluded from host-level encryption by Cilium. Pod-to-Pod and cross-cluster WireGuard traffic (`cilium_wg0`) is fully encrypted and active.

---

## 🔧 Phase 5: PostgreSQL Cross-Cluster Replication (CNPG)

### What you're doing

The setup follows this exact 3-stage flow:

```
  STAGE 1 — PRIMARY HUB (plat-03)          STAGE 2 — PRIMARY HUB      STAGE 3 — SECONDARY HUB (plat-04)
  ─────────────────────────────────         ───────────────────────     ──────────────────────────────────
  Install CNPG Operator                     ServiceExport               Install CNPG Operator
  Deploy postgresql-primary Cluster         (makes the primary DB       Deploy postgresql-secondary Cluster
  (Read-Write, 3 instances)                  visible across ClusterMesh) (Replica mode — reads from primary)
         │                                          │                              │
         │ WAL streaming (Write-Ahead Log)          │                              │
         └──────────────────────────────────────────┼──────────────────────────── ►│
                                                    │         Cross-cluster        │
                                              clusterset.local DNS                 │
                                              resolves primary DB ─────────────── ►│
```

| Step | Runs on | What it does |
|:---:|:---:|:---|
| **5.1** | Primary Hub (`plat-03`) | Install CNPG Operator |
| **5.2** | Primary Hub (`plat-03`) | Deploy the `postgresql-primary` Cluster (Read-Write) |
| **5.3** | Primary Hub (`plat-03`) | `ServiceExport` — expose the primary DB across ClusterMesh |
| **5.4** | Secondary Hub (`plat-04`) | Install CNPG Operator |
| **5.5** | Secondary Hub (`plat-04`) | Deploy `postgresql-secondary` Cluster in replica mode |
| **5.6** | Both | Verify WAL replication is streaming live |

---

### Step 5.1 — Install CloudNativePG Operator on PRIMARY Hub (`bb-mp-plat-03`)

CNPG must be installed on the Primary Hub first — this is where the actual read-write PostgreSQL database runs:

```bash
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl apply -f https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.23/releases/cnpg-1.23.0.yaml

# Wait for the CNPG controller to be ready on Primary Hub
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl wait --for=condition=Available deployment/cnpg-controller-manager \
  -n cnpg-system --timeout=120s

# Verify
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl get pods -n cnpg-system
# Expected: cnpg-controller-manager Running
```

---

### Step 5.2 — Create the PostgreSQL credentials Secret on Primary Hub

The CNPG operator needs a secret with the superuser password before it can create the cluster:

```bash
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl create secret generic postgresql-primary-credentials \
  --from-literal=username=postgres \
  --from-literal=password=<YOUR_STRONG_PASSWORD> \
  -n opensandbox-system
```

---

### Step 5.3 — Deploy the PRIMARY PostgreSQL Cluster on Primary Hub

This creates the main read-write PostgreSQL cluster on `plat-03` — this is the **source of truth** for all scan data:

```bash
cat <<EOF | KUBECONFIG=~/.kube/config-plat-03 kubectl apply -f -
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgresql-primary
  namespace: opensandbox-system
spec:
  instances: 3                     # 3 instances spread across the 3 Hub nodes for in-cluster HA
  primaryUpdateStrategy: unsupervised

  postgresql:
    parameters:
      max_connections: "200"
      wal_level: logical           # Required for cross-cluster replication

  bootstrap:
    initdb:
      database: sandbox_db
      owner: postgres
      secret:
        name: postgresql-primary-credentials

  storage:
    size: 20Gi
    storageClass: local-path
EOF

# Wait for all 3 instances to be ready
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl wait cluster/postgresql-primary \
  -n opensandbox-system \
  --for=condition=Ready \
  --timeout=300s

# Verify cluster is healthy and check which pod is the Primary
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl get cluster postgresql-primary -n opensandbox-system
```

Expected output:
```
NAME                  AGE   INSTANCES   READY   STATUS                     PRIMARY
postgresql-primary    2m    3           3       Cluster in healthy state   postgresql-primary-1
```

---

### Step 5.4 — Export PostgreSQL Service from Primary Hub (ClusterMesh ServiceExport)

This makes the primary PostgreSQL service discoverable from the Secondary Hub across the ClusterMesh tunnel. Without this, the Secondary Hub cannot resolve the primary DB's address:

```bash
cat <<EOF | KUBECONFIG=~/.kube/config-plat-03 kubectl apply -f -
apiVersion: multicluster.x-k8s.io/v1alpha1
kind: ServiceExport
metadata:
  name: postgresql-primary-rw       # Export the read-write service endpoint
  namespace: opensandbox-system
EOF

# Verify the export is registered
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl get serviceexport -n opensandbox-system
```

Expected output:
```
NAME                      AGE   VALID
postgresql-primary-rw     30s   true
```

> 💡 **What ClusterMesh DNS gives you:** After this export, the Secondary Hub can reach the Primary's PostgreSQL using the address:
> `postgresql-primary-rw.opensandbox-system.svc.clusterset.local`
> This is the address used in Step 5.6 to point the replica at the primary.

---

### Step 5.5 — Install CloudNativePG Operator on SECONDARY Hub (`bb-mp-plat-04`)

Now install CNPG on the Secondary Hub — this will manage the replica cluster:

```bash
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl apply -f https://raw.githubusercontent.com/cloudnative-pg/cloudnative-pg/release-1.23/releases/cnpg-1.23.0.yaml

# Wait for CNPG controller to be ready on Secondary Hub
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl wait --for=condition=Available deployment/cnpg-controller-manager \
  -n cnpg-system --timeout=120s
```

---

### Step 5.6 — Copy the credentials Secret to Secondary Hub

The Secondary Hub's CNPG replica needs the same credentials to authenticate with the Primary:

```bash
# Export secret from Primary Hub and apply to Secondary Hub
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl get secret postgresql-primary-credentials \
  -n opensandbox-system -o yaml \
  | grep -v '^\s*\(creationTimestamp\|resourceVersion\|uid\|selfLink\|annotations\):' \
  | KUBECONFIG=~/.kube/config-plat-04 kubectl apply -f -
```

---

### Step 5.7 — Deploy the REPLICA PostgreSQL Cluster on Secondary Hub

This creates the standby replica cluster on `plat-04`. It runs in **read-only replica mode** — it continuously applies WAL stream received from the Primary:

```bash
cat <<EOF | KUBECONFIG=~/.kube/config-plat-04 kubectl apply -f -
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgresql-secondary
  namespace: opensandbox-system
spec:
  instances: 1                     # 1 standby instance on Secondary Hub

  # Replica mode: this cluster only receives WAL from the primary, never writes
  replica:
    enabled: true
    source: postgresql-primary     # Must match the externalClusters name below

  externalClusters:
    - name: postgresql-primary
      connectionParameters:
        # ClusterMesh DNS resolves this to the Primary Hub's PostgreSQL pod IP
        host: postgresql-primary-rw.opensandbox-system.svc.clusterset.local
        user: postgres
        dbname: sandbox_db
        sslmode: require
      password:
        name: postgresql-primary-credentials
        key: password

  storage:
    size: 20Gi
    storageClass: local-path
EOF

# Wait for the replica to come up
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl wait cluster/postgresql-secondary \
  -n opensandbox-system \
  --for=condition=Ready \
  --timeout=300s
```

---

### Step 5.8 — Verify WAL Replication is Streaming

```bash
# On Primary Hub — confirm replication slot is active and streaming to Secondary
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl exec -n opensandbox-system postgresql-primary-1 -- \
  psql -U postgres -c "SELECT client_addr, state, sent_lsn, write_lsn, replay_lsn FROM pg_stat_replication;"

# On Secondary Hub — confirm it is in recovery (replica) mode
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl exec -n opensandbox-system postgresql-secondary-1 -- \
  psql -U postgres -c "SELECT pg_is_in_recovery();"
# Expected: t  (true = standby mode, receiving WAL from Primary)

# Write a test record on Primary and confirm it appears on Secondary
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl exec -n opensandbox-system postgresql-primary-1 -- \
  psql -U postgres -d sandbox_db -c \
  "CREATE TABLE IF NOT EXISTS replication_test (id serial, ts timestamptz DEFAULT now()); INSERT INTO replication_test DEFAULT VALUES;"

# Check it replicated to Secondary (wait ~2 seconds)
sleep 2
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl exec -n opensandbox-system postgresql-secondary-1 -- \
  psql -U postgres -d sandbox_db -c "SELECT * FROM replication_test;"
# Expected: 1 row — confirming WAL replication is live
```

---

## 🔧 Phase 6: RabbitMQ Federation Setup

### What you're doing

RabbitMQ follows a **similar structure** to PostgreSQL but uses a different sync mechanism. Instead of WAL streaming, RabbitMQ uses its built-in **Federation Plugin** to mirror messages from the Primary broker to the Secondary broker.

> 💡 **Does RabbitMQ use ServiceExport/MCS like PostgreSQL?**
> **Yes** — but only to let the Secondary Hub resolve the Primary Hub's AMQPS address across the ClusterMesh tunnel. The actual message mirroring is done by RabbitMQ's own Federation Plugin (not by MCS itself). MCS just provides the cross-cluster DNS address.
>
> **Does MCS need to be installed separately?**
> **No.** MCS (Multi-Cluster Services API) is already bundled inside **Cilium ClusterMesh** (enabled in Phase 4). The `ServiceExport` CRD is automatically available on both Hubs the moment ClusterMesh was enabled. No separate installation needed.

```
  PRIMARY HUB (plat-03)                              SECONDARY HUB (plat-04)
  ─────────────────────                              ───────────────────────
  [Step 6.1] Deploy RabbitMQ                        [Step 6.4] Deploy RabbitMQ
  (Active broker, handles all live scan tasks)       (Backup broker, standby)
          │                                                    │
  [Step 6.2] ServiceExport rabbitmq-primary-amqps            │
  → MCS exposes the AMQPS port (5671) across ClusterMesh      │
  → Secondary can now resolve rabbitmq via clusterset.local   │
          │                                                    │
          │    [Step 6.5] Enable Federation Plugin ◄──────────┘
          │    [Step 6.6] Set upstream URI = clusterset.local address
          │    [Step 6.7] Apply federation policy to repo_scan_queue
          │                                                    │
          └── RabbitMQ Federation mirrors messages ──────────►│
              (Primary queue → Secondary queue continuously)   │
```

| Step | Runs on | What it does |
|:---:|:---:|:---|
| **6.1** | Primary Hub (`plat-03`) | Deploy RabbitMQ with the Cluster Operator |
| **6.2** | Primary Hub (`plat-03`) | `ServiceExport` — expose AMQPS port via MCS ClusterMesh |
| **6.3** | Primary Hub (`plat-03`) | Create RabbitMQ credentials Secret |
| **6.4** | Secondary Hub (`plat-04`) | Deploy RabbitMQ with the Cluster Operator |
| **6.5** | Secondary Hub (`plat-04`) | Enable Federation + Shovel plugins |
| **6.6** | Secondary Hub (`plat-04`) | Set federation upstream pointing to Primary via clusterset DNS |
| **6.7** | Secondary Hub (`plat-04`) | Apply federation policy to `repo_scan_queue` |
| **6.8** | Both | Verify federation is running and messages are mirroring |

---

### Step 6.1 — Deploy RabbitMQ on PRIMARY Hub (`bb-mp-plat-03`)

Deploy RabbitMQ using the RabbitMQ Cluster Kubernetes Operator on Primary Hub:

```bash
# Install the RabbitMQ Cluster Operator
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl apply -f https://github.com/rabbitmq/cluster-operator/releases/latest/download/cluster-operator.yml

# Wait for operator to be ready
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl wait --for=condition=Available deployment/rabbitmq-cluster-operator \
  -n rabbitmq-system --timeout=120s

# Deploy the RabbitMQ cluster on Primary Hub
cat <<EOF | KUBECONFIG=~/.kube/config-plat-03 kubectl apply -f -
apiVersion: rabbitmq.com/v1beta1
kind: RabbitmqCluster
metadata:
  name: rabbitmq-primary
  namespace: opensandbox-system
spec:
  replicas: 3
  rabbitmq:
    additionalPlugins:
      - rabbitmq_federation
      - rabbitmq_federation_management
      - rabbitmq_shovel
    additionalConfig: |
      management.tcp.port = 15672
  service:
    type: ClusterIP
EOF

# Wait for RabbitMQ cluster to be ready
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl wait rabbitmqcluster/rabbitmq-primary \
  -n opensandbox-system \
  --for=condition=AllReplicasReady \
  --timeout=300s
```

---

### Step 6.2 — Export RabbitMQ AMQPS Service from Primary Hub (MCS ServiceExport)

Expose the Primary Hub's RabbitMQ AMQPS port across the ClusterMesh tunnel so the Secondary can connect to it:

```bash
cat <<EOF | KUBECONFIG=~/.kube/config-plat-03 kubectl apply -f -
apiVersion: multicluster.x-k8s.io/v1alpha1
kind: ServiceExport
metadata:
  name: rabbitmq-primary             # Matches the RabbitmqCluster service name
  namespace: opensandbox-system
EOF

# Verify export is valid
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl get serviceexport -n opensandbox-system
```

> After this, Secondary Hub can reach Primary RabbitMQ at:
> `rabbitmq-primary.opensandbox-system.svc.clusterset.local:5671` (AMQPS)

---

### Step 6.3 — Get RabbitMQ credentials from Primary Hub

You'll need these when configuring the federation upstream on Secondary:

```bash
# Get the RabbitMQ default user and password from Primary Hub
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl get secret rabbitmq-primary-default-user \
  -n opensandbox-system \
  -o jsonpath='{.data.username}' | base64 -d && echo

KUBECONFIG=~/.kube/config-plat-03 \
  kubectl get secret rabbitmq-primary-default-user \
  -n opensandbox-system \
  -o jsonpath='{.data.password}' | base64 -d && echo

# Save these values — you will need them in Step 6.6
```

---

### Step 6.4 — Deploy RabbitMQ on SECONDARY Hub (`bb-mp-plat-04`)

```bash
# Install the RabbitMQ Cluster Operator on Secondary Hub
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl apply -f https://github.com/rabbitmq/cluster-operator/releases/latest/download/cluster-operator.yml

KUBECONFIG=~/.kube/config-plat-04 \
  kubectl wait --for=condition=Available deployment/rabbitmq-cluster-operator \
  -n rabbitmq-system --timeout=120s

# Deploy the backup RabbitMQ cluster on Secondary Hub
cat <<EOF | KUBECONFIG=~/.kube/config-plat-04 kubectl apply -f -
apiVersion: rabbitmq.com/v1beta1
kind: RabbitmqCluster
metadata:
  name: rabbitmq-secondary
  namespace: opensandbox-system
spec:
  replicas: 1
  rabbitmq:
    additionalPlugins:
      - rabbitmq_federation
      - rabbitmq_federation_management
      - rabbitmq_shovel
EOF

KUBECONFIG=~/.kube/config-plat-04 \
  kubectl wait rabbitmqcluster/rabbitmq-secondary \
  -n opensandbox-system \
  --for=condition=AllReplicasReady \
  --timeout=300s
```

---

### Step 6.5 — Configure Federation Upstream on Secondary Hub

Tell Secondary RabbitMQ where the Primary is (using the ClusterMesh DNS address from Step 6.2):

```bash
# Replace <USER> and <PASSWORD> with values from Step 6.3
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl exec -n opensandbox-system \
  rabbitmq-secondary-server-0 -it -- \
  rabbitmqctl set_parameter federation-upstream primary-hub \
  '{"uri":"amqps://<USER>:<PASSWORD>@rabbitmq-primary.opensandbox-system.svc.clusterset.local:5671","expires":3600000}'
```

---

### Step 6.6 — Apply Federation Policy to `repo_scan_queue`

```bash
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl exec -n opensandbox-system \
  rabbitmq-secondary-server-0 -it -- \
  rabbitmqctl set_policy --apply-to queues federate-scan-queue \
  "^repo_scan_queue$" \
  '{"federation-upstream":"primary-hub"}'
```

---

### Step 6.7 — Verify Federation is Running

```bash
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl exec -n opensandbox-system \
  rabbitmq-secondary-server-0 -it -- \
  rabbitmqctl federation_status
```

Expected output:
```
[
  #{exchange => <<"repo_scan_queue">>,
    upstream => <<"primary-hub">>,
    status => running,
    uri => <<"amqps://rabbitmq-primary...">>}
]
```

---

## 🔧 Phase 7: Redis Cross-Cluster Replication

### What you're doing

Redis follows a **similar structure** to PostgreSQL. The Primary Hub runs the Redis Master (source of truth for rate limits and session state). The Secondary Hub runs a Redis Replica that receives all writes from the Primary via the standard Redis replication protocol.

> 💡 **Does Redis use ServiceExport/MCS?**
> **Yes** — exactly like PostgreSQL. You `ServiceExport` the Primary Redis service from the Primary Hub so the Secondary Hub can reach it using the ClusterMesh `clusterset.local` DNS address. Then you run `REPLICAOF` on the Secondary pointing to that address.
>
> **Does MCS need to be installed separately?**
> **No.** Again — MCS is already bundled inside Cilium ClusterMesh (Phase 4). No additional installation needed.

```
  PRIMARY HUB (plat-03)                              SECONDARY HUB (plat-04)
  ─────────────────────                              ───────────────────────
  [Step 7.1] Deploy Redis Master                     [Step 7.4] Deploy Redis Replica
  (Handles all rate limit writes)                    (Receives all writes from Primary)
          │                                                    │
  [Step 7.2] ServiceExport redis-primary             │
  → MCS exposes port 6379 across ClusterMesh          │
  → Secondary resolves via clusterset.local           │
          │                                                    │
          │    [Step 7.5] REPLICAOF <clusterset DNS addr> ◄───┘
          │                                                    │
          └── Redis replication stream (async) ──────────────►│
              (every key-write replicated automatically)       │
```

| Step | Runs on | What it does |
|:---:|:---:|:---|
| **7.1** | Primary Hub (`plat-03`) | Deploy Redis Master |
| **7.2** | Primary Hub (`plat-03`) | `ServiceExport` — expose Redis port via MCS ClusterMesh |
| **7.3** | Secondary Hub (`plat-04`) | Deploy Redis (initially standalone) |
| **7.4** | Secondary Hub (`plat-04`) | `REPLICAOF` — point it at Primary via clusterset DNS |
| **7.5** | Both | Verify replication is active |

---

### Step 7.1 — Deploy Redis Master on PRIMARY Hub (`bb-mp-plat-03`)

```bash
cat <<EOF | KUBECONFIG=~/.kube/config-plat-03 kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis-primary
  namespace: opensandbox-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis-primary
  template:
    metadata:
      labels:
        app: redis-primary
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        ports:
        - containerPort: 6379
        command: ["redis-server", "--save", "60", "1", "--loglevel", "notice"]
---
apiVersion: v1
kind: Service
metadata:
  name: redis-primary
  namespace: opensandbox-system
spec:
  selector:
    app: redis-primary
  ports:
  - port: 6379
    targetPort: 6379
EOF

# Wait for Redis Master to be ready
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl rollout status deployment/redis-primary -n opensandbox-system

# Quick ping test
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl exec -n opensandbox-system deployment/redis-primary -- redis-cli PING
# Expected: PONG
```

---

### Step 7.2 — Export Redis Service from PRIMARY Hub (MCS ServiceExport)

```bash
cat <<EOF | KUBECONFIG=~/.kube/config-plat-03 kubectl apply -f -
apiVersion: multicluster.x-k8s.io/v1alpha1
kind: ServiceExport
metadata:
  name: redis-primary
  namespace: opensandbox-system
EOF

# Verify the export is registered and valid
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl get serviceexport -n opensandbox-system
```

Expected output:
```
NAME                     AGE   VALID
postgresql-primary-rw    10m   true
rabbitmq-primary         5m    true
redis-primary            30s   true
```

> After this, Secondary Hub can reach Primary Redis at:
> `redis-primary.opensandbox-system.svc.clusterset.local:6379`

---

### Step 7.3 — Deploy Redis on SECONDARY Hub (`bb-mp-plat-04`)

```bash
cat <<EOF | KUBECONFIG=~/.kube/config-plat-04 kubectl apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis-secondary
  namespace: opensandbox-system
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis-secondary
  template:
    metadata:
      labels:
        app: redis-secondary
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        ports:
        - containerPort: 6379
---
apiVersion: v1
kind: Service
metadata:
  name: redis-secondary
  namespace: opensandbox-system
spec:
  selector:
    app: redis-secondary
  ports:
  - port: 6379
    targetPort: 6379
EOF

KUBECONFIG=~/.kube/config-plat-04 \
  kubectl rollout status deployment/redis-secondary -n opensandbox-system
```

---

### Step 7.4 — Configure Secondary Redis to Replicate from Primary

Point Secondary Redis at the Primary using the ClusterMesh DNS address (exposed in Step 7.2):

```bash
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl exec -n opensandbox-system \
  deployment/redis-secondary -it -- \
  redis-cli REPLICAOF \
  redis-primary.opensandbox-system.svc.clusterset.local 6379

# Expected: OK
```

---

### Step 7.5 — Verify Redis Replication is Active

```bash
# Check replication role on Secondary (should be 'slave')
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl exec -n opensandbox-system \
  deployment/redis-secondary -it -- \
  redis-cli INFO replication | grep -E 'role|master_host|master_link_status'
# Expected:
# role:slave
# master_host:redis-primary.opensandbox-system.svc.clusterset.local
# master_link_status:up

# Write a test key on Primary and confirm it appears on Secondary
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl exec -n opensandbox-system deployment/redis-primary -- \
  redis-cli SET replication_test "hello-from-primary"

sleep 1

KUBECONFIG=~/.kube/config-plat-04 \
  kubectl exec -n opensandbox-system deployment/redis-secondary -- \
  redis-cli GET replication_test
# Expected: "hello-from-primary" — confirms replication is live
```

---

## 🔧 Phase 8: Deploy CodeInspector Stack (`sandbox-api`) on BOTH Hub Clusters via Helm

### What you're doing
The full `codeInspector` application stack—including `sandbox-api` (`apiServer`), Agent Gateway, resource pools, and supporting services—is provisioned using the unified `codeInspector` Helm chart (`codeInspector/`) on **both** Hub clusters:
- **Primary Hub (`plat-03`)** — actively serves 100% of live client traffic under normal operation.
- **Secondary Hub (`plat-04`)** — runs an identical hot-standby copy, ready to receive traffic the moment GSLB DNS switches over after a Primary Hub failure.

> Using Helm standardizes configuration management, enables reproducible deployments across both environments, and eliminates reliance on manual YAML manifests.

---

### Step 8.1 — Deploy CodeInspector via Helm on Primary Hub (`bb-mp-plat-03`)

```bash
# Update Helm chart dependency sub-charts (if needed)
helm dependency build ./codeInspector

# Deploy/Upgrade codeInspector Helm release on Primary Hub
KUBECONFIG=~/.kube/config-plat-03 \
  helm upgrade --install codeinspector ./codeInspector \
    --namespace opensandbox-system \
    --create-namespace \
    -f codeInspector/values.yaml
```

### Step 8.2 — Verify Primary Hub API is healthy

```bash
# Wait for sandbox-api (apiServer) pods to rollout successfully on Primary Hub
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl rollout status deployment/sandbox-api -n opensandbox-system

# Confirm the health endpoint responds on the Primary Hub IP
curl -sk https://10.0.8.9/healthz
# Expected: {"status":"ok"}
```

---

### Step 8.3 — Deploy CodeInspector via Helm on Secondary Hub (`bb-mp-plat-04`)

```bash
# Deploy/Upgrade codeInspector Helm release on Secondary Hub (Hot Standby)
KUBECONFIG=~/.kube/config-plat-04 \
  helm upgrade --install codeinspector ./codeInspector \
    --namespace opensandbox-system \
    --create-namespace \
    -f codeInspector/values.yaml
```

### Step 8.4 — Verify Secondary Hub API is healthy

```bash
# Wait for sandbox-api (apiServer) pods to rollout successfully on Secondary Hub
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl rollout status deployment/sandbox-api -n opensandbox-system

# Confirm the health endpoint responds on the Secondary Hub IP
curl -sk https://10.0.8.10/healthz
# Expected: {"status":"ok"}
```

### Step 8.5 — Confirm both APIs are running side-by-side

```bash
# Quick side-by-side check
echo "=== Primary Hub ==="
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl get pods -n opensandbox-system -l app=sandbox-api

echo "=== Secondary Hub ==="
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl get pods -n opensandbox-system -l app=sandbox-api
```

Expected output on both:
```
NAME                           READY   STATUS    RESTARTS   AGE
sandbox-api-xxxxxxxxxx-xxxxx   1/1     Running   0          Xm
```

> 💡 **Why both must be running:** Under normal operation, GSLB routes 100% of traffic to `10.0.8.9` (Primary). The Secondary API at `10.0.8.10` sits idle but warm. The moment GSLB detects 3 failed health probes on Primary (~15 seconds), it switches DNS to `10.0.8.10` — and the Secondary API immediately begins serving clients with **zero cold-start delay**.

---

## 🔧 Phase 9: Configure GSLB Health-Check DNS Failover

### What you're doing
This is the automatic "traffic cop." A health check probe hits Primary Hub every 5 seconds. If it fails 3 times in a row (15 seconds), DNS is updated to point `api.sandbox.local` at the Secondary Hub IP. **No human intervention needed.**

### Option A — Using Cloudflare (Recommended for production)

```bash
# 1. Create a monitor (health check probe)
curl -s -X POST "https://api.cloudflare.com/client/v4/user/load_balancers/monitors" \
  -H "X-Auth-Email: YOUR_EMAIL" \
  -H "X-Auth-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{
    "type": "https",
    "path": "/healthz",
    "interval": 5,
    "retries": 3,
    "timeout": 3,
    "method": "GET",
    "description": "Sandbox Hub Health Probe"
  }' | jq '.result.id'

# 2. Create the Primary origin pool
curl -s -X POST "https://api.cloudflare.com/client/v4/user/load_balancers/pools" \
  -H "X-Auth-Email: YOUR_EMAIL" \
  -H "X-Auth-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{
    "name": "primary-hub-pool",
    "enabled": true,
    "monitor": "MONITOR_ID",
    "origins": [{"name": "plat-03", "address": "10.0.8.9", "enabled": true, "weight": 1}]
  }' | jq '.result.id'

# 3. Create the Secondary fallback pool
curl -s -X POST "https://api.cloudflare.com/client/v4/user/load_balancers/pools" \
  -H "X-Auth-Email: YOUR_EMAIL" \
  -H "X-Auth-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{
    "name": "secondary-hub-pool",
    "enabled": true,
    "origins": [{"name": "plat-04", "address": "10.0.8.10", "enabled": true, "weight": 1}]
  }' | jq '.result.id'

# 4. Create the Load Balancer with failover priority
curl -s -X POST "https://api.cloudflare.com/client/v4/zones/YOUR_ZONE_ID/load_balancers" \
  -H "X-Auth-Email: YOUR_EMAIL" \
  -H "X-Auth-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  --data '{
    "name": "api.sandbox.local",
    "fallback_pool": "SECONDARY_POOL_ID",
    "default_pools": ["PRIMARY_POOL_ID"],
    "proxied": false,
    "ttl": 30
  }'
```

### Option B — Using CoreDNS + Health-Check CronJob (On-prem / local setup)

```bash
cat <<'EOF' | kubectl apply -f -
apiVersion: batch/v1
kind: CronJob
metadata:
  name: hub-health-check-failover
  namespace: kube-system
spec:
  schedule: "*/1 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: health-check
            image: curlimages/curl:latest
            command:
            - /bin/sh
            - -c
            - |
              PRIMARY_HEALTHY=$(curl -sk --max-time 3 https://10.0.8.9/healthz | grep -c '"status":"ok"')
              if [ "$PRIMARY_HEALTHY" -eq 0 ]; then
                echo "Primary Hub DOWN — patching DNS to Secondary"
                kubectl patch svc api-gateway -n opensandbox-system \
                  -p '{"spec":{"externalIPs":["10.0.8.10"]}}'
              else
                echo "Primary Hub HEALTHY"
              fi
          restartPolicy: OnFailure
          serviceAccountName: failover-sa
EOF
```

---

## 🔧 Phase 10: Test the Full Failover

### What you're doing
Validate the entire setup works as designed. Simulate a Primary Hub crash and confirm Secondary takes over automatically.

### Step 10.1 — Verify normal operation first

```bash
curl -sk https://10.0.8.9/healthz
# → {"status":"ok"}

KUBECONFIG=~/.kube/config-plat-03 kubectl get managedclusters
KUBECONFIG=~/.kube/config-plat-04 kubectl get managedclusters

KUBECONFIG=~/.kube/config-plat-04 kubectl exec \
  -n opensandbox-system postgresql-secondary-1 -- \
  psql -U postgres -c "SELECT pg_is_in_recovery();"
# Expected: t
```

### Step 10.2 — Simulate Primary Hub failure

```bash
# Option A: Scale down the API on Primary Hub
KUBECONFIG=~/.kube/config-plat-03 \
  kubectl scale deployment sandbox-api -n opensandbox-system --replicas=0

# Option B: Full outage simulation — stop RKE2 on bb-mp-plat-03
# ssh bb-mp-plat-03
# systemctl stop rke2-server
```

### Step 10.3 — Promote Secondary PostgreSQL to Read-Write

```bash
KUBECONFIG=~/.kube/config-plat-04 \
  kubectl cnpg promote postgresql-secondary -n opensandbox-system

# Verify Secondary DB is now Read-Write
KUBECONFIG=~/.kube/config-plat-04 kubectl exec \
  -n opensandbox-system postgresql-secondary-1 -- \
  psql -U postgres -c "SELECT pg_is_in_recovery();"
# Expected: f (false = now Primary, fully Read-Write)
```

### Step 10.4 — Confirm Secondary Hub is serving traffic

```bash
curl -sk https://10.0.8.10/healthz
# → {"status":"ok"}

curl -sk -X POST https://10.0.8.10/api/v1/scan \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/test/repo", "branch": "main"}'
# → {"status":"accepted","job_id":"scan-xxxx"}
```

### Step 10.5 — Confirm Spokes receive work from Secondary Hub

```bash
KUBECONFIG=~/.kube/config-kind-east \
  kubectl get manifestworks -n kind-east

KUBECONFIG=~/.kube/config-kind-east \
  kubectl get pods -n opensandbox-system
```

---

## ✅ Post-Setup Checklist

| # | Check | Command |
|:---:|:---|:---|
| 1 | Both Hubs have OCM running | `kubectl get pods -n open-cluster-management` on both Hubs |
| 2 | Spokes registered to both Hubs | `kubectl get managedclusters` on both Hubs |
| 3 | ClusterMesh tunnel is active | `cilium clustermesh status --context plat-03` |
| 4 | PostgreSQL replication active | `psql -c "SELECT * FROM pg_stat_replication;"` on Primary |
| 5 | RabbitMQ federation is running | `rabbitmqctl federation_status` on Secondary |
| 6 | Redis replication confirmed | `redis-cli INFO replication` on Secondary → `role:slave` |
| 7 | Secondary API healthy | `curl https://10.0.8.10/healthz` → `{"status":"ok"}` |
| 8 | GSLB failover tested | Simulate crash, confirm DNS switches within 15s |
| 9 | DB promotion tested | `cnpg promote postgresql-secondary` → `pg_is_in_recovery()` → `f` |
| 10 | End-to-end scan via Secondary | POST `/api/v1/scan` to `10.0.8.10` completes successfully |

---

## ⚠️ The One Manual Step

The only step that is **not** automatic out-of-the-box is PostgreSQL promotion:

```bash
kubectl cnpg promote postgresql-secondary -n opensandbox-system
```

Everything else (DNS failover via GSLB, RabbitMQ federation, Redis replication, OCM Spoke dispatch from Secondary) switches automatically. PostgreSQL promotion can be automated with a watch-loop operator or a Kubernetes CronJob that polls Primary Hub health.

---

## 🚨 Key Files to Track

| File | Purpose |
|:---|:---|
| `/etc/rancher/rke2/config.yaml` on `plat-04` | RKE2 cluster config on Secondary Hub |
| `~/.kube/config-plat-03` | Primary Hub kubeconfig |
| `~/.kube/config-plat-04` | Secondary Hub kubeconfig |
| `manifests/cnpg-replica.yaml` | CNPG cross-cluster replica manifest |
| `codeInspector/` | All-in-one Helm chart for deploying `sandbox-api`, Gateway, and system components |
| `codeInspector/values.yaml` | Configuration values for `codeInspector` Helm chart deployment |
| `manifests/failover-cronjob.yaml` | Auto-failover health check CronJob |
