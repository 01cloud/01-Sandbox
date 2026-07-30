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

---

## Deep Dive: Answers to Specific Questions

---

### Q1: Is "Central RabbitMQ" a New Queue or Your Existing One?

**It is your existing RabbitMQ. You do not create a new one.**

Looking at your actual codebase:

- Your RabbitMQ runs as a pod inside the `opensandbox-system` namespace on VM1,
  defined in [`codeInspector/charts/apiServer/templates/rabbitmq.yaml`](../codeInspector/charts/apiServer/templates/rabbitmq.yaml).
- Your `sandbox-api` pods connect to it via the environment variable `RABBITMQ_URL`
  as seen in [`apiServer/fastapi/core/queue/connection.py`](../apiServer/fastapi/core/queue/connection.py):
  ```python
  RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "")
  ```
- Your `values.yaml` sets the host as `rabbitmq-service` on port `5672` in the
  `opensandbox-system` namespace:
  ```yaml
  rabbitmq:
    enabled: true
    host: "rabbitmq-service"
    port: 5672
  ```

**When you add VM2, you simply point VM2's `sandbox-api` workers at
VM1's RabbitMQ using its WireGuard private IP instead of the internal cluster DNS name.**

No second RabbitMQ. No queue duplication. No data sync needed. One queue, two consumers.

```
VM1 RabbitMQ (rabbitmq-service, port 5672)
         │
         ├── VM1 sandbox-api workers  →  RABBITMQ_URL = amqp://admin:pass@rabbitmq-service:5672
         │                                (uses Kubernetes DNS — same cluster)
         │
         └── VM2 sandbox-api workers  →  RABBITMQ_URL = amqp://admin:pass@10.99.0.1:5672
                                         (uses WireGuard tunnel private IP — cross-cluster)
```

The queues (`quick_scan_queue`, `repo_scan_queue`, `delete_scan_queue`,
`email_notification_queue`) already exist in your RabbitMQ. VM2's workers subscribe
to the same queues and compete for messages automatically. No changes to VM1.

---

### Q2: What Is KEDA and How Does It Enable Automatic Overflow?

#### What KEDA Is

**KEDA** (Kubernetes Event-Driven Autoscaling) is a Kubernetes operator that watches
an **external metric** (in your case, RabbitMQ queue depth) and automatically adjusts
the **replica count** of your `sandbox-api` Deployment — without you running any command.

Your current `values.yaml` has an `hpa` section:
```yaml
hpa:
  enabled: true
  minReplicas: 1
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
  targetMemoryUtilizationPercentage: 80
```

This is the default Kubernetes **HPA** (Horizontal Pod Autoscaler) — it scales based on
**CPU/Memory usage**. The problem: CPU rises only *after* pods are already overloaded.
By then, users are already waiting.

**KEDA replaces this** with queue-depth-driven scaling — it scales *before* pods are
overloaded, the moment messages pile up. It is more predictive and faster to respond.

#### How KEDA Works With Your Existing Code

Your [`consumer.py`](../apiServer/fastapi/core/queue/consumer.py) already uses prefetch:
```python
prefetch_env_key = f"PREFETCH_{jt.job_type.upper()}"
# ...
await ch.set_qos(prefetch_count=prefetch)
```

This means each `sandbox-api` pod holds exactly `prefetch` messages at a time.
Your `values.yaml` sets:
```yaml
maxQuickScanWorkers: "1"
maxRepoScanWorkers: "1"
```

So currently each pod holds 1 job at a time. With KEDA, when 20 jobs are in the queue:
- KEDA sees: `queue_depth (20) / queueLength_threshold (5) = 4 pods needed`
- KEDA tells Kubernetes to scale to 4 replicas
- Each pod picks up 1 job via prefetch → 4 jobs run concurrently
- When queue drains → KEDA scales back to `minReplicaCount`

#### KEDA on VM1 vs VM2 — The Overflow Trigger

| | VM1 KEDA | VM2 KEDA |
|:---|:---|:---|
| **Watches** | Same RabbitMQ queue | Same RabbitMQ queue |
| **`minReplicaCount`** | `2` (always 2 warm workers) | `0` (zero workers when idle) |
| **`maxReplicaCount`** | `20` (VM1 hardware ceiling) | `20` (VM2 hardware ceiling) |
| **Trigger** | Queue depth > 0 | Queue depth > 0 |
| **Effect** | VM1 workers scale up first | VM2 workers also scale — overflow |

Both KEDA controllers see the same queue. Both scale up when the queue fills.
VM1 workers consume up to their prefetch limit. VM2 workers take the rest.
**No coordination logic. No routing rules. Pure queue-driven competition.**

When traffic stops:
- Queue drains → depth = 0
- VM1 KEDA scales to `minReplicaCount: 2` (keeps 2 warm)
- VM2 KEDA scales to `minReplicaCount: 0` (VM2 runs no workload pods at all)

#### KEDA Installation (One Command)

```bash
# Install KEDA on VM1's RKE2 cluster
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda \
  --namespace keda \
  --create-namespace

# Install KEDA on VM2's RKE2 cluster (same command, different kubeconfig)
KUBECONFIG=/path/to/vm2-kubeconfig.yaml helm install keda kedacore/keda \
  --namespace keda \
  --create-namespace
```

#### KEDA ScaledObject for VM2 (Replaces the HPA on VM2)

```yaml
# Deploy this on VM2's RKE2 cluster
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: sandbox-worker-scaler
  namespace: opensandbox-system      # same namespace as your existing deployment
spec:
  scaleTargetRef:
    name: sandbox-api                # same deployment name as VM1
  minReplicaCount: 0                 # scale to zero when VM1 handles all load
  maxReplicaCount: 20
  cooldownPeriod: 300                # 5 min grace before scaling to 0
  triggers:
    - type: rabbitmq
      metadata:
        protocol: amqp
        # VM2 reaches VM1's RabbitMQ via WireGuard tunnel
        host: amqp://admin:changeme@10.99.0.1:5672/
        queueName: repo_scan_queue
        queueLength: "5"
    - type: rabbitmq
      metadata:
        protocol: amqp
        host: amqp://admin:changeme@10.99.0.1:5672/
        queueName: quick_scan_queue
        queueLength: "3"
```

---

### Q3: What Is WireGuard VPN and Why Is It Needed?

#### The Problem Without WireGuard

Your RabbitMQ runs as a Kubernetes ClusterIP service (`rabbitmq-service:5672`).
`ClusterIP` services are **only accessible from within the same Kubernetes cluster**.
VM2's pods are in a completely separate RKE2 cluster — they cannot reach
`rabbitmq-service:5672` on VM1 at all.

Without a private network tunnel between VM1 and VM2, you would have to expose
RabbitMQ (and PostgreSQL) on a public IP with open ports — which is a **serious
security vulnerability**.

#### What WireGuard Does

WireGuard creates a **private, encrypted Layer-3 VPN tunnel** between VM1 and VM2.
Once set up, VM2 sees VM1's services as if they were on a local private network:

```
VM2 pod → 10.99.0.1:5672 (WireGuard IP) → WireGuard tunnel → VM1 → rabbitmq-service:5672
```

- **Encrypted:** All traffic between the VMs is encrypted with modern cryptography (ChaCha20)
- **Fast:** WireGuard lives in the Linux kernel — near-zero overhead vs IPSec or OpenVPN
- **Permanent:** Set up once, stays running across reboots via systemd
- **Narrow scope:** Only routes traffic for the specific IPs you define — nothing else leaks

#### Exactly What Traffic Goes Through the Tunnel

| Service | VM1 Internal Address | VM2 Accesses Via Tunnel |
|:---|:---|:---|
| RabbitMQ | `rabbitmq-service:5672` | `10.99.0.1:5672` |
| PostgreSQL | `postgresql-service:5432` | `10.99.0.1:5432` |
| Redis | `redis-service:6379` | `10.99.0.1:6379` |

For this to work, VM1 needs to expose these ports on its `wg0` interface (`10.99.0.1`).
The simplest way: add `iptables` DNAT rules on VM1 that forward WireGuard traffic to
the Kubernetes NodePort or a MetalLB IP.

