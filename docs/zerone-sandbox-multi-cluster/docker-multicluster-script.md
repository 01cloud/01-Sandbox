# Technical Working Principle: Pure Docker Multi-Cluster Platform (`docker-multi-cluster.sh`)

## 1. Executive Summary & Architectural Paradigm Shift

The `docker-multi-cluster.sh` script automates the complete lifecycle of a production-grade, multi-cluster Kubernetes platform with High Availability (HA) active-passive hub failover, cross-cluster WireGuard mesh networking, continuous stateful replication (PostgreSQL WAL streaming + Valkey cache replication), and Open Cluster Management (OCM) orchestration.

### The Problem with Virtual Machines (VMs)
Previously, testing and validating multi-cluster HA required 4 to 5 full-fledged Linux Virtual Machines running under KVM/QEMU:
- Required **~32 GB of Host RAM** and **16 vCPUs** just to idle.
- Required nested virtualization (`/dev/kvm`), blocking execution inside standard CI/CD runners, Docker-in-Docker (DinD), cloud compute instances, and developer laptops.
- Slow cold provisioning times (15–25 minutes).
- Complex OS-level networking bridges (`virbr0`, tap interfaces, DHCP leases).

### The Pure Docker KinD Paradigm
`docker-multi-cluster.sh` replaces all hypervisors with **containerized Kubernetes nodes (Kubernetes in Docker - KinD)** while maintaining strict multi-cluster isolation:
- **Zero VM Overhead**: Entire 4-cluster mesh runs inside 5 lightweight Docker containers with standard kernel cgroups.
- **Resource Footprint**: Drops from ~32 GB RAM down to **~3.5–4.5 GB RAM total**.
- **Cold Provisioning**: From scratch to 100% Ready in **< 4 minutes**.
- **Portability**: Runs on any modern Linux host, WSL2, or CI/CD runner without hardware virtualization.

---

## 2. Complete Topology & IP Addressing Matrix

The platform provisions five autonomous containers interconnected by a dual-layer network: a physical Docker bridge **underlay** (`01sandbox-transit`) and an encrypted **WireGuard overlay** (`10.99.0.0/24`).

```
                    ┌────────────────────────────────────────────────────────┐
                    │                      HOST MACHINE                      │
                    │   Client Traffic / Browser: http://172.30.0.100:80     │
                    │   MetalLB Ingress VIP:      http://172.18.255.200:80   │
                    └──────────────────────────┬─────────────────────────────┘
                                               │
               ┌───────────────────────────────┴───────────────────────────────┐
               │         Docker Transit Network (01sandbox-transit)            │
               │                      172.30.0.0/24                            │
               └───────┬──────────────┬──────────────┬──────────────┬──────────┘
                       │              │              │              │
         ┌─────────────▼────┐   ┌─────▼────────┐   ┌─▼────────────┐ ┌▼────────────┐
         │  envoy-gateway   │   │ primaryhub   │   │ secondaryhub │ │ spoke1 / 2  │
         │  172.30.0.100    │   │ 172.30.0.20  │   │ 172.30.0.21  │ │ 172.30.0.30/│
         └─────────────┬────┘   └─────┬────────┘   └─┬────────────┘ └┬────────────┘
                       │              │              │               │
  ═════════════════════╪══════════════╪══════════════╪═══════════════╪═════════════════
  WireGuard Mesh (wg0) │              │              │               │
  Overlay:             │ VIP          │ Active Hub   │ Standby Hub   │ Managed Spokes
  10.99.0.0/24         ▼              ▼              ▼               ▼
                 [10.99.0.100]  [10.99.0.1]    [10.99.0.2]     [10.99.0.3 / .4]
  ═════════════════════════════════════════════════════════════════════════════════════
```

### Detailed Network Allocation Table

