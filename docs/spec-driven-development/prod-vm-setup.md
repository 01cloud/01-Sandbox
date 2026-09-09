# SPEC-NET-2026-005: Multi-Region VM Provisioning — pfSense WireGuard Hub

> **This document is the single source of truth** for provisioning four isolated Ubuntu
> VMs across distinct geographic regions. All implementation decisions, AI-assisted
> configuration generation, and operational changes MUST reference this spec.
> Do not make changes to any component described here without first updating this document.

---

## Document Header

| Field | Value |
|---|---|
| **Spec ID** | `SPEC-NET-2026-005` |
| **Version** | `1.0.0` |
| **Status** | `DRAFT — PENDING DEPLOYMENT` |
| **Author** | kamal.tamang@berrybytes.com |
| **Methodology** | Spec-Driven Development (SDD) |
| **Architecture** | Multi-Region / Single pfSense WireGuard Hub / Hub-and-Spoke |
| **Last Updated** | 2026-09-09 |

### Changelog

| Version | Date | Change |
|---|---|---|
| `1.0.0` | 2026-09-09 | Initial production VM provisioning spec. pfSense as WireGuard hub and NAT router. 4 VMs across distinct CIDR regions. |

---

## Purpose & Scope

This specification defines the network provisioning of four (4) Ubuntu Server 20.04 LTS
virtual machines across distinct geographic regions, governed by a **single pfSense**
firewall/router acting as:

1. **WireGuard VPN hub** — all 4 VMs connect to pfSense as WireGuard clients
2. **Internet NAT router** — provides internet egress to all 4 VMs via WAN interface
3. **Central firewall** — enforces inter-VM access policy and WAN rules

**In scope:**
- pfSense WireGuard server, NAT, and firewall configuration
- Per-VM WireGuard client configuration
- Ubuntu 20.04 Netplan static IP configuration
- Firewall policy (inter-VM allow, internet egress via pfSense NAT)
- Acceptance tests for network connectivity and isolation

**Out of scope (addressed in separate specs):**
- High Availability and failover configuration
- KinD cluster provisioning and OCM hub/spoke setup
- Submariner cross-cluster service mesh
- Application-layer configuration

---

## Glossary

| Term | Definition |
|---|---|
| **pfSense** | The central network appliance acting as WireGuard hub, NAT router, and firewall |
| **`primaryhub`** | Production VM in `us-east-1`; will host the OCM primary hub cluster (cluster setup deferred) |
| **`secondaryhub`** | Production VM in `eu-central-1`; will host the OCM secondary hub cluster (cluster setup deferred) |
| **`us-east1`** | Spoke VM in `ap-south-1`; will host an OCM spoke cluster (cluster setup deferred) |
| **`eu-central-1`** | Spoke VM in `ap-southeast-1`; will host an OCM spoke cluster (cluster setup deferred) |
| **Overlay Network** | WireGuard virtual network `10.99.0.0/24` spanning pfSense and all 4 VMs |
| **Underlay Network** | Physical/VPC subnet local to each VM (e.g. `10.0.10.0/24`) |
| **Hub-and-Spoke** | WireGuard topology: all 4 VMs connect only to pfSense; pfSense routes between them |
| **`AllowedIPs = 0.0.0.0/0`** | All VM traffic (including internet) is routed through pfSense WireGuard hub |
| **`<PLACEHOLDER>`** | A value that MUST be substituted with a real value before deployment |

---

## 1. Requirements

### 1.1 Isolation Requirements

| ID | Requirement | Acceptance Criterion |
|---|---|---|
| `REQ-ISO-01` | Each VM MUST reside on a distinct `/24` subnet with a different CIDR per region | `ip a` on each VM shows only its own subnet; no shared L2 segment with other VMs |
| `REQ-ISO-02` | VMs MUST NOT communicate directly; all traffic MUST traverse pfSense | Direct cross-subnet ping between VM LAN IPs MUST fail |

### 1.2 Connectivity Requirements