#### One-Time WireGuard Setup

```bash
# === ON VM1 ===

# 1. Install WireGuard
apt install wireguard

# 2. Generate keypair
wg genkey | tee /etc/wireguard/vm1_private.key | wg pubkey > /etc/wireguard/vm1_public.key
chmod 600 /etc/wireguard/vm1_private.key

# 3. Create /etc/wireguard/wg0.conf
cat > /etc/wireguard/wg0.conf << EOF
[Interface]
PrivateKey = $(cat /etc/wireguard/vm1_private.key)
Address = 10.99.0.1/24
ListenPort = 51820
# Forward WireGuard traffic to Kubernetes services
PostUp = iptables -t nat -A PREROUTING -i wg0 -p tcp --dport 5672 -j DNAT --to-destination <K8S_NODE_IP>:5672
PostUp = iptables -t nat -A PREROUTING -i wg0 -p tcp --dport 5432 -j DNAT --to-destination <K8S_NODE_IP>:5432
PostUp = iptables -t nat -A PREROUTING -i wg0 -p tcp --dport 6379 -j DNAT --to-destination <K8S_NODE_IP>:6379
PostDown = iptables -t nat -D PREROUTING -i wg0 -p tcp --dport 5672 -j DNAT --to-destination <K8S_NODE_IP>:5672
PostDown = iptables -t nat -D PREROUTING -i wg0 -p tcp --dport 5432 -j DNAT --to-destination <K8S_NODE_IP>:5432
PostDown = iptables -t nat -D PREROUTING -i wg0 -p tcp --dport 6379 -j DNAT --to-destination <K8S_NODE_IP>:6379

[Peer]
# VM2's public key (fill in after generating on VM2)
PublicKey = <VM2_PUBLIC_KEY>
AllowedIPs = 10.99.0.2/32
EOF

# 4. Enable and start (survives reboots)
systemctl enable --now wg-quick@wg0
```

```bash
# === ON VM2 ===

# 1. Install WireGuard
apt install wireguard

# 2. Generate keypair
wg genkey | tee /etc/wireguard/vm2_private.key | wg pubkey > /etc/wireguard/vm2_public.key
chmod 600 /etc/wireguard/vm2_private.key

# 3. Create /etc/wireguard/wg0.conf
cat > /etc/wireguard/wg0.conf << EOF
[Interface]
PrivateKey = $(cat /etc/wireguard/vm2_private.key)
Address = 10.99.0.2/24

[Peer]
# VM1's public key
PublicKey = <VM1_PUBLIC_KEY>
# VM1's public IP address (the OVH VPS public IP, not the private IP)
Endpoint = <VM1_OVH_PUBLIC_IP>:51820
AllowedIPs = 10.99.0.1/32
# Keeps the tunnel alive through NAT
PersistentKeepalive = 25
EOF

# 4. Enable and start
systemctl enable --now wg-quick@wg0

# 5. Test — should return RabbitMQ banner or connection
nc -zv 10.99.0.1 5672 && echo "RabbitMQ reachable via tunnel"
nc -zv 10.99.0.1 5432 && echo "PostgreSQL reachable via tunnel"
```

After completing setup, copy VM1's public key to VM2's config and vice versa,
then run `wg show` on both machines to confirm the tunnel is established.

#### Verifying the Complete Setup Works End-to-End

```bash
# On VM2, test RabbitMQ connectivity through WireGuard
python3 -c "
import pika
conn = pika.BlockingConnection(
    pika.URLParameters('amqp://admin:changeme@10.99.0.1:5672/')
)
print('RabbitMQ connected successfully via WireGuard tunnel')
conn.close()
"
```

If this returns successfully, VM2's `sandbox-api` workers can connect to VM1's RabbitMQ.
Deploy your `sandbox-api` Helm chart on VM2 with:
```yaml
# Override for VM2 deployment
RABBITMQ_URL: "amqp://admin:changeme@10.99.0.1:5672/"
PG_HOST: "10.99.0.1"
REDIS_HOST: "10.99.0.1"
```

From this point, VM2 workers compete with VM1 workers for scan jobs automatically.
KEDA manages scale-up and scale-down on both clusters.
No further manual action is ever needed to handle traffic overflow.

---

## Scaling to N Servers: Adding VM3, VM4, VM5...

**Yes — you can add as many OVH VPS servers as you need, all running the same RKE2
configuration.** Each new server joins the worker pool automatically the moment it is
set up. VM1's RabbitMQ never changes. The competing consumers pattern scales linearly
with every server you add.

### How Capacity Grows With Each Server

Assuming each OVH VPS has 16 cores / 32 GB RAM (same as VM1), and each sandbox pod
uses `0.5 CPU + 0.5 GiB RAM`:

| Servers Active | Max Concurrent Pods | Max Concurrent Scans | Approximate User Capacity |
|:---|:---|:---|:---|
| VM1 only | ~29 pods | ~29 scans | ~10–30 users |
| VM1 + VM2 | ~58 pods | ~58 scans | ~30–60 users |
| VM1 + VM2 + VM3 | ~87 pods | ~87 scans | ~60–90 users |
| VM1 + VM4 servers | ~116 pods | ~116 scans | ~100–120 users |
| VM1 + 9 servers (10 total) | ~290 pods | ~290 scans | ~250–300 users |
| VM1 + 34 servers (35 total) | ~1,000 pods | ~1,000 scans | ~1,000 users |

Every server added is additive. No architectural changes. No changes to VM1.

### Architecture: N-Server Worker Pool

```mermaid
flowchart TD
    subgraph Users["Users & API Clients"]
        Traffic["Concurrent Scan Requests (scaling with demand)"]
    end

    subgraph VM1["OVH VPS — VM1 (Primary)"]
        APIGW["API Gateway (public endpoint)"]
        RMQ[("RabbitMQ — single shared queue<br/>NEVER changes, regardless of how many VMs join")]
        DB[("PostgreSQL + Redis")]
        KEDA1["KEDA (minReplicas: 2)"]
        W1["sandbox-api workers"]
        WG1["WireGuard hub\n10.99.0.1"]
    end

    subgraph VM2["VM2 (Overflow)"]
        KEDA2["KEDA (minReplicas: 0)"]
        W2["sandbox-api workers"]
        WG2["10.99.0.2"]
    end

    subgraph VM3["VM3 (Overflow)"]
        KEDA3["KEDA (minReplicas: 0)"]
        W3["sandbox-api workers"]
        WG3["10.99.0.3"]
    end

    subgraph VMN["VM-N (Overflow — add as many as needed)"]
        KEDA_N["KEDA (minReplicas: 0)"]
        W_N["sandbox-api workers"]
        WG_N["10.99.0.N"]
    end

    Traffic --> APIGW
    APIGW --> RMQ

    RMQ -- "Competing consumers" --> W1
    RMQ -- "Competing consumers" --> W2
    RMQ -- "Competing consumers" --> W3
    RMQ -- "Competing consumers" --> W_N

    KEDA1 --> W1
    KEDA2 --> W2
    KEDA3 --> W3
    KEDA_N --> W_N

    W1 -- "Results" --> DB
    W2 -- "Results via tunnel" --> DB
    W3 -- "Results via tunnel" --> DB
    W_N -- "Results via tunnel" --> DB

    WG1 <-- "Encrypted tunnel" --> WG2
    WG1 <-- "Encrypted tunnel" --> WG3
    WG1 <-- "Encrypted tunnel" --> WG_N
```

### Repeatable Checklist: Adding Any New Server

Every new server (VM3, VM4, VM5...) follows the **exact same steps**.
Only two things change: the WireGuard IP and the public key exchange.

#### On the New Server (VMx)