| Entity | Role | KinD Pod CIDR | KinD Svc CIDR | Transit Underlay IP | WireGuard Overlay IP | Key Exposed Ports |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`envoy-gateway`** | Active-Passive HA VIP | N/A | N/A | `172.30.0.100` | **`10.99.0.100` (VIP)** | `80` (HTTP), `6443` (Kube-API) |
| **`primaryhub`** | Active Hub (Master RW) | `10.244.0.0/16` | `10.96.0.0/16` | `172.30.0.20` | `10.99.0.1` | `6443`, `30080` (API), `30432` (PG), `30379` (Valkey) |
| **`secondaryhub`**| Standby Hub (Replica RO)| `10.245.0.0/16` | `10.97.0.0/16` | `172.30.0.21` | `10.99.0.2` | `6443`, `30080` (API), `30432` (PG), `30379` (Valkey) |
| **`spoke1`** | Managed Workload Cluster | `10.246.0.0/16` | `10.98.0.0/16` | `172.30.0.30` | `10.99.0.3` | `6443`, `51820` (WireGuard) |
| **`spoke2`** | Managed Workload Cluster | `10.247.0.0/16` | `10.95.0.0/16` | `172.30.0.31` | `10.99.0.4` | `6443`, `51820` (WireGuard) |

> **CIDR Isolation Guarantee**: KinD clusters defaults to `10.244.0.0/16` and `10.96.0.0/16`. If multiple clusters run on default CIDRs, cross-cluster pod routing and service discovery experience catastrophic IP collisions. `docker-multi-cluster.sh` injects non-overlapping subnets at cluster initialization via `kubeadmConfigPatches`.

---

## 3. Cryptographic Identity & Root CA Sharing

### The Problem
When Spoke clusters register to an OCM Hub, the spoke's `klusterlet` agent verifies the Hub's server certificate against the Hub's Root Certificate Authority (`ca.crt`). If `primaryhub` fails and traffic shifts to `secondaryhub`, spokes immediately reject connections with TLS verification errors (`x509: certificate signed by unknown authority`) unless both hubs share an identical cryptographic identity.

### The Solution: Shared Root CA & ServiceAccount Keys
1. **Pre-Generation**: `docker-multi-cluster.sh` generates a cryptographically secure 2048-bit RSA Root CA keypair (`ca.key`, `ca.crt`) and ServiceAccount signing keypair (`sa.key`, `sa.pub`) on the host disk before cluster creation:
   ```bash
   openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 3650
   openssl genrsa -out sa.key 2048
   openssl rsa -in sa.key -pubout -out sa.pub
   ```
2. **Kubeadm Injection via Bind Mounts**: Both `primaryhub` and `secondaryhub` KinD configs bind-mount these exact files directly into `/etc/kubernetes/pki/`:
   ```yaml
   extraMounts:
   - hostPath: /path/to/.sandbox-state/pki/ca.crt
     containerPath: /etc/kubernetes/pki/ca.crt
   - hostPath: /path/to/.sandbox-state/pki/ca.key
     containerPath: /etc/kubernetes/pki/ca.key
   - hostPath: /path/to/.sandbox-state/pki/sa.key
     containerPath: /etc/kubernetes/pki/sa.key
   - hostPath: /path/to/.sandbox-state/pki/sa.pub
     containerPath: /etc/kubernetes/pki/sa.pub
   ```
3. **Dynamic SAN Injection**: Both API servers are initialized with SANs covering all routing endpoints:
   ```yaml
   kubeadmConfigPatches:
   - |
     apiVersion: kubeadm.k8s.io/v1beta3
     kind: ClusterConfiguration
     apiServer:
       certSANs:
       - "10.99.0.100"
       - "10.99.0.1"
       - "10.99.0.2"
       - "172.30.0.100"
       - "envoy-gateway"
   ```
**Result**: Both Hubs share the exact same SHA256 CA fingerprint. When failover occurs, spoke klusterlets connect to `secondaryhub` over `https://10.99.0.100:6443` without any certificate errors or reconnection drops.

---

## 4. Persistence & Computer Reboot Resilience Architecture

### The Linux `/tmp` Ephemeral RAM-Disk Trap
In earlier implementations, certificates and Envoy configs were saved to `/tmp/01sandbox-*`.
On modern Linux (systemd-based distributions like Ubuntu/Debian), `/tmp` is mounted on **`tmpfs` (RAM)**. When the host computer restarts, `/tmp` is wiped clean.
Upon host reboot:
1. Docker daemon attempted to start the `secondaryhub` container.
2. The bind-mount `/tmp/01sandbox-pki/ca.crt` was missing on the host. Docker created an empty *directory* named `ca.crt`.
3. The container's `runc` runtime crashed trying to mount a directory over a file, producing `Exit Code 127` (`not a directory`).
4. Envoy lacked `--restart unless-stopped`, so it never booted.

