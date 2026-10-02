# Implementation Plan: Pure Docker Multi-Cluster Platform (`docker-multi-cluster.sh`)

## 1. Goal & Architectural Motivation

Transition the entire 5-node multi-cluster setup from running across 5 separate KVM/QEMU Virtual Machines to a **pure Docker-native architecture running on a single host (bare-metal, workstation, or CI/CD runner)**, while preserving:
1. **Network Isolation**: Clusters must be prevented from direct local IP communication.
2. **Encrypted WireGuard Mesh**: All cross-cluster communication (API calls, PostgreSQL WAL streaming, Valkey cache replication, OCM klusterlet heartbeats) must traverse an encrypted WireGuard tunnel (`10.99.0.0/24`).
3. **Continuous Database & Cache Replication**: CloudNativePG physical streaming replication on Timeline 1 and Valkey master-replica replication.
4. **Active-Passive Gateway**: Envoy proxying traffic through the Virtual IP (`10.99.0.100:6443` and `:80`).
5. **Zero Split-Brain Automated Failover & Failback**: The in-cluster `ocm-failover-controller` with declarative delta synchronization.

---

## 2. VM vs. Pure Docker Architecture Comparison

```text
┌────────────────────────────────────────────────────────────────────────┐
│                   CURRENT ARCHITECTURE (5 Heavy VMs)                   │
├────────────────────────────────────────────────────────────────────────┤
│  gateway-vm   │   hub1-vm     │   hub2-vm     │  spoke1-vm │ spoke2-vm │
│ (192.168.100) │(192.168.100)  │(192.168.101)  │(192.168.102│(192.168.10│
│  [Host wg0]   │  [Host wg0]   │  [Host wg0]   │ [Host wg0] │ [Host wg0]│
│  [Envoy Ctr]  │ [KinD Cluster]│ [KinD Cluster]│[KinD Clust]│[KinD Clust│
└────────────────────────────────────────────────────────────────────────┘

                                   ▼ MIGRATING TO ▼

┌────────────────────────────────────────────────────────────────────────┐
│              PROPOSED ARCHITECTURE (Single Host, Zero VMs)             │
├────────────────────────────────────────────────────────────────────────┤
│  Single Linux Host (Docker Engine)                                     │
│                                                                        │
│  Isolated Docker Bridge Networks:                                      │
│  ├─ net-gw    (172.20.0.0/24)  ──> container: envoy-gateway            │
│  ├─ net-hub1  (172.21.0.0/24)  ──> container: primaryhub-control-plane  │
│  ├─ net-hub2  (172.22.0.0/24)  ──> container: secondaryhub-control-plan│
│  ├─ net-spoke1(172.23.0.0/24)  ──> container: spoke1-control-plane     │
│  └─ net-spoke2(172.24.0.0/24)  ──> container: spoke2-control-plane     │
│                                                                        │
│  Underlay Interconnect:                                                │
│  • An isolated transit network (net-transit: 172.30.0.0/24) connecting │
│    all 5 nodes strictly on UDP port 51820 (WireGuard tunnel traffic)   │
│  • Host firewall (iptables) blocks all non-WireGuard traffic between   │
│    the nodes.                                                          │
│                                                                        │
│  Overlay Mesh (10.99.0.0/24):                                          │
│  • WireGuard (wg0) runs inside each container/namespace:               │
│    - gateway:        10.99.0.254 (VIP: 10.99.0.100)                    │
│    - primaryhub:     10.99.0.1                                         │
│    - secondaryhub:   10.99.0.2                                         │
│    - spoke1:         10.99.0.3                                         │
│    - spoke2:         10.99.0.4                                         │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Design Decisions

### A. How to Enforce "WireGuard-Only" Communication Without VMs
On a single Docker host, containers on the same Docker bridge can ping each other directly unless restricted. To guarantee that communication **only** happens via WireGuard:
1. **Isolated Transit Network (`172.30.0.0/24`)**: Each container is attached to an internal bridge network `net-transit`.
2. **Strict Firewall Rules (`iptables`)**:
   - Only UDP port `51820` is allowed between the nodes on the transit bridge.
   - All TCP traffic (ports 6443, 5432, 6379, 80) is **dropped** on the underlay bridge.
   - Any traffic intended for another cluster **must** enter the WireGuard `wg0` tunnel.

### B. Running WireGuard Inside KinD Containers
- KinD control-plane containers run with Docker `--privileged` mode and have access to the host Linux kernel's WireGuard module (`wireguard.ko`).
- WireGuard interfaces (`wg0`) can be created and managed directly inside each KinD node container using `wg-quick` or `ip link add dev wg0 type wireguard`.
- This keeps each Kubernetes cluster completely self-contained with its own private WireGuard IP address.

### C. Cluster Subnet Planning (Preserved)
- `primaryhub`: Pods `10.244.0.0/16`, Services `10.96.0.0/16`
- `secondaryhub`: Pods `10.245.0.0/16`, Services `10.97.0.0/16`
- `spoke1`: Pods `10.246.0.0/16`, Services `10.98.0.0/16`
- `spoke2`: Pods `10.247.0.0/16`, Services `10.100.0.0/16`
- WireGuard Overlay: `10.99.0.0/24` (VIP `10.99.0.100`)

---

## 4. Phase-by-Phase Implementation Plan

### Phase 1: Environment & Tool Verification
- Check Docker, KinD, kubectl, clusteradm, Helm v3, and WireGuard tools on the host.
- Create isolated Docker transit network `01sandbox-transit` (`172.30.0.0/24`).

### Phase 2: WireGuard Key Generation & Underlay Preparation
- Generate keypairs for the 5 entities: `gateway`, `primaryhub`, `secondaryhub`, `spoke1`, `spoke2`.
- Assign transit IPs:
  - `gateway`: `172.30.0.10`
  - `primaryhub`: `172.30.0.20`
  - `secondaryhub`: `172.30.0.21`
  - `spoke1`: `172.30.0.30`
  - `spoke2`: `172.30.0.31`

### Phase 3: KinD 4-Cluster Creation with Transit Network Attachment
- Deploy `primaryhub`, `secondaryhub`, `spoke1`, and `spoke2` with their distinct CIDRs.
- Connect each control-plane container to `01sandbox-transit` with static transit IPs.
- Restrict cross-container traffic on the bridge to UDP `51820` only.

### Phase 4: WireGuard Mesh Activation Inside Containers
- Configure `/etc/wireguard/wg0.conf` inside each KinD container and the Envoy container.
- Bring up `wg0` inside each container (`wg-quick up wg0`).
- Verify bidirectional ping across the `10.99.0.0/24` overlay.

### Phase 5: Shared Root CA & Dynamic TLS SANs
- Synchronize Root CA from `primaryhub` to `secondaryhub`.
- Add `10.99.0.100` (VIP) and `10.99.0.x` IPs to API server certificates on both hubs.

### Phase 6: OCM Hub Initialization & Priority Auto-Acceptor
- Initialize OCM on `primaryhub` and `secondaryhub`.
- Deploy `ocm-auto-acceptor` to auto-approve CSRs.

### Phase 7: Envoy Active-Passive Gateway Container
- Run `envoy-gateway` container attached to `01sandbox-transit` with WireGuard IP `10.99.0.254` and VIP `10.99.0.100`.
- Apply connection recycling (`idle_timeout: 5s`, `max_downstream_connection_duration: 60s`).

### Phase 8: Spoke Registration via Gateway VIP
- Retrieve join token from `primaryhub` via `https://10.99.0.100:6443`.
- Join `spoke1` and `spoke2` through the VIP.
- Accept clusters, apply `wireguard-ip` labels, and bind `sandbox-spokes` ClusterSet.

### Phase 9: CloudNativePG & Valkey Continuous Replication
- Deploy NodePort replication services (`30432` for PostgreSQL, `30379` for Valkey).
- Deploy `codeinspector` Helm chart on `primaryhub` (Master RW).
- Deploy `codeinspector` Helm chart on `secondaryhub` (Standby RO streaming from `10.99.0.1:5432`).

### Phase 10: In-Cluster Failover Controller & Delta Sync
- Deploy `ocm-failover-controller` on `secondaryhub` with the pre-check health barrier.
- Verify automatic failover and failback.

### Phase 11: End-to-End Verification
- Test database write on primary, verify streaming replication on secondary.
- Test `docker stop primaryhub-control-plane`: verify spokes remain available, secondary database auto-promotes to read-write.
- Test `docker start primaryhub-control-plane`: verify delta sync and safe failback without split-brain.

---

## 5. Deliverable
A single standalone script: [`docker-multi-cluster.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docker-multi-cluster.sh) with full idempotency, clean teardown (`--clean`), and one-shot setup.
