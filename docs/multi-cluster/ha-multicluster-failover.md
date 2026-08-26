# High-Availability Multi-Cluster Failover — Executive Summary

## What Was Built

A fully automated, self-healing Kubernetes infrastructure where **if the primary control hub
fails, the secondary hub takes over all cluster management within 30 seconds — with zero human
intervention required.**

This was built, tested, and verified end-to-end on 25 August 2026.

---

## The Problem We Solved

In a standard single-hub Kubernetes setup, the hub is a **single point of failure**. If it goes
down:

- All workload management stops
- No new deployments can be made
- No health checks or policy enforcement occurs
- Engineers must manually intervene to restore service

Our architecture eliminates this entirely.

---

## The Architecture: 3 Machines, 1 Virtual IP

```
┌─────────────────────────────────────────────────────────────────┐
│                    CLIENT / APPLICATIONS                        │
│              Connect via VIP: 192.168.122.230                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
              ┌────────────▼────────────┐
              │   Virtual IP (.230)     │
              │   Floats between hubs   │
              └─────┬────────────┬──────┘
                    │            │
        ┌───────────▼──┐    ┌────▼──────────┐
        │  PRIMARY HUB │    │ SECONDARY HUB  │
        │ 192.168.122  │    │ 192.168.122    │
        │    .225      │    │    .143        │
        │  (ACTIVE)    │    │  (STANDBY)     │
        └──────────────┘    └────────────────┘
                    │            │
              ┌─────▼────────────▼──────┐
              │       SPOKE1            │
              │  192.168.122.52         │
              │  (Managed Workload)     │
              └─────────────────────────┘
```

---

## The 4 Components That Make Failover Possible

### 1. 🌐 kube-vip — The Virtual IP Manager

**What it is:** A lightweight pod running on both hub nodes that manages a shared Virtual IP
address (`192.168.122.230`).

**How it works:**

- Both hubs compete for a Kubernetes Lease lock
- The winner binds the VIP to its network interface
- All client traffic, applications, and API calls go to `.230` — never a specific machine's IP
- When the primary hub fails, kube-vip on the secondary hub **wins the lease and binds the VIP**
  within seconds
- A Gratuitous ARP broadcast is sent so all devices on the network instantly reroute to the
  secondary hub

**Why it matters for business:** Applications, CI/CD pipelines, and operators never need to change
any IP address or configuration. The VIP is the permanent address — the hardware underneath is
invisible.

---

### 2. 👁️ Failover Controller — The Automated Watchdog

**What it is:** A custom watchdog script running as a Kubernetes Deployment on the secondary hub,
continuously monitoring the primary hub's health.

**How it works:**

- Every 5 seconds it checks if the primary hub's API is reachable
- If it fails 3 consecutive checks (~15 seconds), it runs a **quorum check** — asking `spoke1`
  independently: "Can you reach the primary hub?"
- Only if **both the watchdog AND spoke1 independently confirm the primary hub is unreachable**
  (2-of-2 quorum) does it trigger failover
- This prevents **split-brain** scenarios where a network glitch causes a false failover

**The quorum safety mechanism:**

```
Watchdog says: UNREACHABLE
      +
spoke1 says:   UNREACHABLE
      =
✅ REAL OUTAGE CONFIRMED → Failover triggered
```

vs.

```
Watchdog says: UNREACHABLE
      +
spoke1 says:   REACHABLE
      =
⛔ SPLIT-BRAIN SUSPECTED → Failover ABORTED (safe!)
```

**Automatic Failback:** When the primary hub recovers, the watchdog detects it within 5 seconds
and automatically resets — no engineer needed.

---

### 3. 🔗 OCM Dual Klusterlet — Dual Hub Registration

**What it is:** Open Cluster Management (OCM) is the Kubernetes multi-cluster management
framework. A "Klusterlet" is the agent that runs on `spoke1` and sends heartbeats to the hub.

**The challenge:** By default, a spoke can only register to one hub. If it only knows about the
primary hub and the primary hub dies, the spoke is orphaned.

**What we built:** Two independent Klusterlet instances on `spoke1`:

- `klusterlet` → sends heartbeats to **primaryhub** continuously
- `klusterlet-secondaryhub` → sends heartbeats to **secondaryhub** continuously

Both hubs always know `spoke1` is alive and available. When the failover controller promotes
`secondaryhub`, it is already fully registered and ready to dispatch workloads to `spoke1` — with
**no re-registration or reconnection delay**.

```
spoke1
├── open-cluster-management-agent/             ← Klusterlet #1
│   └── hub-kubeconfig-secret → primaryhub     (permanent, 5-year cert)
└── open-cluster-management-agent-secondaryhub/ ← Klusterlet #2
    └── hub-kubeconfig-secret → secondaryhub   (permanent, 5-year cert)
```