### Architectural Fix in `docker-multi-cluster.sh`
1. **Persistent On-Disk State**: All state is placed in a repository-local directory on physical disk:
   ```bash
   STATE_DIR="${ROOT_DIR}/.sandbox-state"
   PKI_DIR="${STATE_DIR}/pki"
   WG_DIR="${STATE_DIR}/wg"
   ENVOY_DIR="${STATE_DIR}/envoy"
   SEC_DIR="${STATE_DIR}/sec"
   ```
   *Safely added to `.gitignore` to prevent committing secrets.*
2. **Restart Policies**:
   - KinD nodes run with `--restart=always`.
   - Envoy Gateway runs with `--restart unless-stopped`.
3. **Pre-Baked Offline Envoy Image (`01sandbox-envoy:v1`)**:
   - Standard Envoy images lack WireGuard and routing tools. Running `apt-get install` inside the container on boot takes 2–3 minutes and fails if DNS/internet is not ready immediately.
   - `docker-multi-cluster.sh` pre-bakes `wireguard-tools`, `iproute2`, and `iptables` into a local image: `01sandbox-envoy:v1`.
   - On host reboot, Envoy starts and establishes `wg0` in **< 1 second** completely offline.
4. **Systemd Inside KinD**:
   - `wg-quick@wg0.service` is enabled via `systemctl enable wg-quick@wg0` inside the KinD nodes (which run systemd as PID 1).
   - On reboot, systemd automatically restores the `10.99.0.0/24` WireGuard interface before kubelet starts pods.

---

## 5. OCM Registration via High-Availability Gateway VIP

Spoke clusters **never register to raw Hub IP addresses**. Instead, they register to the Envoy Gateway Virtual IP (`https://10.99.0.100:6443`).

```
┌─────────────────────────────────────────────────────────────┐
│                       SPOKE CLUSTER                         │
│   klusterlet agent configured with:                         │
│   --hub-apiserver https://10.99.0.100:6443                  │
└──────────────────────────────┬──────────────────────────────┘
                               │ Encrypted WireGuard Tunnel
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                    ENVOY GATEWAY VIP                        │
│                   (10.99.0.100:6443)                        │
└──────────────┬──────────────────────────────┬───────────────┘
               │ Priority 0 (Active)          │ Priority 1 (Failover)
               ▼                              ▼
┌──────────────────────────────┐┌─────────────────────────────┐
│    PRIMARYHUB (10.99.0.1)    ││   SECONDARYHUB (10.99.0.2)  │
│  - Accepts CSRs              ││  - Replicated Hub RBAC      │
│  - Receives Status Updates   ││  - Standby Auto-Acceptor    │
└──────────────────────────────┘└─────────────────────────────┘
```

### Registration Workflow in `docker-multi-cluster.sh`
1. **Token Acquisition**: Fetches registration bootstrap token from `primaryhub`:
   ```bash
   HUB_TOKEN=$(clusteradm get token --context kind-primaryhub | cut -d'=' -f2)
   ```
2. **Non-Blocking Join**: Spoke executes `clusteradm join` against `https://10.99.0.100:6443`.
3. **Automated Approval**: PrimaryHub detects the pending Certificate Signing Request (CSR) and executes `clusteradm accept --clusters <spoke>`.
4. **Metadata & Labels**: Injects hardware capability and routing metadata:
   - `wireguard-ip=10.99.0.3` / `10.99.0.4`
   - `sandbox-workload-capable=true`
   - `runtime.gvisor=true`
   - `runtime.kata=true`
5. **SecondaryHub RBAC Pre-Sync**: The script synchronizes the `ManagedCluster` CRD, spoke namespace, `ClusterRole`, and `RoleBinding` to `secondaryhub`. When failover occurs, `secondaryhub` already possesses all authorization objects to communicate with the spokes instantly.

---

## 6. Stateful Data Replication Layer (Continuous HA)

### 1. CloudNativePG PostgreSQL Replication
- **PrimaryHub (`postgresql-primary`)**: Configured as Master Read-Write (`instances: 1`, `wal_level: logical`, `max_wal_senders: 10`). Exposed across the mesh via Kubernetes NodePort **`30432`**.
- **SecondaryHub (`postgresql-secondary`)**: Configured as Standby Replica (`spec.replica.enabled: true`, `source: postgresql-primary`). Connects to `10.99.0.1:30432`.
- **Physical WAL Streaming**: WAL records are streamed continuously over WireGuard.
- **Verification Metrics**:
  - Primary Query: `SELECT client_addr, state, sync_state FROM pg_stat_replication;` → `streaming`, `async`.
  - Secondary Query: `SELECT status, sender_host, sender_port FROM pg_stat_wal_receiver;` → `streaming`, `10.99.0.1:30432`.

