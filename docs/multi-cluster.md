# Zero-Touch Auto-Scaling on OVHcloud for High-Concurrency Code Scanning

> **Core Principle:** When 100s or 1,000s of users hit the scan API simultaneously,
> the entire infrastructure — pods, nodes, and clusters — scales up **automatically**
> with **zero manual configuration, zero SSH, and zero server setup** by any operator.
> When load drops, it scales back down and releases resources (and billing) automatically.
>
> **Platform:** OVHcloud Virtual Servers / Public Cloud (OpenStack-based)

---

## First: Understanding What You Have on OVH

OVH offers two different product lines that have very different auto-scaling capabilities:

| OVH Product | What It Is | Auto-Scaling? |
|:---|:---|:---|
| **OVH VPS** | Simple fixed virtual servers (like your current setup) | ❌ No native auto-scaling API |
| **OVH Public Cloud (Instances)** | OpenStack-based cloud VMs with a full API | ✅ Yes — via OpenStack API |
| **OVH Managed Kubernetes (MKS)** | Fully managed Kubernetes with built-in node auto-scaler | ✅ Yes — native, zero-touch |
| **OVH Managed Rancher Service (MRS)** | Managed Rancher that deploys RKE2 on OVH Public Cloud | ✅ Yes — RKE2 + OVH autoscaling |

> [!IMPORTANT]
> If you are currently on **OVH VPS** (basic virtual server), you **cannot** do zero-touch
> auto-scaling on that tier. VPS has no API for dynamic instance creation.
> You need to move to **OVH Public Cloud** or **OVH Managed Kubernetes** to enable the
> auto-scaling architectures described in this document.

---

## The Problem: Why One Fixed Server Breaks Under Load

Your current architecture runs a single RKE2 cluster on a single OVH VPS.
Every scan request creates a **sandbox pod** that uses `0.5 CPU` and `0.5 GiB RAM`.

| Concurrent Users | Pods Needed | CPU Required | RAM Required | Single Server Reality |
|:---|:---|:---|:---|:---|
| 10 | 10 pods | 5 CPU | 5 GiB | ✅ OK |
| 30 | 30 pods | 15 CPU | 15 GiB | ⚠️ Near limit |
| 50 | 50 pods | 25 CPU | 25 GiB | ❌ `FailedScheduling` |
| 1,000 | 1,000 pods | 500 CPU | 500 GiB | 💥 Server crash |

The hardware ceiling of one server cannot be solved by Kubernetes tuning alone.
The solution is to make compute **elastic**: new VMs are created automatically when needed
and destroyed when idle — entirely driven by software, with no human involvement.

---

## The Zero-Touch Automation Chain on OVH

On OVH, the scaling chain works as follows:

```
User submits scan request via API
         │
         ▼
RabbitMQ queue depth increases
         │   KEDA watches queue every 10 seconds
         ▼
KEDA automatically adds more sandbox-api worker pods
         │
         ▼
Kubernetes finds no node has enough CPU/RAM → Pods go "Pending"
         │   OVH Cluster Autoscaler watches for Pending pods
         ▼
Cluster Autoscaler calls the OVH Public Cloud API (OpenStack Nova)
→ A new OVH cloud instance is created automatically
         │
         ▼
New VM boots (~60–90 seconds), RKE2 agent auto-installs via cloud-init,
node joins the cluster, Kubernetes marks it "Ready"
         │
         ▼
Pending pods are scheduled → Scans execute
         │
         ▼
When queue drains, pods are removed by KEDA
When nodes sit idle for 10 minutes, Cluster Autoscaler
calls OVH API to delete the instance → You stop paying for it
```

**At no point does a human touch a terminal, write a config, or set up a server.**

---

## Solution 1: OVH Managed Kubernetes + KEDA (Recommended — Easiest)

**Best for:** Up to ~500 concurrent users.
**Why this is easiest:** OVH manages the Kubernetes control plane.
You never manage etcd, kube-apiserver, or master nodes.
Node auto-scaling is a checkbox in the OVH dashboard or API call.

### How OVH Managed Kubernetes (MKS) Works

OVHcloud's Managed Kubernetes Service runs on their OpenStack Public Cloud.
When you create a **node pool** with auto-scaling enabled:

- OVH's built-in Cluster Autoscaler watches for Pending pods.
- When a pod cannot be scheduled, OVH automatically creates a new Public Cloud instance
  and adds it to the pool.
- When that instance is idle for the configured time, OVH automatically deletes it.
- **You pay per hour, per instance.** Zero cost when idle.

### Architecture Diagram