| ID | Requirement | Acceptance Criterion |
|---|---|---|
| `REQ-CON-01` | All 4 VMs MUST be reachable bidirectionally over the WireGuard overlay (`10.99.0.0/24`) | Ping from any overlay IP to any other overlay IP succeeds with 0% packet loss |
| `REQ-CON-02` | All 4 VMs MUST have internet access via pfSense WAN NAT | `ping 8.8.8.8` succeeds from all 4 VMs |
| `REQ-CON-03` | Inter-VM traffic MUST route through pfSense at all times | `traceroute` from any VM shows `10.99.0.254` (pfSense overlay IP) as first hop |

### 1.3 Security Requirements

| ID | Requirement | Acceptance Criterion |
|---|---|---|
| `REQ-SEC-01` | All inter-VM payload traffic MUST be encrypted via WireGuard | All cross-VM traffic traverses `wg0`; no plaintext packets between VMs |
| `REQ-SEC-02` | pfSense is the sole WireGuard hub; VMs MUST NOT peer directly with each other | `wg showconf wg0` on any VM lists only pfSense as the single peer |
| `REQ-SEC-03` | Only WireGuard UDP 51820 is permitted inbound on pfSense WAN | pfSense WAN firewall blocks all other inbound traffic |

### 1.4 Infrastructure Requirements

| ID | Requirement | Acceptance Criterion |
|---|---|---|
| `REQ-INF-01` | OS on all 4 VMs MUST be Ubuntu Server 20.04 LTS | Confirmed via `lsb_release -a` on each VM |
| `REQ-INF-02` | pfSense MUST have a static public IP | pfSense public IP does not change between deployments |
| `REQ-INF-03` | Each VM MUST have a static private IP within its own `/24` subnet | `ip a` shows static IP; no DHCP-assigned address on any VM |

---

## 2. Architecture Decisions

### `DEC-001`: Hub-and-Spoke WireGuard (pfSense as Hub)

| Field | Detail |
|---|---|
| **Decision** | All 4 VMs connect to pfSense as the single WireGuard server. VMs do not peer with each other directly. |
| **Rationale** | Centralises routing, NAT internet access, and firewall policy at a single point. Each VM only needs pfSense's public key. Adding a new VM only requires updating pfSense, not all existing VMs. |
| **Rejected: Full Mesh** | Requires N×(N-1)/2 key pairs (6 for 4 VMs). Adding a 5th VM requires reconfiguring all 4 existing VMs. Does not provide centralised internet NAT egress. |

### `DEC-002`: `AllowedIPs = 0.0.0.0/0` on VM WireGuard Peers

| Field | Detail |
|---|---|
| **Decision** | Each VM routes all traffic (including internet) through pfSense via `AllowedIPs = 0.0.0.0/0` |
| **Rationale** | Provides internet access via pfSense NAT without extra routing config on VMs. All traffic is inspectable and controllable at pfSense. |
| **Implication** | pfSense availability is critical — if pfSense is unreachable, VMs lose both inter-VM connectivity and internet access. |

### `DEC-003`: WireGuard Overlay Subnet `10.99.0.0/24`

| Field | Detail |
|---|---|
| **Decision** | WireGuard overlay uses `10.99.0.0/24` with pfSense at `.254` and VMs at `.1`–`.4` |
| **Rationale** | Consistent with the local dev environment, allowing OCM and Kubernetes configs referencing overlay IPs to be reused without modification when promoting from dev to production. |

---

## 3. System Topology

### 3.1 Logical Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  pfSense (Central Hub)                                               │
│  ├── WAN: <PUB_IP_PFSENSE> (static public IP — required)             │
│  ├── WireGuard Server (wg0): 10.99.0.254/24   Listen: UDP 51820      │
│  └── NAT Masquerade: WAN interface → internet egress for all VMs     │
└───────────┬──────────────┬──────────────┬──────────────┬─────────────┘
            │              │              │              │
    WG Tunnel       WG Tunnel       WG Tunnel       WG Tunnel
    UDP 51820       UDP 51820       UDP 51820       UDP 51820
 (over internet) (over internet) (over internet) (over internet)
            │              │              │              │
  ┌─────────┘  ┌───────────┘  ┌───────────┘  ┌──────────┘
  ▼            ▼              ▼              ▼
┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐
│primaryhub  │ │secondaryhub│ │ us-east1   │ │eu-central-1│
│10.0.10.9   │ │10.0.20.20  │ │10.0.30.22  │ │10.0.40.26  │
│wg0:        │ │wg0:        │ │wg0:        │ │wg0:        │
│10.99.0.1   │ │10.99.0.2   │ │10.99.0.3   │ │10.99.0.4   │
└────────────┘ └────────────┘ └────────────┘ └────────────┘
 us-east-1      eu-central-1   ap-south-1     ap-southeast-1
```

### 3.2 Traffic Flow

```
Inter-VM (e.g. primaryhub → secondaryhub):
  primaryhub (10.99.0.1)
    → encrypted WireGuard UDP 51820 → public internet
    → pfSense wg0 (10.99.0.254) decrypts and routes
    → encrypted WireGuard UDP 51820 → public internet
    → secondaryhub (10.99.0.2) decrypts

Internet (e.g. primaryhub → 8.8.8.8):
  primaryhub
    → encrypted WireGuard UDP 51820 (AllowedIPs = 0.0.0.0/0)
    → pfSense wg0 decrypts → pfSense NAT MASQUERADE on WAN
    → public internet → response returns via same path
```

---

## 4. IP & Network Allocation Matrix

### 4.1 pfSense Hub

| Property | Value |
|---|---|
| WAN Public IP | `<PUB_IP_PFSENSE>` *(static — must be known before deployment)* |
| WireGuard Interface | `wg0` |
| WireGuard Overlay IP | `10.99.0.254/24` |
| Listen Port | `51820` |
| pfSense WireGuard Public Key | `<PUBKEY_pfsense>` *(generated in Phase 1)* |

### 4.2 VM Allocation

| VM ID | Hostname | Region | LAN CIDR | LAN Static IP | LAN Gateway | WireGuard Overlay IP | pfSense Endpoint |
|---|---|---|---|---|---|---|---|
| `115` | `primaryhub` | `us-east-1` | `10.0.10.0/24` | `10.0.10.9` | `10.0.10.1` | `10.99.0.1/24` | `<PUB_IP_PFSENSE>:51820` |
| `203` | `secondaryhub` | `eu-central-1` | `10.0.20.0/24` | `10.0.20.20` | `10.0.20.1` | `10.99.0.2/24` | `<PUB_IP_PFSENSE>:51820` |
| `104` | `us-east1` | `ap-south-1` | `10.0.30.0/24` | `10.0.30.22` | `10.0.30.1` | `10.99.0.3/24` | `<PUB_IP_PFSENSE>:51820` |
| `128` | `eu-central-1` | `ap-southeast-1` | `10.0.40.0/24` | `10.0.40.26` | `10.0.40.1` | `10.99.0.4/24` | `<PUB_IP_PFSENSE>:51820` |

### 4.3 WireGuard Public Key Registry

> Fill in after Phase 3 (key generation on each VM). Required before Phase 4 and Phase 5.

| Node | Overlay IP | Public Key |
|---|---|---|
| `pfSense` | `10.99.0.254` | `<PUBKEY_pfsense>` *(generated in Phase 1)* |
| `primaryhub` | `10.99.0.1` | `<PUBKEY_primaryhub>` |
| `secondaryhub` | `10.99.0.2` | `<PUBKEY_secondaryhub>` |
| `us-east1` | `10.99.0.3` | `<PUBKEY_us-east1>` |
| `eu-central-1` | `10.99.0.4` | `<PUBKEY_eu-central-1>` |

---

## 5. Firewall & Routing Policy Specification

### 5.1 pfSense WireGuard Interface (`wg0`) Rules

| Rule ID | Action | Protocol | Source | Destination | Port | Satisfies |
|---|---|---|---|---|---|---|
| `FW-WG-01` | `PASS` | `ANY` | `10.99.0.0/24` | `10.99.0.0/24` | `*` | REQ-CON-01 — inter-VM overlay traffic |
| `FW-WG-02` | `PASS` | `ANY` | `10.99.0.0/24` | `ANY` | `*` | REQ-CON-02 — internet egress via pfSense NAT |

### 5.2 pfSense WAN Interface Rules

| Rule ID | Action | Protocol | Source | Destination | Port | Satisfies |
|---|---|---|---|---|---|---|
| `FW-WAN-01` | `PASS` | `UDP` | `ANY` | `WAN Address` | `51820` | REQ-SEC-03 — allows WireGuard handshakes |
| `FW-WAN-02` | `BLOCK` | `ANY` | `ANY` | `ANY` | `*` | REQ-SEC-03 — default deny all other inbound |

### 5.3 pfSense NAT Rule

| Rule ID | Type | Source | Translated To | Description | Satisfies |
|---|---|---|---|---|---|
| `NAT-01` | `MASQUERADE` | `10.99.0.0/24` | WAN IP | Internet egress NAT for all VMs | REQ-CON-02 |

---

## 6. Configuration Specifications

> All `<PLACEHOLDER>` values MUST be substituted with real values before applying.
> Do not apply configurations that still contain unresolved placeholders.

### 6.1 pfSense WireGuard Server Configuration

**Location:** `VPN > WireGuard > Tunnels > Add Tunnel`

```
Description:       production-hub
Interface Address: 10.99.0.254/24
Listen Port:       51820
```

**Peers (add one entry per VM under the tunnel):**

```
# Peer 1: primaryhub
Description:  primaryhub
Public Key:   <PUBKEY_primaryhub>
Allowed IPs:  10.99.0.1/32

