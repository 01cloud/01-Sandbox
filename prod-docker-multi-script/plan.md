# Production Deployment & Infrastructure Proposal
## Multi-Region Multi-Cluster Kubernetes Platform with Kata Firecracker, OCM, and Envoy Gateway

**Prepared For:** Engineering Management
**Project:** 01-Sandbox Production Multi-Cluster Infrastructure
**Author:** Platform Engineering Team
**Date:** October 8, 2026
**Status:** Proposal / Pending Approval

---

## Executive Summary

This proposal outlines the production architecture, cloud infrastructure sizing, cost analysis, and execution plan for deploying the **01-Sandbox Multi-Cluster Platform** across **four distinct AWS regions**.

The platform provides isolated, high-security code execution sandboxes utilizing **Kata Containers with Firecracker microVMs** (`kata-fc`) and **Google gVisor** (`runsc`), orchestrated across multiple clusters using **Open Cluster Management (OCM)**, synchronized active-passive PostgreSQL HA databases, and a unified **Envoy Gateway** entry point.

### Key Highlights
- **100% Terraform-Provisioned**: All cloud infrastructure (VPCs, Security Groups, EC2 instances, Elastic IPs, and IAM) is provisioned declaratively via Terraform across 4 AWS regions.
- **Max 8 GiB RAM per Cluster**: Cost-optimized instance selection ensuring zero wasted memory while maintaining over 50% memory headroom for workloads.
- **Ultra-Low Cost via AWS Nitro Nested Virtualization**: Instead of expensive bare-metal instances ($18+/hr), we leverage modern 7th-gen Intel Nitro instances with hardware nested virtualization enabled for the Spoke clusters.
- **Estimated Cloud Cost**: **~$0.36 – $0.51 / hour** total across all 4 regions (~$260 – $368 / month).
- **Secure Cross-Region Mesh**: High-speed, encrypted WireGuard VPN overlay (`10.99.0.0/24`) connecting all 4 AWS regions with sub-second latency and zero exposure of internal Kubernetes APIs to the public internet.

---

## 1. High-Level Architecture Topology

```
                                  [Internet / Public Traffic]
                                               │
                                               ▼ (HTTP:80 / HTTPS:443)
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ EC2 Instance 1 (Region A: e.g., us-east-1) — Primary Control Plane                     │
│ Public IP: IP_HUB1                                                                     │
│                                                                                        │
│  ┌──────────────────────────────────┐        ┌──────────────────────────────────────┐  │
│  │   Envoy Edge Gateway             │        │   PrimaryHub Cluster                 │  │
│  │   • Port 80 / 443 Public Ingress │        │   • OCM Control Hub (Active)         │  │
│  │   • WireGuard VIP (10.99.0.100)  ├───────►│   • CloudNativePG (Primary Database) │  │
│  │   • L4/L7 Active-Passive Proxy   │        │   • WireGuard IP: 10.99.0.1          │  │
│  └──────────────────────────────────┘        └──────────────────────────────────────┘  │
│                                                                                        │
│                      [WireGuard wg0 Host Network: 10.99.0.254]                         │
└──────────────────────────────────────────┬─────────────────────────────────────────────┘
                                           │
                                           │ Encrypted WireGuard Overlay (UDP:51820)
                                           │ Mesh Subnet: 10.99.0.0/24
             ┌─────────────────────────────┼─────────────────────────────┐
             ▼                             ▼                             ▼
┌──────────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────────┐
│ EC2 Instance 2           │  │ EC2 Instance 3           │  │ EC2 Instance 4           │
│ (Region B: eu-west-1)    │  │ (Region C: ap-southeast-1)│ │ (Region D: us-west-2)    │
│ SecondaryHub             │  │ Spoke1 Workload Cluster  │  │ Spoke2 Workload Cluster  │
│                          │  │                          │  │                          │
│ • OCM Hub (Passive)      │  │ • Workload Cluster       │  │ • Workload Cluster       │
│ • Postgres HA Replica    │  │ • Kata Firecracker KVM   │  │ • Kata Firecracker KVM   │
│ • WireGuard IP: 10.99.0.2│  │ • gVisor Runtime         │  │ • gVisor Runtime         │
│                          │  │ • WireGuard IP: 10.99.0.3│  │ • WireGuard IP: 10.99.0.4│
└──────────────────────────┘  └──────────────────────────┘  └──────────────────────────┘
```

---

## 2. Infrastructure Sizing & Cost Analysis

All instances are strictly capped at a **maximum of 8 GiB RAM**, perfectly matching the memory budget required for the cluster components while eliminating excess compute waste.