```bash
# Step 1 — Install RKE2 agent (joins no cluster yet)
curl -sfL https://get.rke2.io | INSTALL_RKE2_TYPE="agent" sh -

# Step 2 — Configure RKE2 to point at VM1's control plane
mkdir -p /etc/rancher/rke2
cat > /etc/rancher/rke2/config.yaml << EOF
server: https://<VM1_IP>:9345
token: <SAME_RKE2_TOKEN_AS_VM1>
node-label:
  - "role=sandbox-worker"
  - "cluster=overflow"
EOF

# Step 3 — Start RKE2 agent — node automatically joins VM1's cluster
systemctl enable --now rke2-agent

# Step 4 — Install WireGuard
apt install wireguard

# Step 5 — Generate keypair
wg genkey | tee /etc/wireguard/vmx_private.key | wg pubkey > /etc/wireguard/vmx_public.key

# Step 6 — Configure WireGuard (use next available IP: .3, .4, .5...)
cat > /etc/wireguard/wg0.conf << EOF
[Interface]
PrivateKey = $(cat /etc/wireguard/vmx_private.key)
Address = 10.99.0.X/24         # replace X with next available: 3, 4, 5...

[Peer]
PublicKey = <VM1_PUBLIC_KEY>
Endpoint = <VM1_OVH_PUBLIC_IP>:51820
AllowedIPs = 10.99.0.1/32
PersistentKeepalive = 25
EOF

systemctl enable --now wg-quick@wg0

# Step 7 — Deploy KEDA + sandbox-api via Helm (same chart as VM1)
KUBECONFIG=/etc/rancher/rke2/rke2.yaml helm upgrade --install codeInspector ./codeInspector \
  --set apiServer.configMap.RABBITMQ_URL="amqp://admin:changeme@10.99.0.1:5672/" \
  --set apiServer.configMap.PG_HOST="10.99.0.1" \
  --set apiServer.configMap.REDIS_HOST="10.99.0.1" \
  --set apiServer.hpa.enabled=false    # disable CPU HPA — KEDA handles scaling
```

#### On VM1 — Register the New Peer (30 seconds)

```bash
# Add the new server as a WireGuard peer on VM1
# Run once per new server added
wg set wg0 peer <VMx_PUBLIC_KEY> allowed-ips 10.99.0.X/32

# Make it persistent across reboots
wg-quick save wg0

# Verify the tunnel is up
wg show
# Output will show the new peer with "latest handshake" timestamp
```

That is the complete process. **No changes to RabbitMQ, PostgreSQL, Redis, or the API Gateway on VM1.**

### How the Load Distributes Automatically

When all servers are running and load increases:

```
Queue depth = 0        → All VM2..VMN have 0 pods (KEDA minReplicas: 0)
Queue depth = 1–50     → VM1 KEDA scales VM1 workers up. Others stay at 0.
Queue depth = 51–100   → VM1 is full. VM2 KEDA activates. VM2 workers scale up.
Queue depth = 101–150  → VM1 + VM2 full. VM3 KEDA activates. VM3 workers scale up.
Queue depth = 150+     → All VMs active, consuming in parallel.
Queue drains           → KEDA scales VM2..VMN back to 0. VM1 stays at minReplicas: 2.
```

The queue is the universal signal. Every KEDA instance reacts to it independently.
No central coordinator. No routing table. No human decision.

### When to Add Another Server vs. Upgrading Existing Ones

| Situation | Action |
|:---|:---|
| Consistent high queue depth, VM1 + VM2 both full | Add VM3 |
| Queue spikes are short bursts only | Tune KEDA `cooldownPeriod` before adding more VMs |
| All VMs at < 50% utilization most of the time | You have enough servers; reduce `maxReplicaCount` to save cost |
| Need more than ~10 VMs regularly | Consider migrating to OVH Public Cloud for auto-provisioned elastic nodes |

---

# Transparent Multi-Cluster Topology: Manager's Architectural Requirement

> **Manager's Requirement (verbatim):**
> *"The core challenge is not scaling nodes within a single cluster, but rather managing
> multiple clusters across different regions transparently. The objective is for the
> application to handle workload scheduling across various clusters without needing to be
> aware of the underlying multi-cluster topology, ensuring that agents can consistently
> reach the nearest data center for performance."*

This is a fundamentally different problem from the N-server overflow approach described
above. It requires a dedicated **multi-cluster control plane** — software that sits above
individual clusters and makes them appear as a single unified compute surface to the
application.

---

## What "Transparent Multi-Cluster" Actually Means

In a transparent multi-cluster setup:

| Without Transparency | With Transparency |
|:---|:---|
| `sandbox-api` on VM1 knows it talks to a specific RabbitMQ on VM1 | `sandbox-api` talks to a `rabbitmq-service` — it doesn't know which cluster serves it |
| Jobs are routed manually to specific clusters | Jobs flow to the nearest/least-loaded cluster automatically |
| Adding a new cluster requires updating application config | Adding a new cluster is a control-plane operation; app sees nothing |
| An agent in Europe talks to a server in Asia (high latency) | An agent in Europe always resolves to the nearest European cluster |

The application code — your `sandbox-api`, `consumer.py`, queue publishers — **does not
change at all**. The multi-cluster control plane handles everything underneath.

---

## The Three Layers Required

To fully satisfy the manager's requirement, three independent layers must work together:

```
Layer 1: Geo-DNS / Anycast Routing
   └─ Users & agents reach the nearest regional entry point automatically
         │
Layer 2: Multi-Cluster Control Plane (Karmada)
   └─ Workloads are scheduled to the right cluster without app awareness
         │
Layer 3: Cross-Cluster Networking (Cilium Cluster Mesh / Submariner)
   └─ Services in any cluster are reachable from any other cluster transparently
```

---

## Layer 1: Geo-DNS / Anycast — Nearest Datacenter Routing

### What It Solves
When a user in Germany submits a scan, their API request should go to the nearest
European cluster — not bounce to a server in Asia or the US.

### How It Works
A **GeoDNS resolver** (Cloudflare or AWS Route 53) maps the same domain name
(`api-sandbox.01security.com`) to different IP addresses based on where the request
originates.

```
User in Germany → DNS resolve api-sandbox.01security.com
    → Cloudflare GeoDNS checks origin: Europe
    → Returns IP of EU Cluster entry point

User in Singapore → DNS resolve api-sandbox.01security.com
    → Cloudflare GeoDNS checks origin: Asia-Pacific
    → Returns IP of AP Cluster entry point
```

The user and the application see the **same domain name**. The network handles geography.

### Architecture Diagram

```mermaid
flowchart TD
    subgraph Users["Global Users"]
        EU_User["User in Europe"]
        US_User["User in USA"]
        AP_User["User in Asia"]
    end

    subgraph DNS["Cloudflare GeoDNS / Route 53 Latency Routing"]
        GEO["api-sandbox.01security.com<br/>Same domain — different IP per region<br/>Health-check based failover included"]
    end

    subgraph RegionEU["EU Cluster Entry Point"]
        EU_LB["Load Balancer<br/>EU Public IP"]
    end

    subgraph RegionUS["US Cluster Entry Point"]
        US_LB["Load Balancer<br/>US Public IP"]
    end

    subgraph RegionAP["AP Cluster Entry Point"]
        AP_LB["Load Balancer<br/>AP Public IP"]
    end

    EU_User -- "DNS query" --> GEO
    US_User -- "DNS query" --> GEO
    AP_User -- "DNS query" --> GEO

    GEO -- "Returns EU IP (lowest latency)" --> EU_User
    GEO -- "Returns US IP (lowest latency)" --> US_User
    GEO -- "Returns AP IP (lowest latency)" --> AP_User

    EU_User --> EU_LB
    US_User --> US_LB
    AP_User --> AP_LB
```

### Cloudflare Setup (Zero-Touch After Initial Config)

```
1. Add your clusters' public IPs as A records in Cloudflare:
   api-sandbox.01security.com → EU IP  (Cloudflare location: Europe)
   api-sandbox.01security.com → US IP  (Cloudflare location: North America)
   api-sandbox.01security.com → AP IP  (Cloudflare location: Asia Pacific)

2. Enable "Load Balancing" with "Geo-steering" in Cloudflare dashboard.

3. Add health checks: if EU cluster is down, EU traffic auto-routes to US.

Result: All clusters use the same domain. No application config changes.
```

---

## Layer 2: Karmada — Transparent Multi-Cluster Workload Scheduling

### What Karmada Is