### 2. Valkey In-Memory Cache Replication
- **PrimaryHub**: Standalone Valkey instance listening on NodePort **`30379`**.
- **SecondaryHub**: Configured with `REPLICAOF 10.99.0.1 30379`.
- **Verification Metric**:
  - `valkey-cli info replication` → `role:slave`, `master_host:10.99.0.1`, `master_link_status:up`.

---

## 7. Kubernetes-Native Automated Failover & Split-Brain Safe Failback

The active-passive failover and failback lifecycle is managed by an in-cluster Kubernetes controller deployed on `secondaryhub`: **`ocm-failover-controller`**.

```
                        ┌──────────────────────────────┐
                        │   PrimaryHub Health Probe    │
                        │    https://10.99.0.1:6443    │
                        │    10.99.0.1:30432 (PG)      │
                        └──────────────┬───────────────┘
                                       │
                    ┌──────────────────┴──────────────────┐
                    ▼                                     ▼
        [Primary Unhealthy: 2 Probes]         [Primary Healthy & PG Up]
                    │                                     │
                    ▼                                     ▼
      ┌───────────────────────────┐         ┌───────────────────────────┐
      │     AUTOMATED FAILOVER    │         │     AUTOMATED FAILBACK    │
      ├───────────────────────────┤         ├───────────────────────────┤
      │ 1. Patch ManagedClusters: │         │ 1. Release spokes back to │
      │    hubAcceptsClient=true  │         │    Primary (untaint).     │
      │ 2. Promote Secondary PG   │         │ 2. Run Job 'delta-sync':  │
      │    to standalone RW.      │         │    copy outage mutations. │
      │ 3. Promote Valkey cache   │         │ 3. Re-clone Secondary PG  │
      │    to master.             │         │    as Standby on Timeline1│
      │ 4. Envoy shifts traffic   │         │ 4. Re-attach Valkey slave │
      │    to 10.99.0.2.          │         │    REPLICAOF 10.99.0.1.   │
      └───────────────────────────┘         └───────────────────────────┘
```

### Preventing Split-Brain on Failback
When `primaryhub` recovers after an outage:
1. `ocm-failover-controller` does **not** blindly demote secondary. It first verifies that `primaryhub` Kube-API is up AND that primary PostgreSQL is accepting connections on `10.99.0.1:30432`.
2. **Declarative Delta Sync Job (`failback-delta-sync`)**: A Kubernetes batch Job runs to extract any database records created on `secondaryhub` during the outage and upserts them into `primaryhub`.
3. **Timeline Parity Re-clone**: Because PostgreSQL forks a new Timeline ID (Timeline 2) upon promotion, it cannot rejoin Timeline 1 without re-cloning. The controller deletes `postgresql-secondary` and re-applies the standby manifest, cleanly re-attaching to PrimaryHub's Timeline 1 WAL stream.
4. **Valkey Reset**: Re-attaches Valkey slave to `10.99.0.1:30379`.
5. **Zero Split-Brain**: Guaranteed data consistency with zero divergent timelines.

---

## 8. Kubernetes-Native Ingress & API Access Architecture

### Eliminating Custom `iptables` Hacks
In container networks, engineers often resort to manual host `iptables -t nat PREROUTING ... DNAT` rules to forward traffic from overlay networks into pods. These rules are fragile, disappear on reboot, and conflict with Kubernetes CNI plugins.

`docker-multi-cluster.sh` uses **100% Kubernetes-native routing**:
1. `sandbox-api-service` exposes a fixed Kubernetes **NodePort: `30080`**.
2. Kubernetes `kube-proxy` natively binds `*:30080` on all node interfaces (`eth0`, `eth1`, and WireGuard `wg0`).
3. Envoy Gateway listens on standard HTTP port **`80`** and proxies upstream traffic directly to:
   - Primary: `10.99.0.1:30080` (Priority 0)
   - Secondary: `10.99.0.2:30080` (Priority 1)