# Peer 2: secondaryhub
Description:  secondaryhub
Public Key:   <PUBKEY_secondaryhub>
Allowed IPs:  10.99.0.2/32

# Peer 3: us-east1
Description:  us-east1
Public Key:   <PUBKEY_us-east1>
Allowed IPs:  10.99.0.3/32

# Peer 4: eu-central-1
Description:  eu-central-1
Public Key:   <PUBKEY_eu-central-1>
Allowed IPs:  10.99.0.4/32
```

### 6.2 Per-VM WireGuard Client Config (`/etc/wireguard/wg0.conf`)

All 4 VMs use the same structure. Only `PrivateKey` and `Address` differ per VM.

**`primaryhub`:**
```ini
[Interface]
Address    = 10.99.0.1/24
ListenPort = 51820
PrivateKey = <PRIVKEY_primaryhub>

[Peer]   # pfSense Hub
PublicKey           = <PUBKEY_pfsense>
Endpoint            = <PUB_IP_PFSENSE>:51820
AllowedIPs          = 0.0.0.0/0
PersistentKeepalive = 25
```

**`secondaryhub`:**
```ini
[Interface]
Address    = 10.99.0.2/24
ListenPort = 51820
PrivateKey = <PRIVKEY_secondaryhub>

[Peer]   # pfSense Hub
PublicKey           = <PUBKEY_pfsense>
Endpoint            = <PUB_IP_PFSENSE>:51820
AllowedIPs          = 0.0.0.0/0
PersistentKeepalive = 25
```

**`us-east1`:**
```ini
[Interface]
Address    = 10.99.0.3/24
ListenPort = 51820
PrivateKey = <PRIVKEY_us-east1>

[Peer]   # pfSense Hub
PublicKey           = <PUBKEY_pfsense>
Endpoint            = <PUB_IP_PFSENSE>:51820
AllowedIPs          = 0.0.0.0/0
PersistentKeepalive = 25
```

**`eu-central-1`:**
```ini
[Interface]
Address    = 10.99.0.4/24
ListenPort = 51820
PrivateKey = <PRIVKEY_eu-central-1>

[Peer]   # pfSense Hub
PublicKey           = <PUBKEY_pfsense>
Endpoint            = <PUB_IP_PFSENSE>:51820
AllowedIPs          = 0.0.0.0/0
PersistentKeepalive = 25
```

### 6.3 Per-VM Netplan Static IP (`/etc/netplan/01-netcfg.yaml`)

**`primaryhub`:**
```yaml
network:
  version: 2
  ethernets:
    ens18:
      dhcp4: no
      addresses: [10.0.10.9/24]
      gateway4: 10.0.10.1
      nameservers:
        addresses: []
```

**`secondaryhub`:**
```yaml
network:
  version: 2
  ethernets:
    ens18:
      dhcp4: no
      addresses: [10.0.20.20/24]
      gateway4: 10.0.20.1
      nameservers:
        addresses: []
```

**`us-east1`:**
```yaml
network:
  version: 2
  ethernets:
    ens18:
      dhcp4: no
      addresses: [10.0.30.22/24]
      gateway4: 10.0.30.1
      nameservers:
        addresses: []
