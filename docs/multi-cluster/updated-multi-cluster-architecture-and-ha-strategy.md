# 01-Sandbox: Simplified Hub and Spoke Architecture & High Availability Guide

> **Document Status:** High-Level Technical Specification & Visual Architecture Blueprint
> **Target Application:** 01-Sandbox High-Concurrency Code Execution Platform
> **Environment:** Hub Cluster (`bb-mp-plat-03`) | KinD Spoke East (`kind-east`) | KinD Spoke West (`kind-west`)
> **Design Philosophy:** Standard, universally compatible Mermaid syntax rendering seamlessly across all IDE Markdown preview engines.

---

## Executive Summary

The **01-Sandbox** platform uses a **Hub-and-Spoke Multi-Cluster Architecture**:
* **Hub Cluster (`bb-mp-plat-03`):** Hosts all stateful core services (`sandbox-api`, RabbitMQ queue, PostgreSQL result database) and fleet scheduling (Open Cluster Management).
* **Service Discovery (MCS API KEP-1645):** Uses standard Kubernetes `ServiceExport` and `ServiceImport` CRDs to expose Hub services across clusters under the `.svc.clusterset.local` DNS domain.
* **Spoke Worker Clusters (`kind-east`, `kind-west`):** Pure stateless execution nodes running hardware-isolated microVM sandboxes (Kata Containers / gVisor).
* **Cross-Cluster Data Mesh:** **Cilium ClusterMesh (eBPF + WireGuard)** delivers kernel-level encrypted packet transport with sub-0.2ms latency.

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
        OCM["Step 3: OCM Placement Engine<br/>(Evaluates Free CPU/RAM Telemetry)"]

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
        DB[("Step 6: Hub PostgreSQL Database<br/>(Stores Scan Reports & Findings)")]
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
└─────────────────────────────────────────────────────────────────────────────────────────┘
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