---

### 4. 🔒 RKE2 — The Production-Grade Kubernetes Distribution

**What it is:** RKE2 (Rancher Kubernetes Engine 2) is the Kubernetes distribution running on all
three nodes. It is FIPS-compliant, CIS-hardened, and designed for production workloads.

**Why it matters:**

- Runs as a `systemd` service — automatically restarts after node reboots
- Stores all cluster state in `etcd` on disk — survives power cycles
- All configuration, secrets, and certificates persist across reboots
- Client certificates issued during OCM registration are valid for **5 years** — no recurring
  manual token management

---

## The Failover Sequence — Step by Step

| Time | Event |
| :--- | :--- |
| **T+0s** | Primary hub loses network / goes down |
| **T+5s** | Failover controller records 1st missed check |
| **T+10s** | 2nd missed check |
| **T+15s** | 3rd missed check — quorum check triggered |
| **T+17s** | spoke1 confirms "unreachable" — 2-of-2 quorum passed |
| **T+18s** | Failover sequence starts — secondary hub activated |
| **T+20s** | kube-vip on secondary hub wins Lease, binds VIP `.230` |
| **T+22s** | Gratuitous ARP sent — all network traffic reroutes |
| **T+25s** | Secondary hub begins managing spoke1 workloads |
| **T+30s** | ✅ **Full failover complete. Zero human action taken.** |

---

## The Failback Sequence — Automatic Recovery

| Time | Event |
| :--- | :--- |
| **T+0s** | Primary hub restored / comes back online |
| **T+5s** | Failover controller detects primary hub is reachable again |
| **T+5s** | Watchdog resets — removes failover annotation, arms for next outage |
| **T+30s** | kube-vip on primary hub reclaims Lease, VIP returns to `.225` |
| **T+60s** | ✅ **Full failback complete. Zero human action taken.** |

---

## Verified Test Results (25 August 2026)

| Test | Result |
| :--- | :--- |
| Simulate primaryhub outage via `iptables` block | ✅ Failover triggered in ~18 seconds |
| Quorum check correctly identified real outage | ✅ Confirmed |
| Split-brain protection correctly aborted false trigger | ✅ Confirmed |
| kube-vip VIP moved to secondaryhub | ✅ Confirmed |
| spoke1 available via secondaryhub post-failover | ✅ AVAILABLE: True |
| Restore primaryhub — automatic failback | ✅ Detected within 5 seconds |
| Both hubs AVAILABLE: True after full cycle | ✅ Confirmed |
| Configuration survives VM power off and restart | ✅ Self-heals within 2–5 minutes |

---

## How to Re-Run the Failover Test

> Full step-by-step test procedure is in
> [updated-multi-cluster-architecture-and-ha-strategy.md](./updated-multi-cluster-architecture-and-ha-strategy.md)
> under **"Automated Failover + Failback Test Sequence"**.

Quick reference:

```bash
# Terminal 1 — Watch live logs on primaryhub:
KUBECONFIG=~/.kube/config-hubs kubectl --context secondaryhub \
  logs -n kube-system deploy/failover-controller -f

# Terminal 2 — Simulate outage on primaryhub:
sudo iptables -I INPUT -p tcp --dport 6443 -j REJECT
sudo iptables -I OUTPUT -p tcp --sport 6443 -j REJECT

# Restore primaryhub (test failback):
sudo iptables -D INPUT -p tcp --dport 6443 -j REJECT
sudo iptables -D OUTPUT -p tcp --sport 6443 -j REJECT

# Verify both hubs healthy:
KUBECONFIG=~/.kube/config-hubs kubectl --context primaryhub get managedcluster spoke1
KUBECONFIG=~/.kube/config-hubs kubectl --context secondaryhub get managedcluster spoke1
```

---

## Business Value Summary

| Capability | Before | After |
| :--- | :--- | :--- |
| Primary hub failure recovery | Manual — hours of engineer time | **Automatic — 30 seconds** |
| Application downtime on hub failure | Hours to days | **< 30 seconds** |
| Risk of false failover (split-brain) | N/A | **Protected by 2-of-2 quorum** |
| Configuration loss on reboot | Risk | **Zero — all state in etcd** |
| Engineer intervention for failover | Required | **Not required** |
| Engineer intervention for failback | Required | **Not required** |

---

> This infrastructure is production-ready and fully documented in the internal architecture
> runbook
> [updated-multi-cluster-architecture-and-ha-strategy.md](./updated-multi-cluster-architecture-and-ha-strategy.md).
> All 19 operational issues encountered during implementation have been recorded with root causes
> and resolutions for future reference.