```

**`eu-central-1`:**
```yaml
network:
  version: 2
  ethernets:
    ens18:
      dhcp4: no
      addresses: [10.0.40.26/24]
      gateway4: 10.0.40.1
      nameservers:
        addresses: []
```

---

## 7. Implementation Phases

Phases are **ordered and sequential**. Do not begin a phase until all steps of the
previous phase are complete and verified.

| # | Phase | Executed On | Depends On |
|---|---|---|---|
| **1** | pfSense WireGuard server setup | pfSense UI | — |
| **2** | pfSense WAN firewall + NAT rules | pfSense UI | Phase 1 |
| **3** | WireGuard key generation on all 4 VMs | Each Ubuntu VM | — |
| **4** | Register VM public keys as pfSense WireGuard peers | pfSense UI | Phase 1 + Phase 3 |
| **5** | Write `wg0.conf` on all 4 VMs | Each Ubuntu VM | Phase 1 + Phase 3 |
| **6** | Apply Netplan static IPs on all 4 VMs | Each Ubuntu VM | — |
| **7** | Enable `wg-quick@wg0` on all 4 VMs | Each Ubuntu VM | Phase 5 + Phase 6 |
| **8** | Verification | Any VM | All phases |

### Phase 3: Key Generation Commands (run on each VM)

```bash
sudo apt update && sudo apt install -y wireguard
umask 077
wg genkey | tee /etc/wireguard/privatekey | wg pubkey > /etc/wireguard/publickey
echo "=== Public key for $(hostname) ==="
cat /etc/wireguard/publickey
```

> Copy each VM's public key output into Section 4.3 of this document, then proceed to Phase 4.

### Phase 7: Enable WireGuard (run on each VM)

```bash
sudo netplan apply
sudo systemctl enable --now wg-quick@wg0
sudo wg show wg0
```

---

## 8. Test Specifications

Each test maps to one or more requirements and is written in Given/When/Then format
for unambiguous execution by humans or AI.

### `TC-01`: WireGuard Overlay Full-Mesh Reachability
**Satisfies:** `REQ-CON-01`, `REQ-SEC-01`

```
Given:  wg-quick@wg0 is active on all 4 VMs
        pfSense WireGuard server is running
        All 4 VM public keys are registered as peers on pfSense
When:   From primaryhub, run:
          ping -c 3 -W 3 10.99.0.2    # secondaryhub
          ping -c 3 -W 3 10.99.0.3    # us-east1
          ping -c 3 -W 3 10.99.0.4    # eu-central-1
          ping -c 3 -W 3 10.99.0.254  # pfSense hub
Then:   All 4 ping commands return 0% packet loss
        sudo wg show wg0 shows latest-handshake < 60s for the pfSense peer
```

### `TC-02`: Internet Egress via pfSense NAT
**Satisfies:** `REQ-CON-02`

```
Given:  pfSense NAT masquerade active on WAN interface
        AllowedIPs = 0.0.0.0/0 on all VM WireGuard peer entries
When:   From each VM: ping -c 3 -W 5 8.8.8.8
Then:   All 4 VMs return 0% packet loss to 8.8.8.8
        traceroute 8.8.8.8 shows 10.99.0.254 (pfSense) as first hop
```

### `TC-03`: Direct VM-to-VM Plaintext Block
**Satisfies:** `REQ-ISO-02`, `REQ-SEC-01`

```
Given:  VMs are in distinct CIDRs with no shared L2 bridge
When:   From primaryhub LAN IP (10.0.10.9), ping cross-subnet LAN IPs:
          ping -c 3 -W 3 10.0.20.20   # secondaryhub LAN
          ping -c 3 -W 3 10.0.30.22   # us-east1 LAN
          ping -c 3 -W 3 10.0.40.26   # eu-central-1 LAN
Then:   All 3 pings return 100% packet loss
```

### `TC-04`: Unknown Peer Rejection
**Satisfies:** `REQ-SEC-03`

```
Given:  pfSense WireGuard peer list contains exactly the 4 registered VM public keys
When:   An unregistered host attempts WireGuard handshake to pfSense UDP 51820
          using an unregistered public key
Then:   pfSense silently drops the handshake
        The unregistered host receives no tunnel IP or overlay route