```mermaid
flowchart TD
    subgraph Users["Users & CI/CD Pipelines"]
        U1["User 1"]
        U2["User 2"]
        Un["User N (Concurrent)"]
    end

    subgraph OVHManagedK8s["OVH Managed Kubernetes Cluster"]
        subgraph ControlPlane["Control Plane (OVH-managed, invisible to you)"]
            APIServer["kube-apiserver"]
            ETCD[("etcd")]
            OVH_CA["OVH Cluster Autoscaler<br/>(built-in, watches Pending pods)"]
        end

        subgraph MgmtPool["Static Node Pool — 2 fixed nodes (Management Tier)"]
            GW["API Gateway + Auth"]
            MQ[("RabbitMQ")]
            DB[("PostgreSQL + Redis")]
            KEDA["KEDA Controller<br/>(watches RabbitMQ queue depth)"]
        end

        subgraph WorkerPool["Auto-Scaling Node Pool — 0 to N nodes (Worker Tier)"]
            W1["OVH Instance 1<br/>Sandbox Pods..."]
            W2["OVH Instance 2<br/>Sandbox Pods..."]
            WN["OVH Instance N<br/>(auto-provisioned)"]
        end
    end

    subgraph OVHCloud["OVH Public Cloud (OpenStack API)"]
        InstanceAPI["Nova Instance API<br/>(OVH creates/deletes VMs here)"]
    end

    U1 --> GW
    U2 --> GW
    Un --> GW
    GW --> MQ
    KEDA -- "1. Queue rises → scale pods up" --> APIServer
    APIServer -- "2. Pods Pending (no node capacity)" --> OVH_CA
    OVH_CA -- "3. Auto-create OVH instance" --> InstanceAPI
    InstanceAPI -- "4. New node joins cluster<br/>RKE2 agent installed via cloud-init" --> WorkerPool
    MQ -- "Dispatches scan jobs" --> W1
    MQ -- "Dispatches scan jobs" --> W2
    MQ -- "Dispatches scan jobs" --> WN
    W1 -- "Scan results" --> DB
    W2 -- "Scan results" --> DB
    WN -- "Scan results" --> DB
```

### One-Time Configuration (Set Once, Never Touch Again)

**Step 1 — Create the OVH Managed Kubernetes cluster (OVH dashboard or API):**
```bash
# Via OVH API (can be automated with Terraform)
# Node pool with auto-scaling: min 0 nodes, max 50 nodes, hourly billing
POST /cloud/project/{projectId}/kube/{kubeId}/nodepool
{
  "name": "sandbox-worker-pool",
  "flavorName": "b2-15",        # 4 vCPU, 15 GiB RAM per node
  "minNodes": 0,                 # scale to zero when idle
  "maxNodes": 50,                # maximum 50 nodes (200 vCPUs)
  "autoscale": true,             # OVH auto-scaler enabled
  "monthlyBilled": false         # hourly billing — pay only when running
}
```

**Step 2 — Deploy KEDA (one `helm install`, never run again):**
```bash
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda --namespace keda --create-namespace
```

**Step 3 — Create the KEDA ScaledObject (deploy once, governs all future scaling):**
```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: sandbox-worker-scaler
spec:
  scaleTargetRef:
    name: sandbox-api
  minReplicaCount: 2       # always 2 warm workers ready
  maxReplicaCount: 200     # can run 200 concurrent worker pods
  triggers:
    - type: rabbitmq
      metadata:
        queueName: repo_scan_queue
        queueLength: "5"   # add 1 pod per 5 pending scan jobs
    - type: rabbitmq
      metadata:
        queueName: quick_scan_queue
        queueLength: "3"
```

**After these three steps, the system is fully autonomous forever:**
- 10 users scan → KEDA adds ~2 pods. No new nodes needed. Cost: baseline.
- 100 users scan → KEDA adds 20 pods. OVH auto-scales 5 new nodes. Cost: ~5 × hourly rate.
- 1,000 users scan → KEDA adds 200 pods. OVH auto-scales 50 nodes. Cost: ~50 × hourly rate.
- Traffic stops → Pods drop to 2. Nodes drain. After 10 min idle, OVH deletes nodes. Cost: baseline.

---

## Solution 2: OVH Public Cloud + Self-Managed RKE2 + Cluster Autoscaler

**Best for:** Teams that need to keep RKE2 and self-manage the cluster but want zero-touch scaling.
**Mechanism:** Kubernetes Cluster Autoscaler with OpenStack cloud provider, talking to OVH's OpenStack API.

### How It Works