**Karmada** (Kubernetes Armada) is a CNCF project that provides a **unified Kubernetes
API** on top of multiple member clusters. You deploy your workloads (Deployments,
Services, ConfigMaps) to the **Karmada control plane** using standard `kubectl` — and
Karmada automatically distributes them to member clusters based on policies you define
once.

**The application never knows which cluster it runs on.** It sees one Kubernetes API.

### How Karmada Achieves Topology Transparency

```
You (DevOps) submit:                    Karmada decides:
                                              │
kubectl apply -f sandbox-api.yaml →    ┌─────┴──────────────────────┐
                                       │ Karmada Scheduler           │
                                       │ - EU cluster: 40% load      │
                                       │ - US cluster: 20% load      │
                                       │ - AP cluster: 80% load      │
                                       │                              │
                                       │ → Send 60% replicas to US   │
                                       │ → Send 30% replicas to EU   │
                                       │ → Send 10% replicas to AP   │
                                       └─────────────────────────────┘
                                              │
                         sandbox-api runs across all clusters.
                         The Deployment YAML you submitted was unmodified.
```

### Architecture Diagram

```mermaid
flowchart TD
    subgraph DevOps["DevOps / CI-CD Pipeline"]
        Dev["kubectl apply -f sandbox-api.yaml<br/>One command. Standard Kubernetes YAML.<br/>No cluster-specific changes."]
    end

    subgraph KarmadaHub["Karmada Control Plane (Management Cluster)"]
        KarmadaAPI["Karmada API Server<br/>(drop-in replacement for kubectl target)"]
        Scheduler["Karmada Scheduler<br/>Reads PropagationPolicy<br/>Decides which clusters get which workloads"]
        PropPolicy["PropagationPolicy<br/>(defined once — governs all scheduling forever)"]
        OverridePolicy["OverridePolicy<br/>(per-cluster config overrides if needed)"]
    end

    subgraph ClusterEU["Member Cluster: EU (RKE2)"]
        EU_API["kube-apiserver"]
        EU_Pods["sandbox-api pods<br/>scanner pods<br/>RabbitMQ consumers"]
    end

    subgraph ClusterUS["Member Cluster: US (RKE2)"]
        US_API["kube-apiserver"]
        US_Pods["sandbox-api pods<br/>scanner pods<br/>RabbitMQ consumers"]
    end

    subgraph ClusterAP["Member Cluster: AP (RKE2)"]
        AP_API["kube-apiserver"]
        AP_Pods["sandbox-api pods<br/>scanner pods<br/>RabbitMQ consumers"]
    end

    Dev --> KarmadaAPI
    KarmadaAPI --> Scheduler
    Scheduler -- "Reads policy" --> PropPolicy
    PropPolicy -- "Propagates to EU" --> EU_API
    PropPolicy -- "Propagates to US" --> US_API
    PropPolicy -- "Propagates to AP" --> AP_API
    EU_API --> EU_Pods
    US_API --> US_Pods
    AP_API --> AP_Pods
```

### Karmada PropagationPolicy for 01-Sandbox

This is defined **once** in Karmada and governs all future scheduling automatically:

```yaml
# Define how sandbox-api is spread across all clusters
apiVersion: policy.karmada.io/v1alpha1
kind: PropagationPolicy
metadata:
  name: sandbox-api-propagation
  namespace: opensandbox-system
spec:
  resourceSelectors:
    - apiVersion: apps/v1
      kind: Deployment
      name: sandbox-api             # your existing deployment — unchanged
    - apiVersion: v1
      kind: Service
      name: sandbox-api-service
    - apiVersion: v1
      kind: ConfigMap
      name: sandbox-api-config
  placement:
    clusterAffinity:
      clusterNames:
        - cluster-eu
        - cluster-us
        - cluster-ap
    replicaScheduling:
      replicaSchedulingType: Divided
      replicaDivisionPreference: Weighted
      weightPreference:
        staticClusterWeight:
          - targetCluster:
              clusterNames: [cluster-eu]
            weight: 3               # EU gets 3/9 = 33% of replicas
          - targetCluster:
              clusterNames: [cluster-us]
            weight: 3               # US gets 3/9 = 33% of replicas
          - targetCluster:
              clusterNames: [cluster-ap]
            weight: 3               # AP gets 3/9 = 33% of replicas
```

To use **dynamic load-based scheduling** instead of static weights:

```yaml
spec:
  placement:
    replicaScheduling:
      replicaSchedulingType: Divided
      replicaDivisionPreference: Aggregated   # fill one cluster before using next
    clusterTolerations:
      - key: "cluster.karmada.io/load"
        operator: Lt
        value: "80"   # only schedule to clusters with < 80% load
```

### Joining RKE2 Clusters to Karmada

```bash
# Install Karmada on a dedicated management VM (or any existing cluster)
kubectl karmada init

# Register EU RKE2 cluster
kubectl karmada join cluster-eu \
  --cluster-kubeconfig=/path/to/eu-rke2.yaml \
  --cluster-context=default

# Register US RKE2 cluster
kubectl karmada join cluster-us \
  --cluster-kubeconfig=/path/to/us-rke2.yaml \
  --cluster-context=default

# Register AP RKE2 cluster
kubectl karmada join cluster-ap \
  --cluster-kubeconfig=/path/to/ap-rke2.yaml \
  --cluster-context=default

# Verify all clusters are registered and healthy
kubectl get clusters
# NAME         VERSION   MODE   READY   AGE
# cluster-eu   v1.29.0   Push   True    2m
# cluster-us   v1.29.0   Push   True    1m
# cluster-ap   v1.29.0   Push   True    45s
```

From this point, `kubectl apply` to the Karmada API server distributes workloads across
all three clusters automatically — no application changes, no cluster-specific targeting.

---

## Layer 3: Cilium Cluster Mesh — Transparent Cross-Cluster Service Networking

### What It Solves

When `sandbox-api` on the EU cluster wants to write scan results to PostgreSQL, it calls
`postgresql-service.opensandbox-system.svc.cluster.local`. In a single cluster, this
works. In a multi-cluster setup, this DNS name only resolves within the same cluster.

**Cilium Cluster Mesh** solves this by making services globally discoverable across all
clusters using the **same service name** — the application never changes its connection
string.

### How It Works

When you annotate a service with `service.cilium.io/global: "true"`, Cilium:
1. Exports the service's backend endpoints to all other clusters in the mesh.
2. Each cluster's local DNS still resolves `postgresql-service` — but the traffic
   is load-balanced across all clusters' backends, preferring local ones first.
3. If the local cluster's backend is unhealthy, traffic transparently flows to a
   healthy backend in another cluster — with no app awareness.

### Global Service with Local Affinity (Nearest DC First)

```yaml
# PostgreSQL service — annotate once, Cilium handles the rest
apiVersion: v1
kind: Service
metadata:
  name: postgresql-service
  namespace: opensandbox-system
  annotations:
    service.cilium.io/global: "true"           # visible to all clusters in mesh
    service.cilium.io/shared: "true"           # share endpoints cross-cluster
    service.cilium.io/affinity: "local"        # prefer local cluster backend FIRST
                                               # fall back to remote only if local is down
spec:
  selector:
    app: postgresql
  ports:
    - port: 5432
```

```yaml
# RabbitMQ service — same pattern
apiVersion: v1
kind: Service
metadata:
  name: rabbitmq-service
  namespace: opensandbox-system
  annotations:
    service.cilium.io/global: "true"
    service.cilium.io/affinity: "local"       # EU workers use EU RabbitMQ by default
spec:
  selector:
    app: rabbitmq
  ports:
    - port: 5672
```

With `affinity: local`:
- EU `sandbox-api` workers connect to EU RabbitMQ → lowest latency
- If EU RabbitMQ goes down → Cilium transparently routes to US or AP RabbitMQ
- No `RABBITMQ_URL` changes. No deployment restarts.

### Cluster Mesh Setup (One-Time)

