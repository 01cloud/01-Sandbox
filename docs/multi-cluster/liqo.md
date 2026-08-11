# Liqo Multi-Cluster: Virtual Node Topology & Automated Pod Offloading

> **Core Architecture Principle:** Peer independent Kubernetes clusters into a **single unified virtual cluster** without installing complex policy engines (no Karmada, no OCM).
>
> Remote Kubernetes clusters are presented to your primary control plane as **Virtual Nodes**. To Kubernetes and standard schedulers, remote clusters look like standard high-capacity worker nodes (`node-ovh-spoke-1`).
>
> **Application Impact:** **Zero code or configuration changes.** Your FastAPI `sandbox-api`, Python workers (`consumer.py`), RabbitMQ queues, and PostgreSQL databases connect using standard Kubernetes DNS service names (`rabbitmq-service:5672`, `postgresql-service:5432`). You deploy manifests to a single Kubernetes cluster as usual.

---

## Table of Contents

1. [Executive Summary & Why Liqo Wins](#1-executive-summary--why-liqo-wins)
2. [Deep Architectural & Component Breakdown](#2-deep-architectural--component-breakdown)
3. [Architecture & Topology Diagrams](#3-architecture--topology-diagrams)
4. [Prerequisites & Cluster Requirements](#4-prerequisites--cluster-requirements)
5. [Step-by-Step Installation & Configuration Guide](#5-step-by-step-installation--configuration-guide)
6. [01-Sandbox Namespace Offloading & Placement](#6-01-sandbox-namespace-offloading--placement)
7. [Traffic Execution & Sequence Flow](#7-traffic-execution--sequence-flow)
8. [Resource & Network Reflection Mechanics](#8-resource--network-reflection-mechanics)
9. [Verification & Troubleshooting Handbook](#9-verification--troubleshooting-handbook)

---

## 1. Executive Summary & Why Liqo Wins

Traditional multi-cluster solutions require managing multiple Kubernetes API endpoints, writing custom placement policies (`PropagationPolicy`), and maintaining complex hub-and-spoke state.

**Liqo abstracts remote clusters into Virtual Nodes inside your primary cluster:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       PRIMARY RKE2 KUBERNETES CONTROL PLANE                 │
│              (You interact with 1 single API Server via kubectl)            │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
┌───────────────────────┐                             ┌───────────────────────┐
│   PHYSICAL NODE       │                             │   LIQO VIRTUAL NODE   │
│   (bb-mp-plat-03)     │                             │   (liqo-ovh-spoke-1)  │
│   - sandbox-api       │                             │   - Kata Scan Pods    │
│   - RabbitMQ          │                             │   - Remote Workers    │
│   - PostgreSQL        │                             │                       │
└───────────────────────┘                             └───────────┬───────────┘
                                                                  │
                                                        WireGuard │ Tunnel
                                                                  ▼
                                                      ┌───────────────────────┐
                                                      │  REMOTE OVH CLUSTER   │
                                                      │  (Physical Compute)   │
                                                      └───────────────────────┘
```

### Key Advantages of Liqo for `01-Sandbox`:

1. **Single Control Plane Management:** You deploy Helm charts and Kubernetes manifests to **one** cluster. The Kubernetes scheduler handles placing pods onto physical or virtual nodes.
2. **Automatic IP Collision Resolution:** Unlike standard network meshes that fail if Pod CIDRs overlap, Liqo includes built-in NAT translation. It connects clusters with overlapping `10.42.0.0/16` subnets automatically!
3. **Selective Namespace Offloading:** You control granularly which namespaces (e.g. `opensandbox-system`) offload pods to remote clusters, while database and control components remain local.
4. **No Custom Placement YAMLs:** Standard Kubernetes `nodeSelector`, `tolerations`, and `nodeAffinity` rules work out of the box.

---

## 2. Deep Architectural & Component Breakdown

Liqo operates via four primary architectural subsystems running on each peered cluster:

```
                  PRIMARY CLUSTER                                     REMOTE CLUSTER
┌──────────────────────────────────────────────┐┌──────────────────────────────────────────────┐
│                                              ││                                              │
│  ┌────────────────┐      ┌────────────────┐  ││  ┌────────────────┐      ┌────────────────┐  │
│  │ sandbox-api    │      │ Virtual Node   │  ││  │ Actual Pod     │      │ Actual Pod     │  │
│  └───────┬────────┘      └───────┬────────┘  ││  └───────▲────────┘      └───────▲────────┘  │
│          │                       │           ││          │                       │           │
│ ─────────┼───────────────────────┼────────── ││ ─────────┼───────────────────────┼────────── │
│          ▼                       ▼           ││          │                       │           │
│  ┌────────────────────────────────────────┐  ││  ┌───────┴────────────────────────┴───────┐  │
│  │      Liqo Resource Reflector           │◄═┼┼═►│       Liqo Remote Controller           │  │
│  └───────────────────┬────────────────────┘  ││  └───────────────────┬────────────────────┘  │
│                      │                       ││                      │                       │
│                      ▼                       ││                      ▼                       │
│  ┌────────────────────────────────────────┐  ││  ┌────────────────────────────────────────┐  │
│  │     Liqo Network Fabric (WireGuard)    │◄═┼┼═►│     Liqo Network Fabric (WireGuard)    │  │
│  └────────────────────────────────────────┘  ││  └────────────────────────────────────────┘  │
│                                              ││                                              │
└──────────────────────────────────────────────┘└──────────────────────────────────────────────┘
```

### Subsystems Explained

#### 1. Virtual Kubelet
* An implementation of the Kubernetes Virtual Kubelet interface.
* **Role:** Registers as a node (`liqo-ovh-spoke-1`) in the primary cluster. When the primary Kubernetes scheduler assigns a pod to this node, the Virtual Kubelet intercepts the create request and forwards it to the remote cluster's API server.

#### 2. Resource Reflector
* Synchronizes essential Kubernetes objects between the primary and remote cluster.
* **Role:** Replicates `Pods`, `Services`, `ConfigMaps`, `Secrets`, and `Endpoints`. When a service is created in the primary cluster, the reflector ensures remote worker pods on virtual nodes can discover and connect to it.

#### 3. Network Fabric & IPAM
* Establishes eBPF-assisted **WireGuard** tunnels between clusters (UDP port 51820).
* **Role:** Manages dynamic NAT mapping. If both primary and remote clusters use `10.42.0.0/16`, Liqo translates pod IPs on the fly so cross-cluster communication never collides.

#### 4. Authentication Engine
* Handles mutual TLS handshake and cluster registration token exchange during `liqoctl peer`.

---

## 3. Architecture & Topology Diagrams

### 3.1 End-to-End Topology with Liqo Virtual Nodes

```mermaid
flowchart TD
    subgraph Clients["Users & Webhooks"]
        Client["API Clients / GitHub Webhooks"]
    end

    subgraph PrimaryCluster["Primary RKE2 Cluster (bb-mp-plat-03)"]
        GW["agentgateway-proxy<br/>(10.0.8.9)"]
        API["sandbox-api (FastAPI)"]
        RMQ[("RabbitMQ Service")]
        DB[("PostgreSQL Database")]
        LocalWorker["Local Scan Pods<br/>(Scheduled on Physical Node)"]

        subgraph VirtualNodes["Liqo Virtual Node Abstraction"]
            VN1["Virtual Node: liqo-ovh-spoke-1"]
            VN2["Virtual Node: liqo-ovh-spoke-2"]
        end
    end

    subgraph LiqoFabric["Liqo Cross-Cluster Fabric"]
        Tunnel["WireGuard Tunnel & NAT Engine<br/>- Encrypted Pod-to-Pod Communication<br/>- Auto IP Collision Translation"]
    end

    subgraph RemoteCluster1["Remote OVH Cluster 1 (Physical Compute)"]
        R1_Workers["Kata/Firecracker Scan Pods<br/>(Offloaded via VN1)"]
    end

    subgraph RemoteCluster2["Remote OVH Cluster 2 (Physical Compute)"]
        R2_Workers["Kata/Firecracker Scan Pods<br/>(Offloaded via VN2)"]
    end

    %% Client flows
    Client --> GW --> API --> RMQ
    API --> DB

    %% Scheduling flows
    RMQ -- "Schedule on Physical" --> LocalWorker
    RMQ -- "Schedule on VN1" --> VN1
    RMQ -- "Schedule on VN2" --> VN2

    %% Liqo Offloading
    VN1 -. "Liqo Virtual Kubelet Offload" .-> Tunnel .-> R1_Workers
    VN2 -. "Liqo Virtual Kubelet Offload" .-> Tunnel .-> R2_Workers

    %% Remote worker database writeback
    R1_Workers -. "Reflected Database Write" .-> Tunnel .-> DB
    R2_Workers -. "Reflected Database Write" .-> Tunnel .-> DB
```

---

### 3.2 Diagram Walkthrough: Explained Layer by Layer

The diagram above shows how the **01-Sandbox** application uses Liqo to transparently spread scan workloads across three Kubernetes clusters — a local RKE2 primary and two remote OVH clusters — **without writing a single custom placement policy or changing any application code**.

Below is a detailed breakdown of every component, arrow, and flow in the diagram.

---

#### 🧑‍💻 Top: Who Sends Requests?

```
Users & Webhooks
└── API Clients / GitHub Webhooks
```

All external traffic — human users submitting code scan requests and GitHub CI/CD pipelines firing webhooks on push — originates here. Every request follows the same entry path into the primary RKE2 cluster.

---

#### 🖥️ Primary RKE2 Cluster (`bb-mp-plat-03`) — The Single Control Plane

This is where **all core services live** and where every scan request is received and processed. In Liqo's design, you interact with only this one Kubernetes API server via `kubectl`. The remote clusters are invisible to you as an operator.

##### ① `agentgateway-proxy` (10.0.8.9)

The **public entry point** for all incoming traffic. It is the LoadBalancer-exposed gateway that receives HTTP requests from users and webhooks and forwards them into the cluster. The IP `10.0.8.9` is the MetalLB-assigned LoadBalancer address on the RKE2 node.

```
User/Webhook → agentgateway-proxy (10.0.8.9)
```

##### ② `sandbox-api` (FastAPI)

The **application API layer**. It receives scan requests forwarded from the gateway, validates the request payload (repository URL, scan parameters), stores request state in Redis (not shown in diagram), and then does two things simultaneously:

- Writes job metadata to the **PostgreSQL Database** (left arrow: `API → DB`)
- Publishes the actual scan job task to the **RabbitMQ Service** (right arrow: `API → RabbitMQ`)

```
agentgateway-proxy → sandbox-api
  ├──▶ PostgreSQL Database  (job record / audit log)
  └──▶ RabbitMQ Service     (scan task queued for execution)
```

##### ③ `RabbitMQ Service` — The Job Dispatcher

RabbitMQ holds the queue of pending scan jobs. This is where Liqo's **scheduling intelligence** becomes visible. When a job is dequeued and needs a worker pod, the Kubernetes scheduler decides **which node** to place it on. In a Liqo-enabled cluster, the scheduler sees three types of nodes:

| Scheduling Target | Label | Arrow in Diagram |
|:---|:---|:---|
| `bb-mp-plat-03` (physical node) | `Schedule on Physical` | → Local Scan Pods |
| `liqo-ovh-spoke-1` (virtual node) | `Schedule on VN1` | → Virtual Node: liqo-ovh-spoke-1 |
| `liqo-ovh-spoke-2` (virtual node) | `Schedule on VN2` | → Virtual Node: liqo-ovh-spoke-2 |

The **three arrows out of RabbitMQ** represent these three independent pod scheduling paths. Which path a job takes is controlled by standard Kubernetes `nodeSelector` or `nodeAffinity` rules — no Liqo-specific YAML needed.

---

#### 🔲 Liqo Virtual Node Abstraction — The Core Innovation

```
┌─────────────────────────────────────────┐
│  Liqo Virtual Node Abstraction          │
│  ┌─────────────────┐ ┌────────────────┐ │
│  │ liqo-ovh-spoke-1│ │liqo-ovh-spoke-2│ │
│  └─────────────────┘ └────────────────┘ │
└─────────────────────────────────────────┘
```

This dashed box represents the **most fundamental concept in Liqo**: remote clusters are registered in the primary cluster's Kubernetes API as if they were regular worker nodes.

When you run `kubectl get nodes` on the primary RKE2 cluster, you see something like:

```
NAME                  STATUS   ROLES
bb-mp-plat-03         Ready    control-plane,master   ← real physical node
liqo-ovh-spoke-1      Ready    agent                  ← Liqo virtual node (OVH EU)
liqo-ovh-spoke-2      Ready    agent                  ← Liqo virtual node (OVH US)
```

`liqo-ovh-spoke-1` and `liqo-ovh-spoke-2` are **not real machines** from the primary cluster's perspective — they are **Virtual Kubelet nodes** backed by the Liqo controller. When the Kubernetes scheduler places a pod on one of these virtual nodes, the Liqo Virtual Kubelet intercepts the pod creation request and transparently forwards it to the **actual remote OVH cluster's API server** for real execution.

> **Key insight:** The Kubernetes scheduler, `kubectl`, Helm charts, HorizontalPodAutoscalers — they all work on these virtual nodes exactly as they do on real nodes. No special API calls needed.

---

#### ✈️ Liqo Virtual Kubelet Offload — The Transport Layer

```
Virtual Node: liqo-ovh-spoke-1
  ──(dashed)──▶ "Liqo Virtual Kubelet Offload"
  ──▶ Liqo Cross-Cluster Fabric
  ──▶ Kata/Firecracker Scan Pods on Remote OVH Cluster 1
```

The two dashed arrows labelled **"Liqo Virtual Kubelet Offload"** represent the actual mechanics of pod offloading:

1. **Kubernetes scheduler** assigns a `scan-worker` pod to `liqo-ovh-spoke-1`
2. **Liqo Virtual Kubelet** receives the pod `CREATE` event (it implements the Kubelet API)
3. Virtual Kubelet **translates** the pod spec and calls the **remote OVH Cluster 1's API server** to create the pod there
4. The pod runs on **real physical OVH hardware** — remote node's CPU, RAM, and kernel
5. From the primary cluster's perspective, the pod shows as `Running` on `liqo-ovh-spoke-1`

This is a one-way relationship: the primary cluster **dispatches** pods into remote clusters. The remote cluster does not know about the primary's application topology — it just runs the pods it receives.

---

#### ⚡ Liqo Cross-Cluster Fabric — WireGuard Tunnel & NAT Engine

```
WireGuard Tunnel & NAT Engine
- Encrypted Pod-to-Pod Communication
- Auto IP Collision Translation
```

This is the **network backbone** that connects the primary cluster to both remote OVH clusters. Unlike Cilium ClusterMesh which requires non-overlapping Pod CIDRs, Liqo's fabric handles overlapping subnets automatically.

| Property | Technical Detail |
|:---|:---|
| **WireGuard Tunnel** | All cross-cluster pod traffic is encrypted using WireGuard (UDP port `51820`). Packets between the primary cluster and remote OVH nodes are indistinguishable from a secure VPN tunnel |
| **Encrypted Pod-to-Pod Communication** | When an offloaded `Kata/Firecracker` pod on OVH needs to write to `postgresql-service:5432` on the primary cluster, the traffic travels through the WireGuard tunnel encrypted end-to-end |
| **Auto IP Collision Translation** | Both your primary cluster and OVH clusters might use the same Pod CIDR (e.g., `10.42.0.0/16`). Liqo's IPAM subsystem performs **automatic NAT translation** — it remaps conflicting pod IPs on the fly so packets are routed correctly without any manual subnet re-planning |

The Liqo fabric acts as a **transparent bridge** — offloaded pods and local pods communicate using standard Kubernetes DNS service names, and Liqo handles all the tunneling and address translation invisibly.

---

#### 🏭 Remote OVH Clusters — Where Scan Execution Happens

##### Remote OVH Cluster 1 (Physical Compute)
```
Kata/Firecracker Scan Pods
(Offloaded via VN1)
```

This cluster receives pods offloaded through `liqo-ovh-spoke-1`. The **Kata/Firecracker Scan Pods** run the actual untrusted code analysis inside isolated microVMs on OVH's physical hardware. The CPU-intensive workload — static analysis, AST parsing, container execution — runs **entirely on OVH's machines**, offloading the primary RKE2 node.

##### Remote OVH Cluster 2 (Physical Compute)
```
Kata/Firecracker Scan Pods
(Offloaded via VN2)
```

Identical to Cluster 1, but this is a **second independent remote cluster**. Jobs scheduled to `liqo-ovh-spoke-2` end up executing here. Having two remote clusters allows the system to distribute scan load across two separate geographic regions or availability zones.

---

#### 🔁 Reflected Database Write — Results Return to Primary

```
Kata/Firecracker Scan Pods (OVH Cluster 1)
  ──(dashed)──▶ "Reflected Database Write"
  ──▶ Liqo Cross-Cluster Fabric (WireGuard)
  ──▶ PostgreSQL Database (Primary RKE2 Cluster)
```

The two dashed arrows labelled **"Reflected Database Write"** are the **return path** for scan results:

1. An offloaded `Kata/Firecracker` pod on OVH finishes a code scan
2. It calls `postgresql-service:5432` using the standard Kubernetes DNS name
3. The **Liqo Resource Reflector** has already mirrored the `postgresql-service` Service object into the remote cluster's namespace — so the DNS name resolves correctly on the remote cluster too
4. Liqo's WireGuard fabric intercepts the outbound TCP connection and **tunnels it back to the primary cluster's PostgreSQL pod**
5. The scan findings are written to the **single authoritative PostgreSQL database** on the primary RKE2 node

> **Why "Reflected"?** Liqo calls this process *Resource Reflection* — it copies Kubernetes `Service`, `ConfigMap`, and `Secret` objects from the primary cluster into the remote cluster's shadow namespace, so offloaded pods can discover and connect to primary-cluster services using standard Kubernetes DNS. The traffic itself is routed back through the WireGuard tunnel.

---

#### 📌 Complete End-to-End Request Journey

```
User submits scan request
  ─[1]─▶ agentgateway-proxy (10.0.8.9) on primary RKE2
  ─[2]─▶ sandbox-api (FastAPI) processes request
  ─[3]─▶ PostgreSQL: job record written (audit log)
  ─[4]─▶ RabbitMQ: scan task enqueued
  ─[5]─▶ Kubernetes scheduler selects target node:
          ├── Physical node (bb-mp-plat-03) → Local Scan Pod runs locally
          ├── liqo-ovh-spoke-1 → Liqo Virtual Kubelet offloads to OVH EU
          └── liqo-ovh-spoke-2 → Liqo Virtual Kubelet offloads to OVH US
  ─[6]─▶ Liqo Virtual Kubelet forwards pod CREATE to remote OVH API server
  ─[7]─▶ WireGuard tunnel carries pod traffic between primary ↔ remote
  ─[8]─▶ Kata/Firecracker pod executes scan in isolated microVM on OVH hardware
  ─[9]─▶ Scan result written to postgresql-service:5432
          └── Liqo Resource Reflector resolves DNS → WireGuard tunnel → Primary PostgreSQL
  ─[10]─▶ HTTP 200 OK returned to user
```

Total cross-cluster hops: **2** (pod dispatch outbound + database write inbound). All scan computation happens on OVH hardware.

---

#### 🔑 How This Differs From Cilium ClusterMesh

| Aspect | Liqo (this doc) | Cilium ClusterMesh (`cilium-mesh.md`) |
|:---|:---|:---|
| **How remote clusters appear** | As virtual nodes in primary cluster's `kubectl get nodes` | As independent peers — no virtual node abstraction |
| **Who controls scheduling** | Primary cluster's native Kubernetes scheduler | Each cluster schedules its own pods independently |
| **Remote cluster runs** | Only offloaded scan pods (nothing else needed) | Full stack: API + RabbitMQ + workers per cluster |
| **Pod CIDR requirement** | Overlapping CIDRs allowed — Liqo NAT handles it | Must be non-overlapping (Cilium requires unique CIDRs) |
| **Service discovery (remote pods)** | Liqo Resource Reflector mirrors Services into remote namespace | Cilium ClusterMesh syncs endpoint IPs via etcd |
| **How many API servers** | 1 — you only manage the primary cluster | 3 — each cluster has its own API server |
| **Operator cognitive load** | Lower — single `kubectl` context | Higher — must manage contexts for all 3 clusters |
| **Remote cluster autonomy** | Low — depends on primary for pod scheduling | High — each cluster independently serves its region |

---

## 4. Prerequisites & Cluster Requirements

### 4.1 Cluster Requirements

1. **Primary Cluster:** Kubernetes v1.24+ (RKE2, K3s, or standard K8s).
2. **Remote Cluster(s):** Kubernetes v1.24+ (OVH Managed Kubernetes, RKE2, or VPS K8s).
3. **CLI Installation:** `liqoctl` binary installed on your workstation.

### 4.2 Firewall & Network Port Requirements

Ensure the following port is open on the remote cluster nodes:

| Port / Protocol | Direction | Description |
| :--- | :--- | :--- |
| **`51820/UDP`** | Inbound / Outbound | Liqo WireGuard VPN Tunnel Endpoint |
| **`6443/TCP`** | Outbound | Access to target Cluster API Servers |

---

## 5. Step-by-Step Installation & Configuration Guide

### Step 1: Install `liqoctl` CLI

```bash
curl --fail -sS https://get.liqo.io | bash
sudo mv liqoctl /usr/local/bin/
liqoctl version
```

### Step 2: Install Liqo on Primary Cluster (`rke2-local`)

Install Liqo control plane components onto your primary RKE2 cluster:

```bash
liqoctl install k3s \
  --context default \
  --cluster-name rke2-local
```

### Step 3: Install Liqo on Remote Cluster (`ovh-spoke-1`)

Install Liqo onto the remote OVH cluster:

```bash
liqoctl install k3s \
  --context ovh-spoke-1 \
  --cluster-name ovh-spoke-1
```

### Step 4: Peer the Clusters

Generate authentication token from remote cluster and run peer command on primary cluster:

```bash
# 1. Generate peer command on Remote Cluster
PEER_CMD=$(liqoctl generate peer-command --context ovh-spoke-1)

# 2. Execute peer command on Primary Cluster
eval $PEER_CMD --context default
```

### Step 5: Verify Virtual Node Creation

Check that the remote cluster is now listed as a **Virtual Node** in your primary cluster:

```bash
kubectl --context default get nodes
```

**Output:**
```text
NAME                 STATUS   ROLES                   AGE   VERSION
bb-mp-plat-03        Ready    control-plane,master   10d   v1.28.4+rke2r1
liqo-ovh-spoke-1     Ready    agent                   2m    v1.28.4 (virtual-kubelet)
```

---

## 6. 01-Sandbox Namespace Offloading & Placement

To make standard deployments run across both physical and virtual nodes, enable namespace offloading.

### Step 1: Offload the `opensandbox-system` Namespace

```bash
liqoctl offload namespace opensandbox-system \
  --context default \
  --pod-offloading-strategy LocalAndRemote \
  --naming-strategy Same
```

### 6.1 Offloading Strategies Explained

| Strategy Flag | Behavior for `01-Sandbox` |
| :--- | :--- |
| **`LocalAndRemote`** | Schedulers spread pods across local physical nodes AND remote virtual nodes. |
| **`RemoteOnly`** | All pods in namespace are forced onto remote virtual nodes (saves local CPU/RAM). |
| **`LocalOnly`** | Pods run exclusively on physical local nodes (used for PostgreSQL database). |

### 6.2 Application Manifest Examples with Node Placement

#### Primary Services Manifest (`control-plane-apps.yaml`)

Forces control components (PostgreSQL, RabbitMQ, FastAPI) to stay on physical local nodes:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sandbox-api
  namespace: opensandbox-system
spec:
  replicas: 2
  template:
    metadata:
      labels:
        app: sandbox-api
    spec:
      nodeSelector:
        # Ensures API server stays on physical local machine
        node-role.kubernetes.io/master: "true"
      containers:
        - name: sandbox-api
          image: 01security/sandbox-api:latest
          env:
            - name: RABBITMQ_HOST
              value: "rabbitmq-service.opensandbox-system.svc.cluster.local"
```

#### Worker Pod Placement (`kata-worker-deployment.yaml`)

Allows Kata scan worker pods to be scheduled onto remote Virtual Nodes dynamically:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: sandbox-scan-worker
  namespace: opensandbox-system
spec:
  replicas: 10
  template:
    metadata:
      labels:
        app: scan-worker
    spec:
      affinity:
        nodeAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              preference:
                matchExpressions:
                  - key: type
                    operator: In
                    values:
                      - virtual-node
      tolerations:
        - key: "virtual-node.liqo.io/building"
          operator: "Exists"
          effect: "NoSchedule"
      containers:
        - name: scanner
          image: 01security/repo-scanner:latest
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
```

---

## 7. Traffic Execution & Sequence Flow

This diagram illustrates how a code scan job executes on a Liqo Virtual Node **without code changes**:

```mermaid
sequenceDiagram
    autonumber
    actor User as User / Webhook
    participant API as sandbox-api (Local Node)
    participant RMQ as RabbitMQ (Local Node)
    participant K8s as K8s Scheduler (Primary)
    participant VK as Liqo Virtual Kubelet
    participant Remote as Remote OVH Pod (Kata Scan)
    participant DB as PostgreSQL (Local Node)

    User->>API: POST /api/v1/scan
    API->>RMQ: Enqueue scan job
    RMQ->>K8s: Worker pod requested (High load)

    K8s->>VK: Schedule scan pod onto "liqo-ovh-spoke-1"
    VK->>Remote: Offload pod creation to Remote OVH Cluster via API
    Remote->>Remote: Provision Kata/Firecracker sandbox & run scan

    Remote->>VK: Query "postgresql-service:5432"
    VK->>DB: Transparently reflect query over WireGuard NAT tunnel
    DB-->>Remote: Return query status

    Remote-->>VK: Pod execution finished (Completed)
    VK-->>K8s: Report pod status "Succeeded"
    API-->>User: HTTP 200 OK (Scan Completed)
```

---

## 8. Resource & Network Reflection Mechanics

When a pod runs on a Liqo Virtual Node, Liqo transparently handles resource reflection:

```
PRIMARY CLUSTER                                      REMOTE CLUSTER
┌────────────────────────┐                          ┌────────────────────────┐
│ Secret: db-credentials │───── Reflected Secret───►│ Secret: db-credentials │
│ ConfigMap: app-config  │───── Reflected Config───►│ ConfigMap: app-config  │
│ Service: postgresql    │───── Reflected Endpoint─►│ Endpoint: 10.250.0.15   │
└────────────────────────┘                          └────────────────────────┘
```

1. **Services & Endpoints:** A synthetic service endpoint is registered inside the remote cluster pointing to the Liqo WireGuard IP of the primary cluster's PostgreSQL database.
2. **Secrets & ConfigMaps:** Automatically synchronized so offloaded worker pods can access API keys and environment configs without manual copying.
3. **Storage Volumes:** PersistentVolumeClaims can be reflected using Liqo's storage fabric (`liqo-storage-class`).

---

## 9. Verification & Troubleshooting Handbook

### 9.1 Diagnostic Commands Quick Reference

```bash
# 1. Check overall Liqo peering status
liqoctl status --context default

# 2. Check namespace offloading health
liqoctl status namespace opensandbox-system --context default

# 3. View virtual node capacity and metrics
kubectl describe node liqo-ovh-spoke-1

# 4. Check active WireGuard tunnels
kubectl -n liqo get pods -l app.kubernetes.io/component=network-gateway
```

### 9.2 Common Troubleshooting Scenarios & Fixes

#### Issue 1: Peering fails (`Connection Refused on port 51820`)
* **Root Cause:** Firewall blocking UDP port `51820`.
* **Fix:** Open UDP 51820 in OVH cloud firewall:
  ```bash
  sudo ufw allow 51820/udp
  ```

#### Issue 2: Offloaded Pods stuck in `ContainerCreating`
* **Root Cause:** Image pull failure or secret reflection delay on remote cluster.
* **Fix:** Inspect remote pod status directly using `liqoctl`:
  ```bash
  kubectl get pods -n opensandbox-system -o wide
  # View events for offloaded pod
  kubectl describe pod <pod-name> -n opensandbox-system
  ```

#### Issue 3: Virtual Node status shows `NotReady`
* **Root Cause:** Virtual Kubelet controller lost connection to remote API server.
* **Fix:** Restart Liqo controller manager pod:
  ```bash
  kubectl -n liqo rollout restart deployment/liqo-controller-manager
  ```