```

### Automated Verification Script

Save as `verify-net.sh` on `primaryhub` and run after completing Phase 8:

```bash
#!/usr/bin/env bash
# SPEC-NET-2026-005 v1.0.0 Network Verification Script
# Run from: primaryhub (10.99.0.1)
set -euo pipefail

PASS=0; FAIL=0

check() {
    local id="$1" desc="$2"; shift 2
    if "$@" > /dev/null 2>&1; then
        echo "  [PASS] $id: $desc"; ((PASS++))
    else
        echo "  [FAIL] $id: $desc"; ((FAIL++))
    fi
}

echo "=== SPEC-NET-2026-005 v1.0.0 Verification ==="

echo ""
echo "--- TC-01: WireGuard Overlay Reachability ---"
check TC-01a "primaryhub → secondaryhub (10.99.0.2)"   ping -c 2 -W 3 10.99.0.2
check TC-01b "primaryhub → us-east1 (10.99.0.3)"       ping -c 2 -W 3 10.99.0.3
check TC-01c "primaryhub → eu-central-1 (10.99.0.4)"   ping -c 2 -W 3 10.99.0.4
check TC-01d "primaryhub → pfSense hub (10.99.0.254)"  ping -c 2 -W 3 10.99.0.254

echo ""
echo "--- TC-02: Internet Egress via pfSense NAT ---"
check TC-02a "primaryhub → 8.8.8.8 (internet)" ping -c 2 -W 5 8.8.8.8

echo ""
echo "--- TC-03: Direct LAN Cross-Subnet Block ---"
check TC-03a "10.0.20.20 unreachable via LAN"  bash -c '! ping -c 2 -W 3 10.0.20.20'
check TC-03b "10.0.30.22 unreachable via LAN"  bash -c '! ping -c 2 -W 3 10.0.30.22'
check TC-03c "10.0.40.26 unreachable via LAN"  bash -c '! ping -c 2 -W 3 10.0.40.26'

echo ""
echo "=== Results: ${PASS} PASSED / ${FAIL} FAILED ==="
[ "$FAIL" -eq 0 ] \
    && echo "✓ All acceptance criteria met. Network provisioning complete." \
    || echo "✗ Failures detected. Resolve before proceeding to cluster setup."
```

---

## 9. Open Items (Blockers Before Deployment)

| ID | Item | Status |
|---|---|---|
| `OI-01` | Replace `<PUB_IP_PFSENSE>` with pfSense static public IP in Sections 4.1 and 6.2 | ⬜ Open |
| `OI-02` | Confirm pfSense has a static public IP; if behind NAT, configure port forwarding UDP 51820 → pfSense LAN IP | ⬜ Open |
| `OI-03` | Generate WireGuard keypairs on all 4 VMs (Phase 3) and fill Section 4.3 | ⬜ Open |
| `OI-04` | Copy pfSense WireGuard public key into Section 4.1 after Phase 1 | ⬜ Open |
| `OI-05` | Confirm network interface name (`ens18`) on each VM; update Netplan configs in Section 6.3 if different | ⬜ Open |

---

## 10. AI Assistant Context

**What this spec governs:**
Multi-region VM network provisioning only. Four Ubuntu 20.04 VMs across 4 distinct
CIDR subnets in different geographic regions. A single pfSense instance is the WireGuard
hub and internet NAT router. All VMs tunnel exclusively to pfSense using
`AllowedIPs = 0.0.0.0/0`. HA failover, cluster setup, and application workloads are
out of scope and addressed in separate specifications.

**How to use this spec:**
- To generate a VM WireGuard config: Section 6.2, substitute values from Sections 4.1 and 4.3.
- To generate the pfSense peer config: Section 6.1, substitute values from Section 4.3.
- To add a new VM: add a row to Section 4.2, add a peer block in Section 6.1, create a new Section 6.2 entry, add a `TC-01` sub-test, update Section 4.3.
- All placeholder values follow the pattern `<UPPERCASE_WITH_UNDERSCORES>`.
- `AllowedIPs = 0.0.0.0/0` on VM configs is intentional — do not change without also adding static internet routes.
- Treat `REQ-*` as hard constraints. Treat `TC-*` as mandatory acceptance tests before cluster setup begins.