```bash
# Enable Cluster Mesh on each RKE2 cluster
# (Cilium must be the CNI on all clusters — install via helm if not already)

cilium clustermesh enable --context cluster-eu
cilium clustermesh enable --context cluster-us
cilium clustermesh enable --context cluster-ap

# Connect all clusters into the mesh
cilium clustermesh connect \
  --context cluster-eu \
  --destination-context cluster-us

cilium clustermesh connect \
  --context cluster-eu \
  --destination-context cluster-ap

cilium clustermesh connect \
  --context cluster-us \
  --destination-context cluster-ap

# Verify mesh health
cilium clustermesh status --context cluster-eu
```

---

## Alternative to Cilium: Submariner (CNI-Agnostic)

If you cannot use Cilium as the CNI (e.g., you're already using Flannel or Calico with
RKE2), **Submariner** provides equivalent cross-cluster service discovery without
requiring a CNI change.

Submariner uses a **Lighthouse** DNS component that makes remote services resolvable
as `service.namespace.svc.clusterset.local`:

```bash
# Install Submariner broker (on the management cluster or any cluster)
subctl deploy-broker --context cluster-eu

# Join all clusters to the broker
subctl join --context cluster-eu broker-info.subm
subctl join --context cluster-us broker-info.subm
subctl join --context cluster-ap broker-info.subm

# Export a service so it's visible cross-cluster
kubectl --context cluster-eu apply -f - <<EOF
apiVersion: multicluster.x-k8s.io/v1alpha1
kind: ServiceExport
metadata:
  name: rabbitmq-service
  namespace: opensandbox-system
EOF
```

After export, other clusters resolve:
`rabbitmq-service.opensandbox-system.svc.clusterset.local` → EU RabbitMQ

---

## Full Combined Architecture: All Three Layers Together

```mermaid
flowchart TD
    subgraph Users["Global Users & CI/CD Agents"]
        EU_User["EU User / Agent"]
        US_User["US User / Agent"]
        AP_User["AP User / Agent"]
    end

    subgraph GeoDNS["Layer 1: Cloudflare GeoDNS"]
        DNS["api-sandbox.01security.com<br/>Routes to nearest region automatically"]
    end

    subgraph KarmadaCP["Layer 2: Karmada Control Plane"]
        Karmada["Karmada API + Scheduler<br/>PropagationPolicy drives all scheduling<br/>DevOps applies one YAML — runs everywhere"]
    end

    subgraph CiliumMesh["Layer 3: Cilium Cluster Mesh"]
        CM["Global Services<br/>service.cilium.io/global: true<br/>affinity: local — use nearest DC first<br/>Cross-cluster failover is automatic"]
    end

    subgraph EU["EU RKE2 Cluster (Member)"]
        EU_GW["API Gateway<br/>(EU entry point)"]
        EU_RMQ[("EU RabbitMQ<br/>(local-affinity primary)")]
        EU_DB[("EU PostgreSQL")]
        EU_Workers["sandbox-api workers<br/>Scanner pods"]
    end

    subgraph US["US RKE2 Cluster (Member)"]
        US_GW["API Gateway"]
        US_RMQ[("US RabbitMQ")]
        US_DB[("US PostgreSQL")]
        US_Workers["sandbox-api workers<br/>Scanner pods"]
    end

    subgraph AP["AP RKE2 Cluster (Member)"]
        AP_GW["API Gateway"]
        AP_RMQ[("AP RabbitMQ")]
        AP_DB[("AP PostgreSQL")]
        AP_Workers["sandbox-api workers<br/>Scanner pods"]
    end

    EU_User -- "DNS resolves to EU" --> DNS
    US_User -- "DNS resolves to US" --> DNS
    AP_User -- "DNS resolves to AP" --> DNS

    DNS --> EU_GW
    DNS --> US_GW
    DNS --> AP_GW

    Karmada -- "Propagates Deployments<br/>to all member clusters" --> EU
    Karmada -- "Propagates Deployments" --> US
    Karmada -- "Propagates Deployments" --> AP

    CM -- "Global service mesh<br/>local-first routing" --> EU_RMQ
    CM -- "Global service mesh" --> US_RMQ
    CM -- "Global service mesh" --> AP_RMQ

    EU_GW --> EU_RMQ --> EU_Workers --> EU_DB
    US_GW --> US_RMQ --> US_Workers --> US_DB
    AP_GW --> AP_RMQ --> AP_Workers --> AP_DB
```

---

## Tool Comparison for the Manager's Requirement

| Tool | Problem It Solves | Topology Transparency | Application Changes? |
|:---|:---|:---|:---|
| **Karmada** | Workload scheduling across clusters | ✅ Full — app submits to one API | None |
| **Cilium Cluster Mesh** | Cross-cluster service discovery + locality routing | ✅ Full — same service names | None |
| **Submariner** | Cross-cluster service DNS (CNI-agnostic) | ✅ Full — `svc.clusterset.local` DNS | None |
| **Cloudflare GeoDNS** | Route users to nearest datacenter | ✅ Full — same domain name | None |
| **Karmada OverridePolicy** | Per-cluster config customization | ✅ Full — base YAML unchanged | None |
| **WireGuard (previous approach)** | Cross-VM encrypted networking (VPS only) | ❌ Partial — requires env var changes | RABBITMQ_URL per VM |
| **Competing consumers (previous)** | RabbitMQ overflow across VMs | ❌ Partial — app must share same queue | Env var change |

---

## Implementation Roadmap for the Manager's Requirement

```mermaid
flowchart LR
    A["Phase 0: Now<br/>Single RKE2 on OVH VPS<br/>Manual everything"] --> B

    B["Phase 1: 2–4 weeks<br/>Karmada Control Plane<br/>Join existing + new clusters<br/>PropagationPolicy defined<br/>App topology is now invisible to devs"] --> C

    C["Phase 2: 2–3 weeks<br/>Cilium Cluster Mesh<br/>Global services with local affinity<br/>RabbitMQ, PostgreSQL, Redis<br/>all route to nearest DC automatically"] --> D

    D["Phase 3: 1 week<br/>Cloudflare GeoDNS<br/>Users reach nearest entry point<br/>Automatic failover to next region<br/>Full zero-touch multi-region active-active"]
```

### What DevOps Does After Full Implementation

```bash
# Deploy sandbox-api update to ALL clusters simultaneously:
kubectl apply -f sandbox-api-deployment.yaml   # <-- Karmada API target

# That's it. Karmada distributes it. Cilium routes traffic.
# DNS sends users to the right region. No cluster-specific steps.
```

### What the Application Code Does

```python
# consumer.py — UNCHANGED
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "")
# Value: amqp://admin:pass@rabbitmq-service:5672/
# Cilium Cluster Mesh resolves "rabbitmq-service" to the LOCAL cluster's RabbitMQ.
# If local RabbitMQ is down, Cilium silently routes to the nearest healthy one.
# consumer.py never knows this happened.
```

Zero application changes. Full multi-cluster transparency. Agents always reach the
nearest datacenter. This is exactly what the manager's requirement describes.

---

# BerryBytes k8s-multicluster-handbook: Ready-Made Implementation