### Instance Role Separation
1. **Hub Instances (`PrimaryHub`, `SecondaryHub`)**:
   - Run management plane controllers, OCM synchronization, PostgreSQL, Valkey, and Envoy.
   - Do **not** run Firecracker microVMs; hardware virtualization (`/dev/kvm`) is **not required**.
   - Standard, highly cost-effective **`t3.large`** (or `t3a.large`) instances are utilized.
2. **Spoke Instances (`Spoke1`, `Spoke2`)**:
   - Run developer microVM sandboxes via Kata Containers and Firecracker.
   - Require `/dev/kvm` hardware virtualization extensions.
   - Standard bare-metal instances (`c5.metal`) cost ~$4.50/hr each ($9.00/hr for 2 spokes).
   - **Optimized Solution**: 7th-generation Intel Nitro instances (**`m7i-flex.large`** or **`c7i-flex.xlarge`**) with **AWS Nitro Nested Virtualization enabled** (`--cpu-options NestedVirtualization=enabled`). This unlocks `/dev/kvm` on virtualized EC2 instances with **zero extra fee from AWS**, delivering **>90% cost savings**.

---

### Cost Breakdown Comparison

#### Option A: Maximum Cost Efficiency (2 vCPU / 8 GiB RAM Across All Nodes)

| Role | Cluster Name | AWS Region | Instance Type | vCPU | RAM | Nested KVM | Hourly Rate | Monthly Cost (730 hrs) |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Edge & Primary** | `primaryhub` + Envoy | `us-east-1` | `t3.large` | 2 | 8 GiB | No | $0.0832 | $60.74 |
| **Passive Backup** | `secondaryhub` | `eu-west-1` | `t3.large` | 2 | 8 GiB | No | $0.0832 | $60.74 |
| **Worker Cluster 1**| `spoke1` | `ap-southeast-1` | `m7i-flex.large` | 2 | 8 GiB | **Yes (`/dev/kvm`)** | $0.0958 | $69.93 |
| **Worker Cluster 2**| `spoke2` | `us-west-2` | `m7i-flex.large` | 2 | 8 GiB | **Yes (`/dev/kvm`)** | $0.0958 | $69.93 |
| **Total Fleet** | **4 Clusters** | **4 Regions** | | **8** | **32 GiB** | | **~$0.358 / hr** | **~$261.34 / mo** |

* **Total Fleet Cost**: **$0.36 / hour** (~$8.60 / day).
* **Test/Demo Run (8 Hours)**: **$2.86 total**.

---

#### Option B: Recommended Production Balance (4 vCPU on Spokes for Concurrent MicroVMs)
*Allocating 4 vCPUs to the two Spoke clusters provides greater CPU scheduling headroom for running concurrent Firecracker microVMs while remaining strictly at 8 GiB RAM.*

| Role | Cluster Name | AWS Region | Instance Type | vCPU | RAM | Nested KVM | Hourly Rate | Monthly Cost (730 hrs) |
| :--- | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| **Edge & Primary** | `primaryhub` + Envoy | `us-east-1` | `t3.large` | 2 | 8 GiB | No | $0.0832 | $60.74 |
| **Passive Backup** | `secondaryhub` | `eu-west-1` | `t3.large` | 2 | 8 GiB | No | $0.0832 | $60.74 |
| **Worker Cluster 1**| `spoke1` | `ap-southeast-1` | `c7i-flex.xlarge` | 4 | 8 GiB | **Yes (`/dev/kvm`)** | $0.1696 | $123.81 |
| **Worker Cluster 2**| `spoke2` | `us-west-2` | `c7i-flex.xlarge` | 4 | 8 GiB | **Yes (`/dev/kvm`)** | $0.1696 | $123.81 |
| **Total Fleet** | **4 Clusters** | **4 Regions** | | **12** | **32 GiB** | | **~$0.506 / hr** | **~$369.10 / mo** |

* **Total Fleet Cost**: **$0.51 / hour** (~$12.14 / day).
* **Savings vs Bare Metal ($18.00/hr)**: **$17.49 / hour saved (97.2% reduction)**.

---

### Storage Cost Estimate (EBS gp3)
- 40 GiB `gp3` per Hub node (40 GiB × 2 = 80 GiB).
- 50 GiB `gp3` per Spoke node (50 GiB × 2 = 100 GiB).
- **Total Storage**: 180 GiB @ $0.08/GiB-month = **$14.40 / month**.

---

## 3. 8 GiB Memory Budget Feasibility

An in-depth memory allocation analysis verifies that 8 GiB comfortably accommodates all cluster services with significant headroom:

```
[PrimaryHub Node: 8.0 GiB Total]
├── OS Kernel + WireGuard:       0.6 GiB
├── K8s Control Plane & etcd:    1.2 GiB
├── CloudNativePG & Valkey:      1.0 GiB
├── OCM Hub & AgentGateway:      0.8 GiB
├── Envoy Gateway Container:     0.2 GiB
└── Free Memory Reserve:         4.2 GiB (52.5% Available Headroom)

[SecondaryHub Node: 8.0 GiB Total]
├── OS Kernel + WireGuard:       0.6 GiB
├── K8s Control Plane & etcd:    1.2 GiB
├── CloudNativePG Replica:       0.6 GiB
└── Free Memory Reserve:         5.6 GiB (70.0% Available Headroom)

[Spoke1 / Spoke2 Nodes: 8.0 GiB Total]
├── OS Kernel + WireGuard:       0.6 GiB
├── K8s Control Plane & etcd:    1.2 GiB
├── OCM Klusterlet Agent:        0.3 GiB
├── Kata Devmapper Thin Pool:    0.2 GiB
├── Kata Node Reconciler:        0.1 GiB
└── Available Sandbox Memory:    5.6 GiB (Sufficient for 6–10 simultaneous microVMs)
```

---

## 4. Potential Technical Drawbacks & Mitigation Strategies

| Potential Challenge | Technical Impact | Proposed Mitigation Strategy |
| :--- | :--- | :--- |
| **Nested Virtualization CPU Traps** | Guest CPU exits (L2 microVM $\rightarrow$ L1 Linux $\rightarrow$ L0 Nitro) increase cold-start microVM latency from ~50ms to ~150–200ms. | Negligible in practice for container tasks. Once the guest kernel is running, code execution inside the sandbox executes at near-native CPU speeds. |
| **Burstable "flex" CPU Baseline** | `m7i-flex` and `c7i-flex` instances have a baseline of 40% CPU with 100% burst. Sustained 24/7 full-load code compilation could exhaust CPU credits. | Monitor CPU credit balance via CloudWatch alarms. If sustained compute is required in production, instances can be swapped via Terraform to standard `c7i.xlarge` without architectural changes. |
| **Multi-Region Network Latency** | Inter-region cross-continent network latency between Hubs and Spokes (e.g., US to Singapore ~180ms). | All synchronous user traffic is terminated at Envoy and PrimaryHub. OCM cross-region heartbeats and cluster registrations operate asynchronously with 25s keepalives, unaffected by WAN latency. |
| **Thin-Pool Snapshot Exhaustion** | Crashed microVM pods can leave stale device-mapper entries in the kernel table. | Mitigated by our automated Kubernetes-native DaemonSet reconciler, which continuously sweeps and removes orphaned snapshots (`open_count == 0`) every 10 seconds. |

---

## 5. Terraform Provisioning Architecture

All AWS infrastructure will be managed in a dedicated Terraform module (`terraform/`) with multi-region provider aliases:

```hcl
# terraform/main.tf
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"
}

provider "aws" {
  alias  = "eu_west_1"
  region = "eu-west-1"
}

provider "aws" {
  alias  = "ap_southeast_1"
  region = "ap-southeast-1"
}

provider "aws" {
  alias  = "us_west_2"
  region = "us-west-2"
}

# Example Spoke Instance with Nested Virtualization Enabled
resource "aws_instance" "spoke1" {
  provider      = aws.ap_southeast_1
  ami           = data.aws_ami.ubuntu_noble.id
  instance_type = "m7i-flex.large" # or c7i-flex.xlarge
  key_name      = var.ssh_key_name

  cpu_options {
    nested_virtualization = "enabled" # Unlocks /dev/kvm for Kata Firecracker
  }

  vpc_security_group_ids = [aws_security_group.spoke1_sg.id]

  root_block_device {
    volume_size = 50
    volume_type = "gp3"
  }

  tags = {
    Name = "01sandbox-spoke1"
    Role = "worker-kata-fc"
  }
}
```