4. MetalLB Layer 2 LoadBalancer allocates external VIP **`172.18.255.200:80`** to `agentgateway-proxy`.

### Access Cheat Sheet

| Access Location | Target URL | Protocol / Routing Path |
| :--- | :--- | :--- |
| **Host Browser / Curl** | `http://172.30.0.100` | Hits Envoy Container on transit network → proxies to active Hub NodePort `30080`. |
| **Host MetalLB VIP** | `http://172.18.255.200` | Hits MetalLB LoadBalancer on KinD bridge → `agentgateway-proxy`. |
| **Spoke Cluster Pods** | `http://10.99.0.100` | Hits Envoy Gateway over WireGuard mesh → proxies to active Hub NodePort `30080`. |
| **Swagger Interactive Docs**| `http://172.30.0.100/docs` | FastAPI interactive API documentation. |
| **Health Probe** | `http://172.30.0.100/health` | Comprehensive multi-service dependency health check. |
| **Kubernetes API VIP** | `https://10.99.0.100:6443` | Active-Passive OCM cluster registration and kubectl API VIP. |

---

## 9. Custom Workload Provider & Standby Microservices

### 1. `opensandbox-server` Custom OCM Image
- Contains custom `OcmWorkloadProvider` (`opensandbox-server/docker-build/src/services/k8s/ocm_provider.py`) enabling OpenSandbox to dispatch batch sandboxes across spoke clusters via OCM `ManifestWork`.
- Script automatically builds `01community/01sandbox-opensandbox-server:v0.7.10-ocm` and loads it into KinD via `kind load docker-image` before Helm chart installation, completely eliminating `ImagePullBackOff` errors.

### 2. `sandbox-api` Standby Recovery Mode
- In active-passive clusters, `sandbox-api` on `secondaryhub` connects to a read-only PostgreSQL standby database.
- Standard migrations (`CREATE TABLE IF NOT EXISTS`) fail with `ReadOnlySqlTransaction`.
- The Helm deployment mounts a dynamic ConfigMap patch for `app_state.py`:
  ```python
  cursor.execute("SELECT pg_is_in_recovery();")
  is_standby = cursor.fetchone()[0]
  if is_standby:
      print("[startup] Connected to Standby/Replica PostgreSQL. Skipping schema migrations.")
  ```
- Allows `sandbox-api` to run at `1/1 Running` on `secondaryhub`, ready for immediate sub-second promotion upon failover.

---

## 10. Operations & Verification Runbook

### Full One-Shot Destruction
To completely tear down all 4 clusters, gateway containers, transit networks, persistent state directories, and residual kubeconfig contexts:
```bash
./docker-multi-cluster-destroy.sh
```

### Full One-Shot Provisioning
To build all images, generate PKI/WireGuard keys, initialize all 4 KinD clusters, establish the mesh, join spokes, and configure continuous replication:
```bash
./docker-multi-cluster.sh
```

### End-to-End System Health Verification
To run the automated verification suite validating OCM registration, PostgreSQL WAL replication, and Valkey memory sync:
```bash
./docker-multi-cluster.sh --verify
```

### Expected Output of `--verify`
```text
======================================================================
▶ Running End-to-End System Health Checks
======================================================================

1a. OCM Managed Clusters Status on PrimaryHub:
<ManagedCluster>
├── <spoke1> (Accepted: true, Available: True)
└── <spoke2> (Accepted: true, Available: True)

1b. OCM Managed Clusters Status on SecondaryHub (Standby Hub):
<ManagedCluster>
├── <spoke1> (Accepted: false, Available: Unknown)
└── <spoke2> (Accepted: false, Available: Unknown)

2. CloudNativePG PostgreSQL Replication Sender (PrimaryHub):
 client_addr |   application_name   |   state   | sync_state
-------------+----------------------+-----------+------------
 10.244.0.1  | postgresql-secondary | streaming | async
(1 row)

3. CloudNativePG PostgreSQL Streaming Receiver (SecondaryHub):
  status   | sender_host | sender_port | latest_end_lsn
-----------+-------------+-------------+----------------
 streaming | 10.99.0.1   |       30432 | 0/10000060
(1 row)

4. Valkey Memory Replication (SecondaryHub):
role:slave
master_host:10.99.0.1
master_port:30379
master_link_status:up

======================================================================
✅ MULTI-CLUSTER HEALTH VERIFICATION COMPLETE!
======================================================================
```