> **Repository:** [https://github.com/BerryBytes/k8s-multicluster-handbook](https://github.com/BerryBytes/k8s-multicluster-handbook)
>
> *"A beginner-friendly, automated multi-cluster setup using Open Cluster Management (OCM),
> and Cilium — deploy and manage Kubernetes workloads across clusters with ease."*

This repository is a **turnkey reference implementation** of the exact architecture
described in the previous sections. It provides working scripts, Helm chart values,
cluster configurations, and examples that can be adapted for production OVH RKE2 clusters.

---

## How the Handbook Maps to Our Architecture

| Architecture Layer (Previous Section) | What the Handbook Provides |
|:---|:---|
| **Layer 2: Multi-cluster control plane** | **Open Cluster Management (OCM)** — hub-spoke model replacing Karmada. Equivalent workload distribution and placement policies. |
| **Layer 3: Cross-cluster networking** | **Cilium Cluster Mesh** — identical to what was described. Same `service.cilium.io/global` annotations. Same `affinity: local`. |
| **GitOps deployment** | **ArgoCD** installed on hub, with addons pushed to spoke clusters automatically |
| **Load balancing** | **MetalLB** on each cluster — equivalent to your existing MetalLB in `values.yaml` |
| **Ingress** | **NGINX Ingress Controller** — equivalent to your existing agentgateway setup |

**Key difference from the Karmada approach:** OCM (Open Cluster Management) is the
CNCF-standard alternative. Both achieve full workload transparency. OCM uses a
**hub-spoke** model with `ManagedCluster`, `Placement`, and `ManifestWorkReplicaSet`
resources instead of Karmada's `PropagationPolicy`.

---

## Repository Structure

```
k8s-multicluster-handbook/
├── scripts/
│   ├── multicluster_bootstrap.sh     # One-command full environment setup
│   ├── install_metallb.sh            # Per-cluster MetalLB setup with IP pools
│   └── setup_cluster_certs.sh        # TLS + cert-manager setup (optional)
├── charts/
│   └── cilium/
│       └── cilium-values.yaml        # Cilium cluster mesh config per cluster
├── cluster-config/
│   ├── hub.config                    # KinD config: hub cluster (pods 10.12.0.0/16)
│   ├── east.config                   # KinD config: east spoke (pods 10.16.0.0/16)
│   └── west.config                   # KinD config: west spoke (pods 10.18.0.0/16)
├── examples/                         # Sample workloads and placement policies
├── docs/
│   └── CONTRIBUTING.md
└── README.md
```

---

## What the Stack Provides

| Component | Role | Applied To |
|:---|:---|:---|
| **Open Cluster Management (OCM)** | Hub manages spoke clusters — distribute workloads, apply policies, track health | Hub only |
| **ArgoCD** | GitOps delivery — deployed on hub, agent pushed to each spoke automatically | Hub + spokes via OCM addon |
| **Cilium Cluster Mesh** | Cross-cluster pod-to-pod networking + global service discovery with local affinity | All clusters |
| **MetalLB** | LoadBalancer service IPs for bare-metal/VPS clusters | All clusters |
| **NGINX Ingress Controller** | External HTTP/HTTPS access to services | All clusters |
| **cert-manager** | Automatic TLS certificate issuance | All clusters (optional) |
| **MCS API CRDs** | `ServiceExport`/`ServiceImport` for standard cross-cluster service sharing | All clusters |

---

## Network Configuration (Pre-Configured, Non-Overlapping CIDRs)

Each cluster uses isolated network ranges to avoid IP conflicts across the mesh:

| Cluster | Role | Pod CIDR | Service CIDR | API Port |
|:---|:---|:---|:---|:---|
| `hub` | Control plane | `10.12.0.0/16` | `10.13.0.0/16` | `6443` |
| `east` | Spoke / worker | `10.16.0.0/16` | `10.17.0.0/16` | `9443` |
| `west` | Spoke / worker | `10.18.0.0/16` | `10.19.0.0/16` | `10443` |

> [!IMPORTANT]
> When adapting to OVH RKE2 clusters, use these same non-overlapping CIDRs.
> RKE2 cluster CIDR is set in `/etc/rancher/rke2/config.yaml` with `cluster-cidr`
> and `service-cidr` keys. Each cluster must have unique, non-overlapping ranges.

---

## Quick Start: Local Lab Setup (KinD — for Testing)

Use this to test the full stack locally before deploying to OVH:

```bash
# Clone the repository
git clone https://github.com/BerryBytes/k8s-multicluster-handbook.git
cd k8s-multicluster-handbook

# Make the bootstrap script executable
chmod +x scripts/multicluster_bootstrap.sh

# Run the complete automated setup (creates hub + east + west clusters)
./scripts/multicluster_bootstrap.sh
```

The script runs **12 automated steps** that mirror a full production setup:

```
Step  1/12: Create hub cluster
Step  2/12: Create east spoke cluster
Step  3/12: Create west spoke cluster
Step  4/12: Install Cilium on all clusters
Step  5/12: Configure MetalLB on all clusters
Step  6/12: Install NGINX Ingress on all clusters
Step  7/12: Install ArgoCD on hub
Step  8/12: Initialize OCM hub
Step  9/12: Join east + west spokes to hub
Step 10/12: Accept managed clusters on hub
Step 11/12: Enable ArgoCD addon on all spokes
Step 12/12: Connect Cilium Cluster Mesh between all clusters
```

---

## Detailed Setup Phases (Manual / Production)

### Phase 1: Cluster Creation + MCS API CRDs

```bash
# Create KinD clusters (for local lab)
kind create cluster --name hub  --config cluster-config/hub.config
kind create cluster --name east --config cluster-config/east.config
kind create cluster --name west --config cluster-config/west.config

# Install Multi-Cluster Services API CRDs on ALL clusters
# (enables ServiceExport / ServiceImport — needed by Cilium for global services)
for ctx in kind-hub kind-east kind-west; do
  kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/mcs-api/master/config/crd/multicluster.x-k8s.io_serviceexports.yaml --context $ctx
  kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/mcs-api/master/config/crd/multicluster.x-k8s.io_serviceimports.yaml --context $ctx
done
```

### Phase 2: Cilium CNI + Cluster Mesh Networking

Each cluster needs Cilium installed with a **unique `cluster.id` and `cluster.name`**
to enable cluster mesh. The `cilium-values.yaml` in the repo pre-configures this:

```bash
helm repo add cilium https://helm.cilium.io/
helm repo update

# Install Cilium on each cluster with unique IDs
helm install cilium cilium/cilium \
  --namespace kube-system \
  --kube-context kind-hub \
  -f charts/cilium/cilium-values.yaml

helm install cilium cilium/cilium \
  --namespace kube-system \
  --kube-context kind-east \
  -f charts/cilium/cilium-values.yaml

helm install cilium cilium/cilium \
  --namespace kube-system \
  --kube-context kind-west \
  -f charts/cilium/cilium-values.yaml
```

Install MetalLB for LoadBalancer services on each cluster:

```bash
./scripts/install_metallb.sh kind-hub  hub
./scripts/install_metallb.sh kind-east east
./scripts/install_metallb.sh kind-west west
```

### Phase 3: Open Cluster Management (OCM) + ArgoCD

```bash
# Install ArgoCD on hub
kubectl create namespace argocd --context kind-hub
kubectl apply -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml \
  --context kind-hub

# Initialize OCM hub control plane
clusteradm init --wait --context kind-hub

# Generate join token from hub and join spoke clusters
join_cmd=$(clusteradm get token --context kind-hub)
clusteradm join $join_cmd --cluster-name east --context kind-east --force-internal-endpoint-lookup
clusteradm join $join_cmd --cluster-name west --context kind-west --force-internal-endpoint-lookup

# Wait ~30 seconds, then accept join requests on hub
sleep 30
clusteradm accept --clusters east,west --context kind-hub

# Label clusters for placement policies (e.g. by geographic location)
kubectl --context kind-hub label managedcluster east \
  cluster.open-cluster-management.io/clusterset=location-es --overwrite
kubectl --context kind-hub label managedcluster west \
  cluster.open-cluster-management.io/clusterset=location-es --overwrite

# Deploy ArgoCD agent on spokes via OCM addon
kubectl config use-context kind-hub
clusteradm install hub-addon --names argocd
clusteradm addon enable --names argocd --clusters east,west
```

### Phase 4: Enable Cilium Cluster Mesh Cross-Cluster Connectivity

```bash
# Connect all clusters into the mesh (bidirectional)
cilium clustermesh connect --context kind-hub  --destination-context kind-east
cilium clustermesh connect --context kind-hub  --destination-context kind-west
cilium clustermesh connect --context kind-east --destination-context kind-west
```

After this step, pods in any cluster can reach services in any other cluster.

---

## Verification Commands

```bash
# 1. Check all KinD clusters exist
kind get clusters
# hub
# east
# west

# 2. Verify OCM managed clusters are ready
kubectl --context kind-hub get managedclusters
# NAME   HUB ACCEPTED   MANAGED CLUSTER URLS   JOINED   AVAILABLE   AGE
# east   true           ...                    True     True        5m
# west   true           ...                    True     True        5m

# 3. Verify ArgoCD addon deployed on spokes
kubectl --context kind-hub get managedclusteraddons -A

# 4. Check Cilium cluster mesh status on each cluster
cilium clustermesh status --context kind-hub
cilium clustermesh status --context kind-east
cilium clustermesh status --context kind-west

# 5. Check OCM placement decisions
kubectl --context kind-hub get placementdecisions -A

# 6. Access ArgoCD UI (hub)
kubectl --context kind-hub -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d
kubectl --context kind-hub port-forward svc/argocd-server -n argocd 8080:443
# Open: https://localhost:8080 | user: admin
```

---

## OCM vs Karmada: Which to Use

Both OCM and Karmada achieve full workload transparency. The choice depends on your
existing tooling:

| | Open Cluster Management (OCM) | Karmada |
|:---|:---|:---|
| **Origin** | Red Hat / IBM (CNCF) | Huawei (CNCF) |
| **Model** | Hub-spoke with `ManagedCluster` | Hub-spoke with `PropagationPolicy` |
| **GitOps** | ArgoCD addon built-in | Bring your own GitOps |
| **Placement API** | `Placement` + `PlacementDecision` | `PropagationPolicy` (more flexible) |
| **Workload push** | `ManifestWork` / `ManifestWorkReplicaSet` | Native `PropagationPolicy` |
| **This handbook** | ✅ Fully covered | Not covered (use handbook for OCM) |
| **Rancher/RKE2 integration** | Good — Rancher has MCM built on OCM | Good — standalone |

**For this project:** Use OCM if you want the handbook's automation scripts.
Use Karmada if you want simpler YAML-only placement policies.

---

## Adapting the Handbook for OVH RKE2 (Production)

The handbook uses KinD for local testing. For OVH RKE2 clusters, replace the KinD
cluster creation steps with your existing RKE2 clusters:

### Step 1: Skip KinD — Use Your RKE2 Clusters

```bash
# Your existing RKE2 cluster on VM1 = hub context
export KUBECONFIG=/etc/rancher/rke2/rke2.yaml
kubectl config rename-context default kind-hub   # rename to match handbook scripts

# Your VM2 RKE2 cluster = east context
KUBECONFIG=/path/to/vm2-rke2.yaml kubectl config rename-context default kind-east

# Your VM3 RKE2 cluster = west context
KUBECONFIG=/path/to/vm3-rke2.yaml kubectl config rename-context default kind-west
```

### Step 2: Set Non-Overlapping CIDRs on Each RKE2 Cluster

Edit `/etc/rancher/rke2/config.yaml` on each server **before** first RKE2 start:

```yaml
# On VM1 (hub):
cluster-cidr: "10.12.0.0/16"
service-cidr: "10.13.0.0/16"

# On VM2 (east):
cluster-cidr: "10.16.0.0/16"
service-cidr: "10.17.0.0/16"

# On VM3 (west):
cluster-cidr: "10.18.0.0/16"
service-cidr: "10.19.0.0/16"
```

### Step 3: Run the Handbook Steps (Phases 2–4)

With your RKE2 kubeconfigs merged and renamed to match handbook context names, you
can run Phases 2, 3, and 4 from the handbook **unchanged** — Cilium install, OCM
init, cluster join, ArgoCD addon, and Cluster Mesh connect all work the same.

```bash
# Install MCS API CRDs on your RKE2 clusters
for ctx in kind-hub kind-east kind-west; do
  kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/mcs-api/master/config/crd/multicluster.x-k8s.io_serviceexports.yaml --context $ctx
  kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/mcs-api/master/config/crd/multicluster.x-k8s.io_serviceimports.yaml --context $ctx
done

# Install MetalLB (you already have this — skip or reuse existing)
# Install Cilium (RKE2 ships with its own CNI — override with Cilium)
# Install OCM + ArgoCD (same commands as handbook Phase 3)
# Connect Cilium Cluster Mesh (same commands as handbook Phase 4)
```

### Step 4: Annotate Your Existing Services for Cluster Mesh

After mesh is connected, annotate your existing services so they are globally
discoverable with local-first routing:

```yaml
# rabbitmq-service — in codeInspector/charts/apiServer/templates/rabbitmq.yaml
# Add these annotations:
metadata:
  annotations:
    service.cilium.io/global: "true"
    service.cilium.io/affinity: "local"   # EU workers use EU RabbitMQ first
```

```yaml
# postgresql-service and redis-service — same pattern
metadata:
  annotations:
    service.cilium.io/global: "true"
    service.cilium.io/affinity: "local"
```

### Step 5: Create OCM Placement Policy for sandbox-api

```yaml
# Deploy this on the hub cluster after OCM is initialized
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: Placement
metadata:
  name: sandbox-api-placement
  namespace: opensandbox-system
spec:
  numberOfClusters: 3
  clusterSets:
    - location-es
  predicates:
    - requiredClusterSelector:
        labelSelector:
          matchExpressions:
            - key: cluster.open-cluster-management.io/clusterset
              operator: In
              values: [location-es]
---
apiVersion: work.open-cluster-management.io/v1alpha1
kind: ManifestWorkReplicaSet
metadata:
  name: sandbox-api-workload
  namespace: opensandbox-system
spec:
  placementRefs:
    - name: sandbox-api-placement
      rolloutStrategy:
        type: RollingUpdate
  manifestWorkTemplate:
    spec:
      workload:
        manifests:
          - apiVersion: apps/v1
            kind: Deployment
            metadata:
              name: sandbox-api
              namespace: opensandbox-system
            spec:
              # ... your existing sandbox-api deployment spec unchanged
```

OCM reads this and pushes `sandbox-api` to all clusters matching the `Placement`.
**Your Deployment YAML is unchanged. Application code is unchanged.**

---

## Troubleshooting (From the Handbook)

### Cluster Creation Fails
```bash
# Ensure Docker/container runtime is running
sudo systemctl start docker
# Check resource availability
docker system df
# Fix inotify limits if KinD cluster creation stalls
sudo sysctl fs.inotify.max_user_watches=100000
sudo sysctl fs.inotify.max_user_instances=100000
```

### OCM Join Fails
- Check network connectivity between clusters (WireGuard tunnel if on OVH VPS)
- Verify API server endpoints are accessible from other clusters
- Review cluster join token — tokens expire after ~24 hours

### Cilium Cluster Mesh Issues
```bash
# Check Cilium pod health on each cluster
kubectl get pods -n kube-system --context kind-hub
# Check full Cilium status
cilium status --context kind-hub
# View Cilium logs
kubectl logs -n kube-system -l k8s-app=cilium --context kind-east
# Verify cluster mesh connectivity
cilium clustermesh status --context kind-hub
```

---

## Reference Links (from the Handbook)

| Resource | URL |
|:---|:---|
| Open Cluster Management docs | https://open-cluster-management.io/docs/ |
| ArgoCD docs | https://argo-cd.readthedocs.io/ |
| Cilium Cluster Mesh guide | https://docs.cilium.io/en/stable/gettingstarted/clustermesh/ |
| KinD docs | https://kind.sigs.k8s.io/ |
| MCS API (ServiceExport/Import) | https://github.com/kubernetes-sigs/mcs-api |
| KinD docs | https://kind.sigs.k8s.io/ |
| MCS API (ServiceExport/Import) | https://github.com/kubernetes-sigs/mcs-api |
| k8s-multicluster-handbook repo | https://github.com/BerryBytes/k8s-multicluster-handbook |

---

# Applicability to Current Configuration: What Actually Changes

> [!IMPORTANT]
> This section documents the confirmed state of the **live RKE2 cluster** and gives a
> precise, honest answer on what must change for each approach — so you can make
> an informed decision between OCM (handbook) and Karmada.

## Confirmed Current Cluster State

Running `kubectl get nodes` and `kubectl get pods -n kube-system` on the live cluster confirms:

| Property | Current Value |
|:---|:---|
| **Node** | `bb-mp-plat-03` (single node) |
| **RKE2 version** | `v1.30.5+rke2r1` |
| **OS** | Ubuntu 24.04.4 LTS |
| **Pod CIDR** | `10.42.0.0/24` |
| **API server** | `https://127.0.0.1:6443` |
| **CNI** | **Cilium** (already installed — `cilium-mn8zt`, `cilium-operator` both Running) |
| **Clusters** | 1 (single cluster, single node) |
| **Karmada installed** | No |
| **OCM installed** | No |
| **ArgoCD installed** | No |

### This Confirms the Most Important Thing

**You already run Cilium.** This removes the largest obstacle to the entire
multi-cluster architecture. Cilium Cluster Mesh can be enabled on your existing
cluster without any CNI migration, without any downtime, and without touching a
single line of application code.

---

## Is the Handbook (OCM + Cilium) Applicable Without Major Changes?

**Yes. The minimum change set is tiny.**

### Changes to VM1 (Your Existing Cluster) — All Non-Destructive

```bash
# 1. Enable Cilium Cluster Mesh (1 command, zero downtime)
cilium clustermesh enable

# 2. Install MCS API CRDs (2 kubectl applies, no restarts)
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/mcs-api/master/config/crd/multicluster.x-k8s.io_serviceexports.yaml
kubectl apply -f https://raw.githubusercontent.com/kubernetes-sigs/mcs-api/master/config/crd/multicluster.x-k8s.io_serviceimports.yaml

# 3. Install OCM hub (new namespace, no impact on existing workloads)
clusteradm init --wait
```

### Changes to Helm Templates — 3 Files, 2 Lines Each

In `codeInspector/charts/apiServer/templates/rabbitmq.yaml` (the Service object):
```yaml
# Add only this — nothing else changes:
  annotations:
    service.cilium.io/global: "true"
    service.cilium.io/affinity: "local"
```

Same 2 lines added to the PostgreSQL service and Redis service templates.
**That is the only change to your Helm chart.** Application code: zero changes.

### What Does NOT Change

| What | Status |
|:---|:---|
| `consumer.py` and all Python code | ✅ Unchanged |
| `RABBITMQ_URL` env var | ✅ Unchanged on VM1 |
| Docker images | ✅ Unchanged |
| Helm chart structure | ✅ Unchanged (3 annotation lines added) |
| RabbitMQ queues and topology | ✅ Unchanged |
| PostgreSQL schema | ✅ Unchanged |
| JWT/Auth0 config | ✅ Unchanged |
| MetalLB (already installed) | ✅ Unchanged |
| Existing scan jobs in progress | ✅ Unaffected |

---

## Is Karmada Applicable Without Major Changes?

**Also yes — and Karmada has fewer moving parts than OCM for the control plane.**

Karmada is **CNI-agnostic** — it does not touch networking at all. It only manages
**which cluster runs which workload**. Cross-cluster networking is handled separately
(by Cilium Cluster Mesh, which you already have).

### What Karmada Needs

```bash
# 1. Install Karmada control plane (on VM1 or a separate VM)
helm repo add karmada-charts https://raw.githubusercontent.com/karmada-io/karmada/master/charts
helm install karmada karmada-charts/karmada \
  --namespace karmada-system \
  --create-namespace

# 2. Register VM1's RKE2 cluster as a member
kubectl karmada join vm1-cluster --cluster-kubeconfig=/etc/rancher/rke2/rke2.yaml

# 3. Register VM2 when you add it
kubectl karmada join vm2-cluster --cluster-kubeconfig=/path/to/vm2.yaml

# 4. Apply PropagationPolicy (new YAML file, doesn't change existing deployments)
kubectl apply -f sandbox-api-propagation.yaml
```

### What Karmada Does NOT Need

| What | Karmada Requirement |
|:---|:---|
| CNI change | ❌ Not needed — CNI-agnostic |
| Cilium Cluster Mesh | ❌ Not needed for scheduling — optional for cross-cluster networking |
| ArgoCD | ❌ Not needed — use your existing Helm deploy |
| Application code changes | ❌ None |
| Image rebuilds | ❌ None |
| Changes to consumer.py | ❌ None |
| Existing RabbitMQ setup | ❌ Unchanged |

> [!NOTE]
> Karmada handles **where** the workload runs. Cilium Cluster Mesh handles **how**
> services talk across clusters. You need both for full transparency — but Karmada
> alone gives you workload distribution immediately, and you add Cluster Mesh
> networking on top as a separate, independent step.

---

## Decision Guide: OCM (Handbook) vs Karmada

Both work with your current setup. Both require no application code changes.
Here is the honest trade-off:

| Criteria | OCM + Handbook | Karmada |
|:---|:---|:---|
| **Automation scripts available** | ✅ `multicluster_bootstrap.sh` automates everything | ❌ Manual steps only |
| **Control plane complexity** | Higher — OCM hub + ArgoCD addon + clusteradm CLI | Lower — single Karmada chart |
| **Placement policy syntax** | `ManifestWorkReplicaSet` + `Placement` (verbose) | `PropagationPolicy` (simpler YAML) |
| **GitOps** | ✅ ArgoCD built-in via addon | Bring your own (or add ArgoCD separately) |
| **Cross-cluster networking** | Cilium Cluster Mesh (already have it) | Cilium Cluster Mesh or Submariner (separate step) |
| **Requires Cilium** | For Cluster Mesh: yes. OCM itself: no | No — CNI-agnostic |
| **Works with your Helm chart** | ✅ Yes — YAML unchanged | ✅ Yes — YAML unchanged |
| **Time to first working multi-cluster** | ~1-2 hours (script does it) | ~30-60 min (simpler setup) |
| **Community support** | CNCF, Red Hat-backed | CNCF, Huawei-backed |
| **Learning curve** | Medium — new `clusteradm` CLI, OCM concepts | Low — uses standard `kubectl` |
| **Best for** | Full GitOps multi-cluster out of the box | Simple workload distribution first, add networking later |

---

## Recommended Path for Your Scenario

Given you have **one existing cluster**, **Cilium already running**, and want to move
to multi-cluster **without disrupting anything currently working**:

```mermaid
flowchart TD
    A["Current State: 1 RKE2 Cluster<br/>VM1: Cilium running<br/>Pod CIDR: 10.42.0.0/24<br/>Everything working"]

    A --> B{"Do you want ArgoCD<br/>for GitOps right now?"}

    B -- "Yes / Already planned" --> C["Use OCM Handbook<br/>Run multicluster_bootstrap.sh locally first<br/>Then adapt Phases 2-4 for RKE2<br/>Full stack: OCM + Cilium Mesh + ArgoCD"]

    B -- "No / Keep Helm for now" --> D["Use Karmada<br/>Simpler control plane<br/>PropagationPolicy for scheduling<br/>Add Cilium Cluster Mesh separately<br/>Keep existing Helm workflow"]

    C --> E["Both paths result in:<br/>Zero application code changes<br/>Same service names in consumer.py<br/>Workers find nearest RabbitMQ automatically<br/>New clusters join with 1 command"]

    D --> E
```

### If You Choose OCM (Handbook)

Start here (local test, zero risk to production):
```bash
git clone https://github.com/BerryBytes/k8s-multicluster-handbook.git
cd k8s-multicluster-handbook
./scripts/multicluster_bootstrap.sh
# Test the full stack locally with KinD clusters
# When satisfied, apply Phases 2-4 to your OVH RKE2 clusters
```

### If You Choose Karmada

Start here (directly on your existing cluster):
```bash
helm repo add karmada-charts https://raw.githubusercontent.com/karmada-io/karmada/master/charts
helm install karmada karmada-charts/karmada \
  --namespace karmada-system --create-namespace
kubectl karmada join current-cluster \
  --cluster-kubeconfig=/etc/rancher/rke2/rke2.yaml \
  --cluster-context default
```

Then apply your first `PropagationPolicy` — your `sandbox-api` immediately becomes
multi-cluster aware without any other changes.

### The One Thing Both Need

After either is set up, both still need **Cilium Cluster Mesh** enabled for
cross-cluster service transparency. Since you already run Cilium, this is:

```bash
# Enable on VM1 (your existing cluster)
cilium clustermesh enable
# Enable on VM2 (when you add it)
KUBECONFIG=/path/to/vm2.yaml cilium clustermesh enable
# Connect them
cilium clustermesh connect --destination-context vm2-context
```

**Three commands. Zero application changes. Done.**
