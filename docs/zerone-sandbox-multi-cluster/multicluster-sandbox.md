# 01-Sandbox Multi-Cluster Platform — Team Presentation

> **Audience**: Engineering Team
> **Purpose**: Architecture deep-dive and technical walkthrough of the 01-Sandbox multi-cluster platform
> **Prepared By**: Platform Engineering
> **Date**: October 2026

---

## Table of Contents

1. [What Are We Building?](#1-what-are-we-building)
2. [Architecture Overview (Text Diagram)](#2-architecture-overview-text-diagram)
3. [Networking Layer: WireGuard Encrypted Mesh](#3-networking-layer-wireguard-encrypted-mesh)
4. [Traffic Gateway: Envoy, MetalLB and AgentGateway](#4-traffic-gateway-envoy-metallb-and-agentgateway)
5. [Hub Clusters — The Control Plane](#5-hub-clusters--the-control-plane)
6. [Spoke Clusters — The Execution Layer](#6-spoke-clusters--the-execution-layer)
7. [Hub-Spoke Registration Mechanism](#7-hub-spoke-registration-mechanism)
8. [Data Replication: PostgreSQL and Valkey](#8-data-replication-postgresql-and-valkey)
9. [Failover Mechanism](#9-failover-mechanism)
10. [Split-Brain Prevention and Safe Failback](#10-split-brain-prevention-and-safe-failback)
11. [Reboot Resilience and Persistence](#11-reboot-resilience-and-persistence)
12. [Full State Lifecycle](#12-full-state-lifecycle)
13. [Key Design Decisions and Why We Made Them](#13-key-design-decisions-and-why-we-made-them)
14. [How to Access the Platform](#14-how-to-access-the-platform)
15. [Quick Reference and Operational Runbook](#15-quick-reference-and-operational-runbook)

---

## 1. What Are We Building?

**01-Sandbox** is an enterprise-grade, high-availability **code analysis and execution sandbox platform**. It allows users to submit code for safe, isolated analysis using hardware-enforced container sandboxes (Kata Containers with Firecracker microVMs).

### The Problem We Are Solving

| Challenge | Our Solution |
| :--- | :--- |
| Run untrusted code safely | Kata Containers + gVisor (hardware VM isolation per workload) |
| Single point of failure in management plane | Dual-Hub Active-Passive architecture with automated failover |
| Data loss during failover | Physical WAL streaming replication + authoritative delta sync on failback |
| Complex multi-cluster networking | WireGuard encrypted mesh overlay across all nodes |
| Manual traffic rerouting on outage | Envoy Gateway with sub-second health-check-driven failover |
| Heavy VM infrastructure cost | Pure Docker + KinD — runs on a single workstation (~4 GB RAM) |

### Five Nodes, One Platform

```
 gateway-vm    —  Traffic Front-Door (Envoy Proxy + Virtual IP)
 hub1-vm       —  Primary Hub        (Active Management Control Plane)
 hub2-vm       —  Secondary Hub      (Warm Standby Control Plane)
 spoke1-vm     —  Spoke Cluster 1    (Sandbox Execution Worker)
 spoke2-vm     —  Spoke Cluster 2    (Sandbox Execution Worker)
```

---

## 2. Architecture Overview (Text Diagram)

```
+==============================================================================+
|                  01-SANDBOX MULTI-CLUSTER PLATFORM                           |
|                                                                              |
|   EXTERNAL USERS / OPERATORS / kubectl                                       |
|   +------------------------------------------------------------------+       |
|   |  Browser / API Client / kubectl / Spoke klusterlet agents        |       |
|   |  Target: https://10.99.0.100:6443  |  http://VIP/docs           |       |
|   +-------------------------------+------------------------------------+      |
|                                   |                                          |
|                    All traffic enters through single VIP                     |
|                                   |                                          |
|                                   v                                          |
|   +----------------------------------------------------------------------+   |
|   |           ENVOY GATEWAY  (10.99.0.100 — Virtual IP)                 |   |
|   |           gateway-vm  .  172.30.0.10  .  wg0: 10.99.0.254          |   |
|   |                                                                      |   |
|   |   Port :80   (HTTP application traffic)                              |   |
|   |   Port :6443 (Kubernetes API + OCM klusterlet mTLS)                 |   |
|   |   Active TCP health checks every 1s — Priority-based failover       |   |
|   +------------------+---------------------+-----------------------------+   |
|                      |  Priority 0 (Active)  |  Priority 1 (Standby)        |
|                      v                       v                               |
|   +==========================+   +================================+          |
|   |  PRIMARY HUB (hub1-vm)   |   |  SECONDARY HUB (hub2-vm)       |          |
|   |  Underlay: 192.168.100.20|   |  Underlay: 192.168.101.20      |          |
|   |  WireGuard: 10.99.0.1    |   |  WireGuard: 10.99.0.2          |          |
|   |                          |   |                                |          |
|   |  KinD Kubernetes Cluster |   |  KinD Kubernetes Cluster       |          |
|   |  - sandbox-api (FastAPI) |   |  - sandbox-api (Standby Mode)  |          |
|   |  - OCM Hub (Active)      |<->|  - OCM Hub (Standby)           |          |
|   |  - AgentGateway Proxy    |   |  - AgentGateway Proxy          |          |
|   |  - MetalLB (172.30.0.200)|   |  - MetalLB (172.30.0.201)      |          |
|   |  - PostgreSQL (RW Master)|-->|  - PostgreSQL (WAL Replica)    |          |
|   |  - Valkey Cache (Master) |-->|  - Valkey Cache (Slave)        |          |
|   |                          |   |  - ocm-failover-controller     |          |
|   +==========================+   +================================+          |
|          WAL Stream (NodePort 30432) ------------------------------>          |
|          Valkey Replication (NodePort 30379) ---------------------->          |
|                          |                                                   |
|                          |  OCM ManifestWork (via 10.99.0.100:6443)          |
|                          +--------------------+--------------------           |
|                                               |                              |
|                          +===========================================+        |
|                          |         SPOKE CLUSTERS                   |        |
|                          |                                          |        |
|                          |  spoke1-vm              spoke2-vm        |        |
|                          |  192.168.102.20         192.168.103.20   |        |
|                          |  wg: 10.99.0.3          wg: 10.99.0.4   |        |
|                          |                                          |        |
|                          |  KinD Cluster           KinD Cluster     |        |
|                          |  klusterlet agent       klusterlet agent |        |
|                          |  Kata Containers        Kata Containers  |        |
|                          |  (Firecracker MicroVM)  (Firecracker)    |        |
|                          |  gVisor sandbox         gVisor sandbox   |        |
|                          +===========================================+        |
|                                                                              |
|  ========================================================================    |
|   WIREGUARD ENCRYPTED OVERLAY MESH — 10.99.0.0/24                           |
|   VIP: 10.99.0.100 | GW: 10.99.0.254 | H1:.1 | H2:.2 | S1:.3 | S2:.4      |
|  ========================================================================    |
+==============================================================================+
```

### Node IP Reference Table

| Node | Role | VM Underlay IP | WireGuard Overlay IP | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `gateway-vm` | Traffic Gateway | `192.168.100.10` | `10.99.0.254` | Holds VIP `10.99.0.100` |
| `hub1-vm` | Primary Hub | `192.168.100.20` | `10.99.0.1` | Active; PostgreSQL RW, Valkey Master |
| `hub2-vm` | Secondary Hub | `192.168.101.20` | `10.99.0.2` | Standby; WAL Replica, Valkey Slave |
| `spoke1-vm` | Worker Spoke 1 | `192.168.102.20` | `10.99.0.3` | Kata + gVisor execution |
| `spoke2-vm` | Worker Spoke 2 | `192.168.103.20` | `10.99.0.4` | Kata + gVisor execution |

---

## 3. Networking Layer: WireGuard Encrypted Mesh

### What Is It?

WireGuard is a modern, kernel-integrated VPN protocol that creates encrypted peer-to-peer tunnels between all nodes. Every packet crossing a cluster boundary is encrypted — even between nodes on the same physical host.

### Why Did We Use It?

| Reason | Detail |
| :--- | :--- |
| **Security** | All cross-cluster communication is encrypted end-to-end (API, DB replication, cache, OCM heartbeats) |
| **Isolation** | Prevents direct Docker bridge leakage between clusters |
| **Portability** | Kernel-native — works inside KinD containers running `--privileged` |
| **Performance** | Operates at kernel speed (<1ms overhead), no userspace relay |
| **Simplicity** | Single config file (`wg0.conf`) per node; managed by `wg-quick@wg0.service` |

### How It Works

```
Each node has a WireGuard interface (wg0) with:
  - A private key (generated once, stored in .sandbox-state/)
  - A static overlay IP (10.99.0.x/24)
  - Peer entries for every other node (public key + allowed IPs)

Transit network (172.30.0.0/24) carries only UDP port 51820 (WireGuard).
All TCP traffic (port 6443, 5432, 80, 6379) is BLOCKED on underlay.
Only WireGuard-encrypted packets traverse the transit bridge.
```

### PostUp Routing Rules (per node wg0.conf)

```bash
PostUp = ip rule add from 10.99.0.X table 200 priority 100
         ip route add default dev wg0 table 200
         iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE
         sysctl -w net.ipv4.ip_forward=1
```

> **Why routing table 200?** Reply packets from spoke clusters need to return via `wg0`, not the host default route (underlay NIC). Without table 200, symmetric routing breaks and connections silently drop.

---

## 4. Traffic Gateway: Envoy, MetalLB and AgentGateway

### The Request Flow

```
Browser / API Client
        |
        v
  Envoy Gateway (VIP: 10.99.0.100)
        |  Port :80  ----------------------------------------------------------+
        |                                                                      |
        v                                                                      v
  MetalLB LoadBalancer (172.30.0.200 on PrimaryHub)          MetalLB (172.30.0.201 on SecondaryHub)
        |                                                     [Only active if Primary fails]
        v                                                                      v
  AgentGateway Proxy (L7 Policy Engine)                      AgentGateway Proxy
        |  - Rate limiting (500 req/min)
        |  - Auth enforcement (JWT / API key validation)
        |  - /metrics endpoint access deny
        v
  sandbox-api (FastAPI — ClusterIP)
```

---

### A. Envoy Gateway

#### What Is It?
Envoy is an open-source, high-performance **Layer-4 TCP proxy** originally developed at Lyft. In our setup, it is the single, fixed entry-point (Virtual IP `10.99.0.100`) for all external traffic.

#### What Does It Do?
- Listens on **port 80** (application HTTP traffic) and **port 6443** (Kubernetes API + OCM klusterlet mTLS)
- Routes **100% of traffic to PrimaryHub** (`10.99.0.1`) when it is healthy
- **Automatically fails over to SecondaryHub** (`10.99.0.2`) when Primary fails 2 consecutive health checks (~2 seconds)
- **Kills all stale TCP connections** to the failed node instantly, forcing spoke agents to reconnect to the new active hub

#### Why Did We Use It?

| Problem (Old Approach) | Why Envoy Is Better |
| :--- | :--- |
| `iptables` DNAT rules — blind, no health awareness | Envoy performs **active TCP health checks** every 1 second |
| Shell watchdog (`ocm-vip-watchdog.sh`) — race conditions, zombie rules | Fully declarative YAML config, zero shell loops |
| No connection draining on failure | `close_connections_on_host_health_failure: true` kills stale TCP instantly |
| Multi-second outage during iptables rule flip | Sub-100ms in-memory priority failover, no kernel table flush |

#### Health Check Configuration

```yaml
health_checks:
- timeout: 1s
  interval: 1s              # Probe every 1 second
  unhealthy_threshold: 2    # 2 consecutive failures -> mark UNHEALTHY (~2s)
  healthy_threshold: 2      # 2 consecutive successes -> mark HEALTHY again
  tcp_health_check: {}      # Raw TCP handshake (works for any protocol)
```

#### Connection Recycling (Port 6443 — Kubernetes API)

```yaml
idle_timeout: 5s                         # Close idle connections after 5s
max_downstream_connection_duration: 60s  # Force recycle every 60s
```

> **Why does port 6443 need this?** Kubernetes clients (`kubectl`, OCM spoke agents) hold **long-lived HTTP/2 watch streams** that can persist for hours. Without forced recycling, a client connected before a failover event would remain attached to the dead hub, completely missing that SecondaryHub is now active.

---

### B. MetalLB

#### What Is It?
MetalLB is a **bare-metal LoadBalancer implementation** for Kubernetes. In cloud providers (AWS, GCP, Azure), `LoadBalancer`-type services get a real external IP automatically. MetalLB provides the same capability for on-premises and local Kubernetes clusters.

#### What Does It Do?
MetalLB assigns a real, routable IP address from a configured address pool to services of type `LoadBalancer`.

In our architecture:
- **PrimaryHub MetalLB VIP**: `172.30.0.200`
- **SecondaryHub MetalLB VIP**: `172.30.0.201`

These VIPs are the backend endpoints that Envoy routes `:80` traffic to.

#### Why Did We Use It?
- Kubernetes `NodePort` services require clients to know the exact node IP and port — not suitable when nodes can fail.
- MetalLB gives us a stable, cluster-level IP that works behind Envoy's backend pool — **no hardcoded node IPs in Envoy config**.
- Enables seamless `LoadBalancer`-type services in KinD (which has no cloud provider).

---

### C. AgentGateway

#### What Is It?
AgentGateway is an **L7 (application-layer) policy proxy** that sits between MetalLB and the `sandbox-api` service inside each hub cluster. It enforces intelligent, per-route policies before any request reaches the backend.

#### What Does It Do?
- **Rate limiting**: Enforces per-client request limits (currently 500 req/min per source IP)
- **Authentication enforcement**: Validates JWT tokens and API keys before forwarding to `sandbox-api`
- **Route-level access control**: Blocks access to internal monitoring endpoints (e.g., `/metrics`) from external clients
- **Traffic shaping**: Applies request/response transformations and header injection

#### Why Did We Use It?

| Reason | Benefit |
| :--- | :--- |
| Protect API from abuse | Rate limiting at the gateway — `sandbox-api` never sees abusive traffic |
| Centralized auth enforcement | Auth policy lives in one place; `sandbox-api` does not duplicate it |
| Decouple policy from application code | Rate limit values can be changed in `values.yaml` without code changes |
| Observability | AgentGateway emits per-route metrics (requests/s, latency, error rates) |

> **Note**: Rate limit was tuned from 7 req/min (too low for browser navigation) to **500 req/min** to support real-world dashboard usage without HTTP 429 errors.

#### Full Request Path Summary

```
Client Request
  -> Envoy Gateway      (VIP: 10.99.0.100, L4 TCP proxy, health-aware failover)
  -> MetalLB            (assigns cluster-level stable IP, routes to pod)
  -> AgentGateway       (L7 policy: rate limit, auth, ACL)
  -> sandbox-api FastAPI (business logic, PostgreSQL, Valkey)
```

---

## 5. Hub Clusters — The Control Plane

Hub clusters are **management clusters** running Open Cluster Management (OCM). They do not execute user workloads. They are the **brain** of the multi-cluster system.

### What Is OCM?

Open Cluster Management (OCM) is a CNCF project that provides a unified API and control plane for managing multiple Kubernetes clusters. It follows the hub-spoke model:
- **Hub** = the central cluster that manages others
- **Spoke (ManagedCluster)** = clusters registered to and controlled by the hub

### Primary Hub (hub1-vm) — Active Mode

| Component | Purpose |
| :--- | :--- |
| **OCM Hub Controller** | Accepts spoke cluster registrations, distributes `ManifestWork` |
| **sandbox-api (FastAPI)** | User-facing REST API: create API keys, submit scan jobs, view results |
| **PostgreSQL (CNPG RW Master)** | Primary read-write database: API keys, job history, user data |
| **Valkey Cache (Master)** | In-memory cache for active tokens and hot query results |
| **AgentGateway Proxy** | L7 policy enforcement (rate limiting, auth) |
| **MetalLB** | Exposes `AgentGateway` via stable LoadBalancer VIP `172.30.0.200` |
| **node-network-agent** | DaemonSet ensuring `ip_forward=1` is set on all KinD nodes |

**Cluster Subnets**: Pod CIDR `10.244.0.0/16` | Service CIDR `10.96.0.0/16` | WireGuard IP `10.99.0.1`

### Secondary Hub (hub2-vm) — Warm Standby Mode

| Component | Purpose |
| :--- | :--- |
| **OCM Hub Controller** | Pre-configured, running but spokes are NOT accepted (standby) |
| **sandbox-api (Standby)** | Runs but does not serve writes while Primary is alive |
| **PostgreSQL (CNPG Replica)** | Continuously receives WAL stream from PrimaryHub (zero lag target) |
| **Valkey Cache (Slave)** | Replicates all keys from PrimaryHub's Valkey Master |
| **ocm-failover-controller** | The critical watchdog — monitors Primary health every 1 second |
| **AgentGateway Proxy** | Same policy config as PrimaryHub (ready to serve on failover) |
| **MetalLB** | VIP `172.30.0.201` (becomes active Envoy backend on failover) |

**Cluster Subnets**: Pod CIDR `10.245.0.0/16` | Service CIDR `10.97.0.0/16` | WireGuard IP `10.99.0.2`

#### Why Two Hubs?

Single-hub architectures have a fatal flaw: **if the hub goes down, you lose visibility and control over all spoke clusters**. With dual hubs:
- SecondaryHub is always warm and current (streaming DB replication = near-zero data lag)
- Failover is **fully automated** — no human intervention required
- Failback after Primary recovery is also **fully automated** with zero split-brain

---

## 6. Spoke Clusters — The Execution Layer

Spoke clusters are **worker clusters** that receive workloads from the hub and execute them in isolation. They have **no management responsibilities** — they just run jobs.

### What Do Spokes Do?

1. **Receive job manifests** from the hub via OCM `ManifestWork` API
2. **Execute code analysis sandboxes** using Kata Containers (Firecracker microVMs) and gVisor
3. **Report results** back to the hub
4. **Maintain a heartbeat lease** with the hub OCM controller every 60 seconds

### Spoke Configuration

| Property | Spoke 1 | Spoke 2 |
| :--- | :--- | :--- |
| VM Host | `spoke1-vm` | `spoke2-vm` |
| Underlay IP | `192.168.102.20` | `192.168.103.20` |
| WireGuard IP | `10.99.0.3` | `10.99.0.4` |
| Pod CIDR | `10.246.0.0/16` | `10.247.0.0/16` |
| Service CIDR | `10.98.0.0/16` | `10.100.0.0/16` |
| OCM ClusterSet | `sandbox-spokes` | `sandbox-spokes` |

### Spoke Runtime Labels

```yaml
sandbox-workload-capable: "true"   # Eligible to receive sandbox jobs
runtime.gvisor: "true"             # Has gVisor (runsc) runtime class installed
runtime.kata: "true"               # Has Kata Containers (Firecracker) installed
wireguard-ip: "10.99.0.3"         # Used for direct WireGuard routing
```

### Why Kata Containers + Firecracker?

Standard Docker containers share the host kernel — a kernel exploit can compromise the entire host. This is unacceptable for running untrusted user code.

| Isolation Technology | Mechanism | Use Case |
| :--- | :--- | :--- |
| **Kata Containers** | Each container gets its own lightweight Firecracker microVM (<125ms boot) | Strong hardware-enforced isolation |
| **gVisor** | Userspace kernel intercepts all syscalls (Go implementation) | Lightweight sandboxing for less-trusted code |

### Spoke devmapper Storage (Persistence After Restart)

Spokes use **Linux LVM thin-pool + devmapper** as the containerd snapshotter for Kata workloads. A systemd drop-in (`10-devmapper.conf`) ensures correct boot ordering:

```
lvm2-monitor.service
      |  After=
      v
init-containerd-devmapper.service   <- activates thin pool, creates /dev/mapper nodes
      |  After=
      v
containerd.service                  <- only starts when devmapper is ready
      |  After=
      v
kubelet.service                     <- only starts when containerd is ready
```

> This ensures spoke clusters **survive host reboots** without manual intervention.

---

## 7. Hub-Spoke Registration Mechanism

### Registration Flow (Step-by-Step)

```
Step 1: Bootstrap Token Request
  PrimaryHub generates a join token:
    clusteradm get token --context kind-primaryhub
  Token = short-lived RBAC bootstrap credential

Step 2: Spoke Joins via VIP (Not Direct Hub IP)
  On spoke cluster:
    clusteradm join \
      --hub-token <token> \
      --hub-apiserver https://10.99.0.100:6443 \   <- VIP, NOT 10.99.0.1
      --cluster-name spoke1

  WHY VIP? If spoke joins via direct hub IP (10.99.0.1) and PrimaryHub
  fails, the spoke kubeconfig points to a dead server. Joining via VIP
  means Envoy can transparently redirect to SecondaryHub without
  updating any spoke configs.

Step 3: klusterlet Agent Deployment
  OCM installs two components on the spoke:
    - klusterlet-registration-agent  (handles TLS CSR and cluster identity)
    - klusterlet-work-agent          (downloads and applies ManifestWork)

Step 4: CSR Generation
  The spoke generates an X.509 CSR with its cluster identity.
  Sends it to the hub's kube-apiserver via https://10.99.0.100:6443.

Step 5: Hub Auto-Approval (ocm-auto-acceptor)
  ocm-auto-acceptor controller on the hub:
    a. Watches for new ManagedCluster objects (status: Pending)
    b. Automatically approves the CSR (no manual kubectl needed)
    c. Sets hubAcceptsClient: true on the ManagedCluster

Step 6: ManagedCluster Object Created
  Hub creates:
    ManagedCluster: spoke1
      spec.hubAcceptsClient: true
      status.conditions:
        - ManagedClusterJoined: True
        - ManagedClusterConditionAvailable: True

Step 7: Spoke Heartbeat Lease (Health Signaling)
  klusterlet-registration-agent creates and renews a Lease object
  in its own namespace on the hub every 60 seconds.
  Hub OCM controller monitors these leases. If a lease is not renewed
  within the grace period -> spoke is marked UNAVAILABLE.
```

### Standby Registration on SecondaryHub

During initial setup, spokes are **pre-registered** on SecondaryHub but kept in standby mode:

```yaml
# On SecondaryHub — spoke is pre-registered but BLOCKED
ManagedCluster: spoke1
  spec:
    hubAcceptsClient: false    <- Hub does NOT accept spoke
    taints:
    - key: cluster.open-cluster-management.io/unreachable
      effect: NoSelect         <- OCM Placement engine skips this spoke
```

This means **SecondaryHub already knows about the spokes** — no re-registration is needed on failover. The failover controller simply patches `hubAcceptsClient: true` and removes the taint.

---

## 8. Data Replication: PostgreSQL and Valkey

### PostgreSQL — Physical WAL Streaming Replication

WAL (Write-Ahead Log) is PostgreSQL's internal transaction journal. Every change is written to WAL first, then applied to data files. Physical streaming replication sends this WAL stream from Primary to Replica in real time.

```
PrimaryHub PostgreSQL (RW Master)
   |
   |  Physical WAL stream (binary, byte-for-byte)
   |  NodePort 30432 -> WireGuard -> 10.99.0.1:5432
   v
SecondaryHub PostgreSQL (WAL Replica)
   - Applies WAL continuously (lag target: ~0ms)
   - Read-Only while Primary is alive
   - On failover: pg_promote() makes it Read-Write in < 500ms
```

**Managed by**: CloudNativePG (CNPG) — a Kubernetes operator for PostgreSQL. Handles replication slot management, WAL archival, connection pooling, and failover promotion.

### Valkey — In-Memory Cache Replication

```
PrimaryHub Valkey (Master)
   |
   |  Full in-memory dataset synchronization
   |  NodePort 30379 -> WireGuard -> 10.99.0.1:6379
   v
SecondaryHub Valkey (Slave)
   - Mirrors all keys from Master in real time
   - Read-Only while Primary is alive
   - On failover: REPLICAOF NO ONE -> becomes Master
```

---

## 9. Failover Mechanism

### The ocm-failover-controller

The `ocm-failover-controller` is a **Kubernetes-native controller** running as a Deployment on **SecondaryHub** inside the `opensandbox-system` namespace. It is the automation engine for the entire failover and failback lifecycle.

#### Why Kubernetes-Native (Not a systemd daemon)?

| Old Approach (systemd bash daemon) | New Approach (Kubernetes Deployment) |
| :--- | :--- |
| State lost on pod/host restart | State restored from live cluster resources on pod restart |
| No RBAC — ran with cluster-admin kubeconfig | Least-privilege RBAC (ClusterRole with exact verbs per resource) |
| Invisible to `kubectl` | Fully inspectable via `kubectl get/logs/describe` |
| No retry on crash | Kubernetes restart policy handles crashes automatically |
| Not version-locked to app | Version-locked to same Helm release as all other components |

#### Controller Resources in Kubernetes

```
failover-controller.yaml (Helm template — enabled only on SecondaryHub)
|
+-- ServiceAccount       ocm-failover-controller-sa
+-- ClusterRole          ocm-failover-controller-role  (least-privilege)
+-- ClusterRoleBinding   ocm-failover-controller-rb
+-- ConfigMap            ocm-failover-controller-script
|     +-- reconciler.sh          <- The main controller loop (PID 1)
|     +-- secondary-cluster.yaml <- CNPG re-clone manifest (for failback)
|     +-- delta-sync-job.yaml    <- Failback delta sync Job manifest
+-- Deployment           ocm-failover-controller
      Mounts ConfigMap at /scripts/ and runs reconciler.sh
```

> Scripts live **inside a ConfigMap** — not on any VM disk. Version-locked to the Helm release.

### Failover Detection

```
reconciler.sh — continuous loop (every 1 second):
  curl -k -m 1 -s https://10.99.0.1:6443/livez -> HTTP 200 = HEALTHY
                                                 -> FAIL/timeout = failure++

  FAIL_THRESHOLD = 2 consecutive failures (~4 seconds detection window)

  Simultaneously, Envoy Gateway detects 2 consecutive failed TCP probes on 10.99.0.1
  -> Shifts all :80 and :6443 traffic to 10.99.0.2 in-memory (< 100ms)
  -> close_connections_on_host_health_failure kills all stale TCP sessions
```

### Failover Sequence — PrimaryHub Goes DOWN

```
+================================================================+
|  FAILOVER: PrimaryHub DOWN                                     |
+================================================================+

STEP 1 — Envoy (automatic, < 100ms):
  - 10.99.0.1:6443 fails 2 TCP health checks
  - All traffic shifted to 10.99.0.2 in-memory
  - All stale TCP connections to Primary killed (TCP RST)

STEP 2 — ocm-failover-controller (within ~4 seconds):
  2a. Patch ManagedCluster 'spoke1' and 'spoke2':
        hubAcceptsClient: true
        taints: []                    <- Remove NoSelect taint
      Spokes reconnect to SecondaryHub OCM immediately

  2b. Approve pending spoke TLS CSRs (auto-approve)

  2c. Promote PostgreSQL:
        kubectl patch cluster postgresql-secondary \
          -p '{"spec":{"replica":{"enabled":false}}}'
      In-place pg_promote() — pod stays alive, < 500ms
      SecondaryHub DB is now Read-Write

  2d. Promote Valkey:
        valkey-cli REPLICAOF NO ONE
      Valkey becomes standalone master

RESULT:
  SecondaryHub is now fully active.
  Total time from Primary down to Secondary fully active: ~6-10 seconds
  Data loss: ZERO (WAL replication lag was ~0ms before failure)

+================================================================+
```

### Live Test Evidence (2026-09-24)

```
[06:08:59Z] Primary check failed (1/2)
[06:09:01Z] Primary check failed (2/2)
[06:09:01Z] CRITICAL: PrimaryHub is DOWN. Triggering automated in-place failover...
[06:09:01Z] Step 1: Accepting and untainting OCM spoke clusters...
[06:09:02Z] Step 2: Promoting PostgreSQL secondary to Read-Write...
[06:09:03Z] Step 3: Promoting Valkey to master...
[06:09:04Z] SUCCESS: Automated Failover complete. Zero downtime!
```

**Total elapsed time: ~5 seconds from Primary failure to full SecondaryHub activation.**

---

## 10. Split-Brain Prevention and Safe Failback

### What Is Split-Brain?

Split-brain occurs when two nodes both believe they are the master for the same dataset. In our case:
- PrimaryHub recovers after failover
- Both hubs' PostgreSQL are in Read-Write mode
- Different data has been written to each
- They are now permanently out of sync

### The Old Approach (Flawed)

```bash
# Old delta sync — additive only
pg_dump --data-only --inserts --on-conflict-do-nothing | grep '^INSERT' | psql ...
```

This only inserts new rows. **Deletions are completely ignored.** A key deleted on SecondaryHub during the outage would **resurrect** on PrimaryHub when it came back.

### Failback Sequence — PrimaryHub RECOVERED

```
+================================================================+
|  FAILBACK: PrimaryHub RECOVERED                                |
+================================================================+

STEP 1 — Envoy (automatic, < 100ms):
  - 10.99.0.1:6443 passes 2 consecutive TCP health checks
  - Traffic shifts back to 10.99.0.1 (PrimaryHub active again)

STEP 2 — ocm-failover-controller:
  Patch spokes back to standby on SecondaryHub:
    hubAcceptsClient: false
    taints: [cluster.open-cluster-management.io/unreachable: NoSelect]
  Delete spoke Lease objects on SecondaryHub
  -> Forces immediate OCM re-registration on PrimaryHub
  -> Without this, spokes wait up to 60s for lease expiry

STEP 3 — Health Gate:
  Poll pg_isready on 10.99.0.1:5432 until PostgreSQL accepts connections
  Prevents delta sync racing against PrimaryHub crash recovery WAL replay

STEP 4 — Authoritative Delta Sync (Kubernetes Job):
  Submit failback-delta-sync Job using postgres:15-alpine:

    pg_terminate_backend() on PrimaryHub  <- Kill stale connections (release locks)
    pg_dump --clean --if-exists           <- SecondaryHub (authoritative) -> PrimaryHub
    CHECKPOINT                            <- Flush to disk

  WHY --clean?
  --clean generates DROP TABLE IF EXISTS first, then inserts current data.
  SecondaryHub becomes the single source of truth.
  Deletions made during outage ARE honored. No ghost records.

STEP 5 — Valkey Bidirectional Sync:
  1. PrimaryHub Valkey: REPLICAOF SecondaryHub  (pulls memory from Secondary)
  2. PrimaryHub Valkey: REPLICAOF NO ONE        (becomes standalone master)
  3. SecondaryHub Valkey: REPLICAOF PrimaryHub  (returns to slave mode)

STEP 6 — PostgreSQL Re-Clone (Timeline Parity):
  Problem:
    When Secondary was promoted, its timeline incremented: 1 -> 2
    PrimaryHub is still on Timeline 1.
    Streaming replication is PERMANENTLY BROKEN without a re-clone.

  Solution (declarative, automated):
    kubectl delete cluster postgresql-secondary
    kubectl apply -f /scripts/secondary-cluster.yaml
    -> CNPG runs pg_basebackup from PrimaryHub (now has authoritative data)
    -> Secondary returns to Timeline 1 and resumes WAL streaming

RESULT:
  100% data parity. Zero ghost records. Zero split-brain.
  Downtime during failback: ZERO (Envoy switched back instantly in STEP 1)

+================================================================+
```

### Comparison: Old vs. Current Behavior

| Dimension | Old Behavior | Current Behavior |
| :--- | :--- | :--- |
| Delta sync philosophy | Additive only (`--on-conflict-do-nothing`) | Full authoritative state transfer (`--clean --if-exists`) |
| Deletions handling | Ignored — deleted records resurrected | Honored — deleted records removed from Primary |
| Cache sync (Valkey) | One-way only | Bidirectional handshake (Secondary -> Primary -> Replica) |
| Lock handling | Transaction timeouts | Stale sessions terminated via `pg_terminate_backend` |
| Timeline parity | Manual intervention required | Automated declarative re-clone via CNPG |

---

## 11. Reboot Resilience and Persistence

### Problem

When a VM or host machine reboots: Docker containers stop, WireGuard interfaces disappear, devmapper LVM thin pools may not re-activate, and KinD containers may not recover in the right order.

### Layered Persistence Strategy

#### Layer 1 — State on Disk (Outside Container Lifecycles)

All cryptographic material and configuration is stored in `.sandbox-state/` on the host disk:

```
.sandbox-state/
  +-- wg-keys/          <- WireGuard private keys (generated once, reused on restart)
  +-- pki/              <- Shared Root CA certificates
  +-- envoy.yaml        <- Envoy configuration
  +-- cluster.env       <- Node IP configuration
```

#### Layer 2 — Container Restart Policies

All containers use `restart: unless-stopped`. They automatically restart after host reboot.

#### Layer 3 — WireGuard via systemd

`wg-quick@wg0.service` is enabled on all VMs. On boot, it reads `/etc/wireguard/wg0.conf` and restores the `wg0` interface, overlay IP, and routing rules atomically.

#### Layer 4 — devmapper for Spoke Clusters

```
Systemd boot order on spoke VMs:
  lvm2-monitor.service
       |  After=
       v
  init-containerd-devmapper.service   <- activates /dev/vg0/thinpool
       |  After=
       v
  containerd.service
       |  After=
       v
  kubelet.service
```

#### Layer 5 — OCM Controller Auto-Reconnect

The `klusterlet` agents on spokes continuously retry their connection to `https://10.99.0.100:6443`. After a host reboot, once Envoy is up and the hub cluster is healthy, spokes automatically re-register and `ManagedCluster` status returns to `Available: True`.

---

## 12. Full State Lifecycle

```
+================================================================+
|                 STATE LIFECYCLE DIAGRAM                        |
+================================================================+

  NORMAL STATE
  +---------------------------------------------------------+
  |  PrimaryHub:   Active RW master (DB + Valkey)           |
  |  SecondaryHub: Warm standby (WAL streaming, Valkey rep) |
  |  Envoy VIP:    Priority 0 -> 10.99.0.1 (:80 + :6443)   |
  |  Controller:   STANDBY loop — probing /livez every 1s   |
  |  Spokes:       Registered to PrimaryHub (ACCEPTED=true) |
  +---------------------------+-----------------------------+
                              |
                              | PrimaryHub GOES DOWN
                              | (2 consecutive probe failures ~4s)
                              v
  FAILOVER TRIGGERED
  +---------------------------------------------------------+
  |  Envoy:        Traffic -> 10.99.0.2  (< 100ms)         |
  |                Kills stale TCP to Primary               |
  |  Controller:   STANDBY -> FAILOVER_ACTIVE               |
  |    1. Spokes untainted (ACCEPTED on SecondaryHub)       |
  |    2. Approve spoke TLS CSRs                            |
  |    3. CNPG: spec.replica.enabled=false (pg_promote)     |
  |    4. Valkey: REPLICAOF NO ONE (Valkey master)          |
  +---------------------------+-----------------------------+
                              |
                              | [Outage period — Secondary is fully active]
                              | API keys created, scans run
                              | Data written to SecondaryHub DB
                              |
                              | PrimaryHub RECOVERS
                              v
  AUTOMATED FAILBACK (ZERO SPLIT-BRAIN)
  +---------------------------------------------------------+
  |  Envoy:        Traffic -> 10.99.0.1  (< 100ms)         |
  |  Controller:   FAILOVER_ACTIVE -> STANDBY               |
  |    1. Spokes tainted back to standby on SecondaryHub    |
  |       Lease deleted -> instant OCM re-reg on Primary    |
  |    2. Health gate: wait for Primary PG pg_isready       |
  |    3. failback-delta-sync Job (pg_dump --clean)         |
  |    4. Valkey bidirectional sync                         |
  |    5. CNPG delete + re-clone (Timeline 1 restored)      |
  +---------------------------+-----------------------------+
                              |
                              v
                  Returns to NORMAL STATE
                  100% data parity, zero split-brain
```

---

## 13. Key Design Decisions and Why We Made Them

### 1. Pure Docker + KinD (No Heavy VMs)

**Decision**: Run all 4 Kubernetes clusters as Docker containers using KinD instead of 4 KVM virtual machines.

**Why**:
- **Cost**: 4 KVM VMs require 4x RAM allocation (min 4 GB each = 16 GB). KinD runs everything in ~3.5–4.5 GB.
- **Speed**: KinD cluster creation takes ~30 seconds vs. ~5 minutes per VM boot.
- **CI/CD friendly**: Runs directly in GitHub Actions runners without nested virtualization.
- **Portability**: Anyone with Docker and 8 GB RAM can reproduce the entire environment.

### 2. WireGuard-Only Cross-Cluster Communication

**Decision**: Enforce that ALL cross-cluster traffic MUST traverse the WireGuard encrypted overlay.

**Why**: Prevents data leakage between cluster networks even if Docker bridge isolation is bypassed. Provides a single, auditable security perimeter.

### 3. Envoy Over iptables DNAT

**Decision**: Replace legacy `iptables` DNAT rules and shell watchdog with Envoy Gateway.

**Why**: `iptables` has no health awareness — it blindly routes to dead endpoints. Shell watchdogs introduce race conditions. Envoy delivers sub-100ms in-memory failover with zero kernel table manipulation.

### 4. Kubernetes-Native Failover Controller

**Decision**: Deploy `ocm-failover-controller` as a Kubernetes Deployment on SecondaryHub.

**Why**: State is preserved across pod restarts. Fully inspectable via `kubectl`. Version-locked to Helm release. RBAC-isolated with least-privilege permissions.

### 5. Authoritative Delta Sync (Full State Transfer vs. Additive Merge)

**Decision**: Use `pg_dump --clean --if-exists` during failback instead of additive `INSERT --on-conflict-do-nothing`.

**Why**: Additive-only sync cannot honor **deletions** — records deleted during outage resurrect on failback. `--clean` drops tables first then inserts current data — SecondaryHub becomes the single source of truth.

### 6. Spoke Registration via VIP (Not Direct Hub IP)

**Decision**: All spokes join via `https://10.99.0.100:6443` (Envoy VIP), not `10.99.0.1` (PrimaryHub direct).

**Why**: If a spoke is registered to `10.99.0.1` directly and PrimaryHub fails, the spoke kubeconfig points to a dead server. Registering via VIP means Envoy handles routing transparently — spoke configs never need to change.

---

## 14. How to Access the Platform

### API Endpoints (Browser / curl)

| Endpoint | Purpose | URL |
| :--- | :--- | :--- |
| Interactive API Docs (Swagger) | Test all API endpoints | `http://172.30.0.100/docs` |
| Health Check | System component status | `http://172.30.0.100/health` |
| OpenAPI Schema | Raw JSON API spec | `http://172.30.0.100/openapi.json` |
| MetalLB Ingress VIP | Direct cluster ingress | `http://172.18.255.200` |
| Kubernetes API VIP | kubectl / OCM spoke endpoint | `https://10.99.0.100:6443` |

### Kubernetes Contexts

```bash
# Switch to Primary Hub (active master)
kubectl config use-context kind-primaryhub

# Switch to Secondary Hub (warm standby)
kubectl config use-context kind-secondaryhub

# Switch to Spoke 1
kubectl config use-context kind-spoke1

# Switch to Spoke 2
kubectl config use-context kind-spoke2
```

### Check Cluster Health

```bash
# Run built-in health verification
./docker-multi-cluster.sh --verify

# Check all managed clusters on PrimaryHub
kubectl --context kind-primaryhub get managedclusters

# Check Envoy upstream health
curl -s http://127.0.0.1:9901/clusters | grep -E "health_flags|priority"
```

---

## 15. Quick Reference and Operational Runbook

### Standing Up the Platform from Scratch

```bash
# 1. Give execution permission
chmod +x docker-multi-cluster.sh

# 2. Run one-shot automated provisioning (~3-5 min first run)
./docker-multi-cluster.sh

# 3. Verify all components are healthy
./docker-multi-cluster.sh --verify
```

### Simulate Failover (Test)

```bash
# Stop PrimaryHub
docker stop primaryhub-control-plane

# Watch controller logs (should see "SUCCESS: Automated Failover complete" within ~10s)
kubectl --context kind-secondaryhub -n opensandbox-system \
  logs -l app.kubernetes.io/name=ocm-failover-controller -f
```

### Restore PrimaryHub (Failback)

```bash
# Start PrimaryHub back up
docker start primaryhub-control-plane

# Watch controller logs (should see "SUCCESS: Automated Failback completed!" within ~90s)
kubectl --context kind-secondaryhub -n opensandbox-system \
  logs -l app.kubernetes.io/name=ocm-failover-controller -f
```

### Verify Data Parity After Failback

```bash
# Check API keys on PrimaryHub
kubectl --context kind-primaryhub exec -n opensandbox-system \
  postgresql-primary-1 -c postgres -- \
  psql -U postgres -d apikeys -c "SELECT id, name, created_at FROM api_keys;"

# Check PostgreSQL replication status
kubectl --context kind-secondaryhub get cluster.postgresql.cnpg.io -n opensandbox-system

# Check Valkey is back in slave mode
kubectl --context kind-secondaryhub exec -n opensandbox-system deploy/valkey -- \
  valkey-cli info replication | grep -E "role|master_host|master_link_status"
```

### Tear Down Everything

```bash
./docker-multi-cluster-destroy.sh
```

### Log Locations

| Component | How to View Logs |
| :--- | :--- |
| Failover Controller | `kubectl --context kind-secondaryhub -n opensandbox-system logs -l app.kubernetes.io/name=ocm-failover-controller -f` |
| sandbox-api | `kubectl --context kind-primaryhub -n opensandbox-system logs -l app.kubernetes.io/name=sandbox-api -f` |
| PostgreSQL | `kubectl --context kind-primaryhub -n opensandbox-system logs postgresql-primary-1 -c postgres` |
| Envoy Gateway | `docker logs envoy-gateway --tail=50 -f` |
| Spoke klusterlet | `kubectl --context kind-spoke1 -n open-cluster-management-agent logs -l app=klusterlet -f` |

---

## Appendix: Component Interaction Summary

```
+------------------------------------------------------------------+
|  COMPONENT          | PROTOCOL | PORT   | PURPOSE                |
+------------------------------------------------------------------+
|  Envoy Gateway      | TCP      | :80    | HTTP API ingress       |
|  Envoy Gateway      | TCP      | :6443  | Kubernetes API proxy   |
|  WireGuard (wg0)    | UDP      | :51820 | Encrypted mesh tunnel  |
|  CNPG Replication   | TCP      | :30432 | PostgreSQL WAL stream  |
|  Valkey Replication | TCP      | :30379 | In-memory cache sync   |
|  OCM klusterlet     | HTTPS    | :6443  | Spoke->Hub heartbeat   |
|  MetalLB VIP        | TCP      | :80    | Cluster LB VIP         |
|  AgentGateway       | HTTP     | :80    | L7 policy (auth/rate)  |
|  Envoy Admin        | HTTP     | :9901  | Health status dashboard|
+------------------------------------------------------------------+
```

---

*This document reflects the live architecture of the 01-Sandbox platform as of October 2026.*
*All IP addresses, port numbers, timings, and behavioral descriptions reflect the actual deployed configuration.*