Since OVH Public Cloud is built on OpenStack, the standard Kubernetes **Cluster Autoscaler**
has a native **OpenStack cloud provider** that can call OVH's Nova API to:
1. Add instances to an existing **Server Group** (OVH's equivalent of ASG).
2. Join them to the RKE2 cluster automatically via `cloud-init` bootstrap scripts.
3. Remove them when idle.

New nodes join the cluster automatically because their `cloud-init` script installs the
RKE2 agent and connects to the existing control plane. No human SSH is required.

### Architecture Diagram

```mermaid
flowchart TD
    subgraph Users["Users & Webhooks"]
        APIHits["Concurrent Scan Requests"]
    end

    subgraph RKE2Cluster["Self-Managed RKE2 Cluster on OVH Public Cloud"]
        subgraph Masters["3 Fixed OVH Instances — HA Control Plane"]
            RKE2Master["RKE2 Server (master)"]
            ETCD[("etcd HA")]
            CA["Cluster Autoscaler<br/>OpenStack Provider<br/>(watches Pending pods)"]
            KEDA["KEDA Controller<br/>(watches RabbitMQ)"]
        end

        subgraph MgmtNodes["2 Fixed OVH Instances — Management Services"]
            GW["API Gateway"]
            MQ[("RabbitMQ")]
            DB[("PostgreSQL + Redis")]
        end

        subgraph WorkerNodes["OVH Auto-Scaled Instances (0 to N)"]
            W1["Worker 1<br/>cloud-init: installs rke2-agent<br/>auto-joins cluster"]
            W2["Worker 2<br/>cloud-init: installs rke2-agent<br/>auto-joins cluster"]
            WN["Worker N<br/>(auto-provisioned by CA)"]
        end
    end

    subgraph OVHOpenStack["OVH Public Cloud — OpenStack API"]
        Nova["Nova (Compute API)<br/>Creates/Deletes OVH instances"]
        ServerGroup["Instance Group<br/>(auto-scaling pool template)"]
    end

    APIHits --> GW
    GW --> MQ
    KEDA -- "Queue depth rises → scale pods" --> RKE2Master
    RKE2Master -- "Pods Pending" --> CA
    CA -- "Calls OpenStack Nova API" --> Nova
    Nova -- "Launches new instance from template" --> ServerGroup
    ServerGroup -- "cloud-init runs rke2-agent install<br/>node auto-joins cluster" --> WorkerNodes
    MQ --> W1
    MQ --> W2
    MQ --> WN
    W1 -- "Results" --> DB
    W2 -- "Results" --> DB
    WN -- "Results" --> DB
```

### cloud-init Bootstrap (What Makes Nodes Join Automatically)

Every new OVH instance launched by the autoscaler runs this `cloud-init` script at boot.
This is defined **once** in the instance template. No operator involvement per node:

```yaml
# /etc/rancher/rke2/config.yaml  (baked into cloud-init)
server: https://<HA-CONTROL-PLANE-VIP>:9345
token: <RKE2_CLUSTER_TOKEN>          # pre-shared secret, set once
node-label:
  - "role=sandbox-worker"
```

```bash
# cloud-init user-data script (baked into OVH instance template)
#!/bin/bash
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="agent" sh -
systemctl enable rke2-agent --now
# Node joins the cluster automatically. Done.
```

---

## Solution 3: OVH Managed Rancher Service (MRS) + Multi-Cluster (Enterprise Scale)

**Best for:** 5,000+ concurrent users. Multiple RKE2 clusters auto-provisioned on demand.
**Why:** OVH offers a **Managed Rancher Service** that combines Rancher's fleet management
with OVH Public Cloud infrastructure. You get Rancher's full UI + API to manage multiple
RKE2 clusters, and the clusters themselves auto-scale via Cluster API Provider OpenStack (CAPO).

### How Multi-Cluster Zero-Touch Works on OVH

When a single RKE2 cluster reaches capacity on OVH:

1. **Cluster API Provider OpenStack (CAPO)** + **CAPRKE2** are deployed in a
   central Management Cluster.
2. A Smart Dispatcher (monitoring RabbitMQ queue depth and per-cluster utilization)
   submits a `Cluster` Kubernetes manifest to CAPO.
3. CAPO calls the OVH OpenStack API → provisions new VMs → bootstraps a full RKE2 cluster.
4. **Rancher Fleet** (GitOps) detects the new cluster → automatically pushes all Helm charts
   (KEDA, sandbox-api, RabbitMQ consumer config).
5. The new cluster is live within 5–10 minutes, fully configured, receiving scan jobs.
6. When traffic drops, the Dispatcher deletes the `Cluster` manifest → CAPO tears down all VMs.

### Architecture Diagram

```mermaid
flowchart TD
    subgraph Users["Global Users & Webhooks"]
        Traffic["1,000s of Concurrent Scan Requests"]
    end

    subgraph MgmtCluster["OVH Managed Rancher (Management Cluster — Always On)"]
        direction TB
        APIGW["API Gateway + Auth + Rate Limiting"]
        CentralMQ[("Central RabbitMQ")]
        CentralDB[("PostgreSQL + Redis")]
        Dispatcher["Smart Dispatcher<br/>monitors per-cluster load"]
        CAPO["Cluster API OpenStack (CAPO)<br/>+ CAPRKE2<br/>auto-provisions RKE2 clusters on OVH"]
        Fleet["Rancher Fleet<br/>GitOps — auto-deploys config to new clusters"]
    end

    subgraph OVHInfra["OVH Public Cloud OpenStack API"]
        Nova["Nova Compute API<br/>(creates/deletes OVH instances)"]
    end

    subgraph Spoke1["RKE2 Spoke Cluster 1 (Always Active)"]
        S1_KEDA["KEDA"]
        S1_CA["OVH Cluster Autoscaler"]
        S1_Pods["Sandbox Pods (0–200)"]
        S1_Nodes["OVH Worker Nodes (auto-scaled)"]
    end

    subgraph Spoke2["RKE2 Spoke Cluster 2 (Auto-Provisioned by CAPO)"]
        S2_KEDA["KEDA"]
        S2_CA["OVH Cluster Autoscaler"]
        S2_Pods["Sandbox Pods (0–200)"]
        S2_Nodes["OVH Worker Nodes (auto-scaled)"]
    end

    subgraph SpokeN["RKE2 Spoke Cluster N (On-Demand)"]
        SN_KEDA["KEDA"]
        SN_CA["OVH Cluster Autoscaler"]
        SN_Pods["Sandbox Pods (0–200)"]
        SN_Nodes["OVH Worker Nodes (auto-scaled)"]
    end

    Traffic --> APIGW
    APIGW --> CentralMQ
    CentralMQ --> Dispatcher

    Dispatcher -- "Routes to Cluster 1 while < 80% load" --> S1_KEDA
    Dispatcher -- "Routes to Cluster 2 when Cluster 1 overloaded" --> S2_KEDA
    Dispatcher -- "Submits Cluster manifest to CAPO<br/>when all clusters at capacity" --> CAPO
    CAPO -- "Calls OVH OpenStack API<br/>Provisions new RKE2 cluster VMs" --> Nova
    Nova -- "New cluster comes online" --> SpokeN
    Fleet -- "Auto-pushes KEDA, worker config<br/>to newly provisioned clusters" --> Spoke2
    Fleet -- "Auto-pushes config" --> SpokeN

    S1_KEDA -- "Scales pods on queue depth" --> S1_Pods
    S1_CA -- "Scales OVH nodes on Pending pods" --> S1_Nodes
    S2_KEDA -- "Scales pods" --> S2_Pods
    S2_CA -- "Scales OVH nodes" --> S2_Nodes

    S1_Pods -- "Results" --> CentralDB
    S2_Pods -- "Results" --> CentralDB
    SN_Pods -- "Results" --> CentralDB
```

---

## OVH-Specific Tooling Summary

| Layer | AWS Equivalent | OVH Equivalent | Auto? |
|:---|:---|:---|:---|
| Managed Kubernetes | EKS | OVH Managed Kubernetes (MKS) | ✅ Built-in autoscaler |
| Managed Multi-Cluster | EKS + Rancher | OVH Managed Rancher Service (MRS) | ✅ Rancher + CAPO |
| VM provisioning API | EC2 + ASG | OVH Public Cloud Nova (OpenStack) | ✅ Via Cluster Autoscaler |
| Node auto-provisioner | Karpenter | Cluster Autoscaler (OpenStack provider) | ✅ Watches Pending pods |
| Cluster auto-provisioner | CAPI + EKS | CAPO + CAPRKE2 | ✅ OVH OpenStack API |
| Pod scaling | KEDA | KEDA (cloud-agnostic) | ✅ Watches RabbitMQ |
| GitOps config | ArgoCD / Flux | Rancher Fleet / ArgoCD | ✅ Auto-pushes to all clusters |
| Cloud bootstrap | EC2 user-data | OVH cloud-init | ✅ Nodes self-register |

---

## Comparison of Solutions on OVH

| Dimension | Solution 1: OVH MKS + KEDA | Solution 2: RKE2 + CA | Solution 3: MRS + CAPO |
|:---|:---|:---|:---|
| **Concurrent Users** | Up to ~500 | Up to ~2,000 | Up to ~20,000+ |
| **Zero-touch node scaling** | ✅ OVH built-in | ✅ Cluster Autoscaler | ✅ Per-cluster CA |
| **Zero-touch cluster scaling** | ❌ Single cluster | ❌ Single cluster | ✅ CAPO creates clusters |
| **Zero-touch config delivery** | ✅ ArgoCD / Fleet | ✅ ArgoCD / Fleet | ✅ Rancher Fleet |
| **Keeps RKE2** | ❌ OVH manages K8s | ✅ Self-managed RKE2 | ✅ RKE2 via MRS |
| **Setup complexity** | Low (1 week) | Medium (2–3 weeks) | Medium-High (3–5 weeks) |
| **OVH cost (idle)** | ~€50–80/mo (MKS fee) | ~€80–120/mo (3 masters) | ~€150–250/mo |
| **OVH cost (1,000 users)** | ~€800–2,000/mo | ~€1,000–2,500/mo | ~€2,000–5,000/mo |

---

## Recommended Path for OVH

```mermaid
flowchart LR
    A["Today<br/>Single OVH VPS<br/>~10 users max<br/>(current state)"] -->|"Migrate to"| B
    B["Phase 1<br/>OVH Managed Kubernetes<br/>+ KEDA<br/>Handles up to 500 users<br/>Zero-touch node scaling"] -->|"When 500+ users"| C
    C["Phase 2<br/>OVH Managed Rancher Service<br/>+ CAPO + CAPRKE2<br/>Handles 5,000+ users<br/>Zero-touch cluster scaling"]
```

### Phase 1 Migration Steps (One-Time, Then Fully Automated)

1. **Create an OVH Public Cloud project** (if not already on Public Cloud).
2. **Create an OVH Managed Kubernetes cluster** via the OVH Control Panel or API.
3. **Create a Worker Node Pool** with:
   - `autoscale: true`
   - `minNodes: 0`, `maxNodes: 50`
   - `monthlyBilled: false` (hourly billing — critical for auto-scaling cost efficiency)
   - Flavor: `b2-15` or `c2-15` (compute-optimized OVH instances)
4. **Migrate your RabbitMQ, PostgreSQL, Redis, API Gateway** deployments via `helm upgrade`.
5. **Deploy KEDA** and create the `ScaledObject` manifest (see Solution 1 above).
6. **Set up ArgoCD** watching your Git repo — config changes deploy automatically.
7. **Done.** From this point, zero manual scaling actions are ever needed again.

---

## Identifying Your Current OVH Setup

Before designing any scaling solution, you must confirm which OVH product tier your
server is running on. The product tier determines which auto-scaling options are available.

### How to Check

Run the following commands on your server:

**Method 1 — DMI Product Name (most reliable):**
```bash
sudo cat /sys/class/dmi/id/product_name
sudo cat /sys/class/dmi/id/sys_vendor
```

**Method 2 — OpenStack Metadata API:**
```bash
curl -s --connect-timeout 3 http://169.254.169.254/openstack/latest/meta_data.json
```

**Method 3 — Hostname pattern:**
```bash
hostname -f
```

### Interpreting the Results

| DMI `product_name` Output | Metadata API | Hostname Pattern | OVH Product | Auto-Scaling |
|:---|:---|:---|:---|:---|
| `Standard PC (i440FX + PIIX, 1996)` | Timeout / refused | `vpsXXXXXX.vps.ovh.net` | **OVH VPS** | ❌ Not possible |
| `OpenStack Nova` | Returns JSON | Compute hostname | **OVH Public Cloud** | ✅ Full support |
| `HVM domU` | Timeout / refused | `nsXXXXXX.ip-X.eu` | **OVH Dedicated/Bare Metal** | ⚠️ Limited |

### Current Environment: OVH VPS Confirmed

> [!IMPORTANT]
> Running `cat /sys/class/dmi/id/product_name` on the current server returns:
> ```
> Standard PC (i440FX + PIIX, 1996)
> ```
> This is the QEMU/KVM emulated chipset fingerprint of an **OVH VPS**.
> This is confirmed to be an OVH VPS (Virtual Private Server), **not** OVH Public Cloud.

### What OVH VPS Cannot Do vs. OVH Public Cloud

| Capability | OVH VPS (Current) | OVH Public Cloud (Required) |
|:---|:---|:---|
| **API to create new VMs on demand** | ❌ No API | ✅ Full OpenStack Nova API |
| **Cluster Autoscaler (node scaling)** | ❌ Cannot provision nodes | ✅ Watches Pending pods, calls Nova API |
| **KEDA (pod scaling)** | ✅ Works | ✅ Works |
| **Zero-touch node scaling** | ❌ Impossible | ✅ Fully automated |
| **Zero-touch cluster scaling (CAPO)** | ❌ Impossible | ✅ Via CAPO + CAPRKE2 |
| **OVH Managed Kubernetes (MKS)** | ❌ Not available | ✅ Built-in autoscaling node pools |
| **Billing model** | Fixed monthly fee | Hourly — pay only for what runs |
| **Max practical concurrent users** | ~10–30 (hardware ceiling) | Unlimited (elastic compute) |

### Migration Path: OVH VPS → OVH Public Cloud

To unlock zero-touch auto-scaling, the server needs to be migrated from OVH VPS
to **OVH Public Cloud**. These are separate products in the OVH portal.

```mermaid
flowchart TD
    A["Current State<br/>OVH VPS<br/>Fixed single server<br/>Standard PC / QEMU-KVM<br/>~10-30 concurrent users max"] --> B

    B["Step 1: Create OVH Public Cloud Project<br/>ovhcloud.com/manager → Public Cloud<br/>No cost until instances are launched"]

    B --> C["Step 2: Choose compute product"]

    C --> D["Option A<br/>OVH Managed Kubernetes (MKS)<br/>Easiest — OVH manages control plane<br/>Enable autoscale on node pool<br/>Set min=0, max=50, hourly billing"]

    C --> E["Option B<br/>OVH Public Cloud Instances<br/>Self-managed RKE2<br/>+ Cluster Autoscaler (OpenStack provider)<br/>+ cloud-init auto-join for new nodes"]

    D --> F["Step 3: Migrate application stack<br/>RabbitMQ, PostgreSQL, Redis,<br/>API Gateway via helm upgrade<br/>(manifests are identical — just new cluster)"]

    E --> F

    F --> G["Step 4: Deploy KEDA<br/>ScaledObject watches RabbitMQ<br/>Pods scale 2 → 200 automatically"]

    G --> H["Step 5: Set up ArgoCD / Fleet<br/>GitOps — config changes auto-deploy<br/>to all clusters without manual steps"]

    H --> I["Done<br/>Zero-touch auto-scaling active<br/>500–5,000+ concurrent users supported<br/>No operator intervention ever needed"]
```

### OVH Public Cloud Instance Flavors to Use

When creating the auto-scaling worker node pool on OVH Public Cloud,
use compute-optimized flavors billed hourly:

| OVH Flavor | vCPU | RAM | Sandbox Pods per Node | Monthly (if always on) | Hourly |
|:---|:---|:---|:---|:---|:---|
| `b2-7` | 2 vCPU | 7 GiB | ~4 pods | ~€14/mo | ~€0.019/hr |
| `b2-15` | 4 vCPU | 15 GiB | ~8 pods | ~€28/mo | ~€0.038/hr |
| `b2-30` | 8 vCPU | 30 GiB | ~16 pods | ~€55/mo | ~€0.075/hr |
| `c2-15` | 6 vCPU | 15 GiB | ~10 pods | ~€40/mo | ~€0.054/hr |

> [!TIP]
> Use `b2-15` or `b2-30` for the auto-scaling worker pool with `monthlyBilled: false`.
> At zero traffic, 0 nodes run and you pay nothing for compute.
> At 1,000 concurrent users, the autoscaler spins up ~125 `b2-15` nodes automatically
> and tears them down when load drops — you only pay for the hours they were running.

---

## Dual OVH VPS Architecture: Automatic Overflow Between Two Pre-Provisioned RKE2 Clusters

This section answers the specific scenario:

> *"I have VM1 with RKE2 already. I create VM2 with RKE2. Can traffic automatically
> overflow from VM1 to VM2 when VM1 is overloaded, then scale back to VM1 when load drops?"*

### Short Answer

**Yes — this is possible on OVH VPS without any cloud API or managed service.**

However, the mechanism is not HTTP-level routing. It works at the **message queue
(RabbitMQ) level**, which is exactly how your existing architecture already processes
scan jobs. Both clusters connect to the same shared RabbitMQ queue as competing
consumers. Jobs flow to whichever cluster has free worker capacity.

> [!IMPORTANT]
> **What "scale down" means on OVH VPS:**
> Because OVH VPS bills at a fixed monthly rate regardless of usage, "scaling down"
> means the **pods** on VM2 scale to zero (KEDA drains them when the queue is empty).
> VM2 itself keeps running and you continue to pay its fixed monthly cost.
> This is the key difference from OVH Public Cloud, where VMs are deleted and billing stops.

---

### How It Works: The Competing Consumers Pattern

The core mechanism that makes this zero-touch is **RabbitMQ competing consumers**:

```
User submits scan → API Gateway → Central RabbitMQ Queue
                                         │
                    ┌────────────────────┴──────────────────────┐
                    │                                           │
            VM1 sandbox-api workers                   VM2 sandbox-api workers
            (consume jobs freely)                     (also consuming same queue)
                    │                                           │
            When VM1 pods are all busy,             VM2 workers pick up the
            they stop ACKing new messages.          overflow messages automatically.
                    │                                           │
            VM1 sandbox pods                        VM2 sandbox pods
            (up to VM1 hardware limit)              (up to VM2 hardware limit)
```

RabbitMQ **never pushes more messages to a consumer than its prefetch limit allows**.
When all VM1 workers are at capacity (prefetch slots full), messages stay in the queue
and VM2 workers — which are also subscribed — consume them automatically.
**No routing logic. No load balancer rule. No human decision.**

When traffic drops:
- Queue drains → both VM1 and VM2 workers become idle
- **KEDA** scales VM2 worker pod replicas down to `minReplicaCount: 0`
- VM2 RKE2 cluster sits idle with zero workload pods — minimal resource use
- Next traffic spike → KEDA scales VM2 pods back up automatically

---

### Required Components

| # | Component | Runs On | Role | Zero-Touch? |
|:---|:---|:---|:---|:---|
| 1 | **Central RabbitMQ** | VM1 (primary) | Single shared job queue both clusters read from | N/A — fixed |
| 2 | **KEDA** | Both VM1 and VM2 | Scales worker pod count based on RabbitMQ queue depth | ✅ Automatic |
| 3 | **sandbox-api workers** | Both VM1 and VM2 | RabbitMQ consumers that create sandbox pods | ✅ Auto-scaled by KEDA |
| 4 | **WireGuard VPN Tunnel** | Both VM1 and VM2 | Secure private network between the two VPS so VM2 can reach VM1's RabbitMQ | ✅ Set once |
| 5 | **Prometheus + metrics-server** | Both VM1 and VM2 | Collects CPU/memory/queue metrics per cluster | ✅ Passive |
| 6 | **HAProxy or Nginx** | VM1 (optional) | HTTP-level load balancer for API Gateway traffic if you want HTTP routing too | ⚠️ Config set once |
| 7 | **Shared PostgreSQL** | VM1 (primary) | Both clusters write scan results to same DB | N/A — fixed |
| 8 | **ArgoCD / Rancher Fleet** | VM1 or standalone | Keeps sandbox-api manifests in sync on both clusters | ✅ GitOps |

---

### Architecture Diagram

```mermaid
flowchart TD
    subgraph Internet["Users & API Clients"]
        Users["Concurrent Scan Requests<br/>(100s of users)"]
    end

    subgraph VM1["OVH VPS — VM1 (Primary RKE2 Cluster)"]
        direction TB
        APIGW["API Gateway (public endpoint)"]
        RMQ[("Central RabbitMQ<br/>(shared — both clusters subscribe here)")]
        DB[("PostgreSQL + Redis<br/>(shared results DB)")]
        KEDA1["KEDA Controller<br/>(watches queue depth)"]
        Workers1["sandbox-api Workers<br/>(pods scaled 2 to N by KEDA1)"]
        Pods1["Sandbox Scanner Pods<br/>(limited by VM1 hardware)"]
        WG1["WireGuard VPN (10.99.0.1)"]
    end

    subgraph VM2["OVH VPS — VM2 (Overflow RKE2 Cluster)"]
        direction TB
        KEDA2["KEDA Controller<br/>(watches same queue depth)"]
        Workers2["sandbox-api Workers<br/>(pods scaled 0 to N by KEDA2)"]
        Pods2["Sandbox Scanner Pods<br/>(overflow capacity)"]
        WG2["WireGuard VPN (10.99.0.2)"]
    end

    Users --> APIGW
    APIGW --> RMQ

    RMQ -- "VM1 workers consume<br/>(up to prefetch limit)" --> Workers1
    RMQ -- "VM2 workers consume overflow<br/>(when VM1 prefetch slots are full)" --> Workers2

    KEDA1 -- "Monitors queue depth<br/>Scales VM1 workers up/down" --> Workers1
    KEDA2 -- "Monitors same queue depth<br/>Scales VM2 workers 0 to N" --> Workers2

    Workers1 -- "Creates sandbox pods" --> Pods1
    Workers2 -- "Creates sandbox pods" --> Pods2

    Pods1 -- "Scan results" --> DB
    Pods2 -- "Scan results via tunnel" --> DB

    WG1 <-- "Encrypted WireGuard Tunnel<br/>(RabbitMQ + DB traffic)" --> WG2
```

---

### Step-by-Step: How Overflow and Scale-Down Happen Automatically

#### Scenario A — Normal load (only VM1 active)

1. 20 users submit scans → 20 messages in RabbitMQ queue.
2. VM1's KEDA detects queue depth → scales VM1 workers to 4 pods.
3. 4 workers each hold 5 prefetch slots = 20 concurrent jobs. Queue drains.
4. VM2's KEDA also sees the queue — depth is 0 → VM2 workers stay at `minReplicaCount: 0`.
5. **VM2 runs zero workload pods. All scans run on VM1.**

#### Scenario B — High load (VM1 saturated, VM2 activates automatically)

1. 500 users submit scans → 500 messages pile into the queue.
2. VM1's KEDA scales workers to maximum (e.g., 20 pods × 5 prefetch = 100 jobs).
3. VM1 workers are all at prefetch capacity → they stop pulling new messages.
4. **400 messages remain in queue.**
5. VM2's KEDA also sees queue depth = 400 → scales VM2 workers up to 20 pods.
6. VM2 workers start consuming the 400 overflow jobs automatically.
7. **Both VM1 and VM2 run concurrently with no operator action.**

#### Scenario C — Load drops (VM2 scales back to zero automatically)

1. Traffic subsides → queue drains to 0.
2. VM1 KEDA: queue depth = 0 → scales VM1 workers back to `minReplicaCount: 2`.
3. VM2 KEDA: queue depth = 0 → scales VM2 workers back to `minReplicaCount: 0`.
4. **VM2 has zero running workload pods.** VM2 RKE2 cluster is idle.
5. VM1 handles any remaining trickle of traffic alone.

---

### KEDA Configuration for Both Clusters

The KEDA `ScaledObject` on **both** clusters points to the **same** RabbitMQ host on VM1.
The only difference is `minReplicaCount` — VM2 scales to 0 when idle:

```yaml
# VM1 cluster: always keeps 2 warm workers ready
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: sandbox-worker-scaler
  namespace: sandbox
spec:
  scaleTargetRef:
    name: sandbox-api
  minReplicaCount: 2          # VM1 always keeps 2 warm workers ready
  maxReplicaCount: 20         # VM1 hardware ceiling
  triggers:
    - type: rabbitmq
      metadata:
        host: amqp://user:pass@10.99.0.1:5672   # VM1 RabbitMQ via WireGuard IP
        queueName: repo_scan_queue
        queueLength: "5"
```

```yaml
# VM2 cluster: pure overflow — scales to 0 when queue is not overflowing
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: sandbox-worker-scaler
  namespace: sandbox
spec:
  scaleTargetRef:
    name: sandbox-api
  minReplicaCount: 0          # VM2 runs ZERO pods when not needed
  maxReplicaCount: 20         # VM2 overflow capacity ceiling
  cooldownPeriod: 300         # wait 5 min after queue drains before scaling to 0
  triggers:
    - type: rabbitmq
      metadata:
        host: amqp://user:pass@10.99.0.1:5672   # same VM1 RabbitMQ
        queueName: repo_scan_queue
        queueLength: "5"
```

---

### WireGuard Tunnel Setup (One-Time — Lets VM2 Reach VM1's Services)

VM2 needs to reach VM1's RabbitMQ (port `5672`) and PostgreSQL (port `5432`)
over a **private encrypted tunnel**. WireGuard is set up once and runs permanently:

```bash
# On VM1 — /etc/wireguard/wg0.conf
[Interface]
PrivateKey = <VM1_PRIVATE_KEY>
Address = 10.99.0.1/24
ListenPort = 51820

[Peer]
PublicKey = <VM2_PUBLIC_KEY>
AllowedIPs = 10.99.0.2/32
```

```bash
# On VM2 — /etc/wireguard/wg0.conf
[Interface]
PrivateKey = <VM2_PRIVATE_KEY>
Address = 10.99.0.2/24

[Peer]
PublicKey = <VM1_PUBLIC_KEY>
Endpoint = <VM1_PUBLIC_IP>:51820
AllowedIPs = 10.99.0.1/32
PersistentKeepalive = 25
```

After this, VM2 reaches VM1's services at:
- RabbitMQ: `10.99.0.1:5672`
- PostgreSQL: `10.99.0.1:5432`
- Redis: `10.99.0.1:6379`

---

### Honest Limitations of the Dual VPS Approach

| Limitation | Details |
|:---|:---|
| **Both VPS costs are fixed** | Even when VM2 is idle (zero pods), you pay its full monthly OVH VPS bill |
| **VM2 capacity is a hard ceiling** | If VM1 + VM2 are both saturated, there is no 3rd VM to overflow to without manual setup |
| **No true elastic compute** | VMs are not created or destroyed automatically — only pods scale up/down |
| **Manual VM provisioning** | Adding VM3, VM4 etc. always requires manual RKE2 installation and config |
| **Best upgrade path** | Migrate to OVH Public Cloud where VMs ARE auto-created and destroyed |

### What This Achieves vs. What It Cannot Do

| | Dual VPS Approach | OVH Public Cloud |
|:---|:---|:---|
| **Automatic pod scaling** | ✅ KEDA handles it | ✅ KEDA handles it |
| **Automatic overflow to 2nd cluster** | ✅ RabbitMQ competing consumers | ✅ Same |
| **Scale VM2 pods back to zero** | ✅ KEDA `minReplicaCount: 0` | ✅ Same |
| **Automatic VM creation** | ❌ Must be pre-provisioned manually | ✅ Cluster Autoscaler auto-creates |
| **Automatic VM deletion (save money)** | ❌ VPS bill is always fixed | ✅ Hourly billing stops when VM deleted |
| **Add 3rd or 4th cluster on demand** | ❌ Manual RKE2 install required each time | ✅ CAPO auto-provisions new clusters |
| **Setup complexity** | Low — WireGuard + KEDA only | Medium — OpenStack or MKS setup |