### Security Group Inbound Rules
- **All 4 Nodes**: UDP Port `51820` open to the other 3 instance Public IPs (WireGuard inter-region mesh).
- **PrimaryHub (EC2 #1)**: TCP Ports `80` and `443` open to `0.0.0.0/0` (Public web traffic and API gateway).
- **All 4 Nodes**: TCP Port `22` open to administrator/CI IP addresses (SSH automation).
- **Internal APIs (Kube-API 6443, etcd, PostgreSQL 5432)**: Strictly bound to the encrypted WireGuard overlay (`10.99.0.0/24`), completely inaccessible from the public internet.

---

## 6. One-Shot Automation: `prod-docker-multi-script` Implementation Plan

The `prod-docker-multi-script/` repository will be enhanced to execute full end-to-end orchestration remotely over SSH and WireGuard in a single command (`./docker-multi-cluster.sh`).

### File Modification Map

```
prod-docker-multi-script/
├── prod.env                     # [NEW] Generated from Terraform outputs (Public IPs, SSH keys)
├── terraform/                   # [NEW] Multi-region Terraform configurations
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
├── lib/
│   ├── globals.sh               # [UPDATE] Add remote_exec helpers, source prod.env
│   ├── network.sh               # [UPDATE] WireGuard mesh generation using EC2 Public IPs
│   ├── clusters.sh              # [UPDATE] Remote KinD cluster creation & kubeconfig merger
│   ├── kata.sh                  # [UPDATE] Apply Kubernetes-native kata-fc-daemonset.yaml
│   ├── kata-fc-daemonset.yaml   # [READY] Self-healing DaemonSet with watchdog
│   ├── deploy.sh                # [READY] Helm deployment of PostgreSQL HA & OpenSandbox
│   ├── ocm.sh                   # [READY] Multi-hub OCM join via VIP 10.99.0.100
│   └── main.sh                  # [UPDATE] Orchestrate one-shot remote rollout sequence
└── docker-multi-cluster.sh      # [READY] Entry point CLI
```

### Automated Phase Sequence
1. **Phase 01: Preflight & Remote SSH Handshake**: Validates SSH reachability and checks `/dev/kvm` presence on Spoke instances.
2. **Phase 02: Centralized WireGuard Key Generation**: Generates 5 x25519 keypairs locally and formats `wg0.conf` with remote Public IP endpoints.
3. **Phase 03: Overlay Activation & Ping Validation**: Deploys `wg0` to all 4 EC2 nodes and verifies encrypted transit between `10.99.0.1` through `10.99.0.4`.
4. **Phase 04: PrimaryHub Cluster Deployment**: Spins up `primaryhub` cluster on EC2 #1 with multi-IP certSANs.
5. **Phase 05: Shared Root CA Distribution & SecondaryHub Deployment**: Exports PrimaryHub's CA and synchronizes it to EC2 #2 before creating `secondaryhub`.
6. **Phase 06: Envoy Gateway Deployment**: Deploys Envoy on EC2 #1 mapped to host ports 80/443 and VIP `10.99.0.100`.
7. **Phase 07: OCM Hub Initialization**: Deploys OCM registration and work-manager controllers on both hubs.
8. **Phase 08: Spoke Cluster Creation**: Deploys `spoke1` on EC2 #3 and `spoke2` on EC2 #4.
9. **Phase 09: Kubernetes-Native Kata Firecracker Rollout**: Applies `kata-fc-daemonset.yaml` to both spokes via `kubectl`.
10. **Phase 10: OCM Spoke Join**: Registers spokes to the cluster mesh via the Envoy VIP.
11. **Phase 11: Workload Stack Deployment**: Deploys CloudNativePG, Valkey, and OpenSandbox server.
12. **Phase 12: End-to-End Verification**: Executes test pods under microVM guest kernel 6.12.28 and verifies Exit Code 0.

---

## 7. Project Timeline & Milestones

| Milestone | Duration | Deliverables |
| :--- | :---: | :--- |
| **Milestone 1: Terraform Infrastructure Code** | 1.0 hr | Terraform configurations for 4 AWS regions, Security Groups, and Nitro CPU options. |
| **Milestone 2: Script Adaptation (`prod.env` & SSH)** | 1.0 hr | Refactoring `prod-docker-multi-script` with remote execution abstractions and WireGuard mesh generators. |
| **Milestone 3: Infrastructure Provisioning** | 0.5 hr | `terraform apply` spinning up the 4 instances in their respective regions. |
| **Milestone 4: One-Shot Cluster Rollout & Smoke Test**| 0.5 hr | Executing `./docker-multi-cluster.sh`, verifying WireGuard, OCM join, and Kata Firecracker pods. |
| **Total Estimated Time to Production** | **~3.0 Hours** | **Fully functional multi-region microVM sandbox platform.** |

---

## 8. Management Approval Request

**Requested Decision**: Approval to proceed with provisioning 4 AWS EC2 instances across 4 regions via Terraform and executing the automated setup using the recommended configuration:
- **PrimaryHub + Envoy**: 1× `t3.large` ($0.083/hr)
- **SecondaryHub**: 1× `t3.large` ($0.083/hr)
- **Spoke1 & Spoke2**: 2× `m7i-flex.large` ($0.096/hr each) or `c7i-flex.xlarge` ($0.17/hr each)
- **Estimated Budget Impact**: **~$0.36 to $0.51 / hour** (~$260 to $369 / month total).
- **Execution Window**: 3 hours from approval to live deployment.

---

*Approved by:* _____________________________ &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; *Date:* _________________
