# 01Sandbox: Improved Multi-Cluster Architecture

> **Document Type:** Improved Architecture Proposal
> **Based On:** User-submitted multi-cluster architecture diagram (Sep 2026)
> **Scope:** Active-Passive Dual Hub Control Plane + Geo-Distributed Spoke Workload Layer

---

## 1. Architecture Overview

The improved architecture addresses the following gaps from the original design:

- **Redundant global entry point** (eliminates single nginx SPOF)
- **Explicit failover trigger mechanism** (VIP Watchdog with defined thresholds)
- **Defined DB replication strategy** (Primary-Replica streaming, not just "sync")
- **RabbitMQ cross-hub message continuity** (Federation/Shovel Plugin)
- **Redis cross-hub state replication** (Active job state for cancel/SSE tracking)
- **Auth0 JWKS local caching** (prevents total platform outage during Auth0 downtime)
- **Explicit spoke-to-hub binding model** (primary solid / failover dotted)

---

## 2. Full Architecture Diagram

```
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              INTERNET / USERS                                                  │
└───────────────────────────────────────────┬────────────────────────────────────────────────────┘
                                            │
                         ┌──────────────────▼──────────────────────┐
                         │   DNS-Level Global Load Balancer         │
                         │   (Route53 / Cloudflare with             │
                         │    Health Checks per nginx instance)      │
                         └───────────┬──────────────┬──────────────┘
                                     │              │
                         ┌───────────▼──┐      ┌───▼───────────────┐
                         │  nginx-1     │      │  nginx-2           │
                         │  (Primary)   │      │  (Hot Standby)     │
                         │  Region A    │      │  Region B          │
                         └──────┬───────┘      └────────┬───────────┘
                                │                       │
                         DNS failover auto-routes to healthy nginx
                                │                       │
                         └──────┴───────────────────────┘
                                         │
                                         ▼
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│                         HUB CONTROL PLANE LAYER                                                │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                │
│  ┌──────────────────────────────────┐        ┌──────────────────────────────────────────────┐  │
│  │  PRIMARY HUB  (Region: us-east-1)│        │  SECONDARY HUB  (Region: us-west-2)           │  │
│  │  Hub: PrimaryHub (Priority 1)    │        │  Hub: SecondaryHub (Priority 2)               │  │
│  │  WireGuard: 10.99.0.1            │        │  WireGuard: 10.99.0.2                         │  │
│  │  VIP: 10.99.0.100 [ACTIVE]       │◄ ─ ─ ─│  VIP: 10.99.0.100 [STANDBY]                  │  │
│  │                                  │  Yield │  ocm-vip-watchdog monitors Primary            │  │
│  │  ┌──────────────────────────┐    │  Check │  Failover trigger: 3 consecutive failures      │  │
│  │  │   agentgateway           │    │        │                                               │  │
│  │  │ (Edge Ingress & Auth)    │    │        │  ┌──────────────────────────┐                 │  │
│  │  └─────────────┬────────────┘    │        │  │   agentgateway           │                 │  │
│  │                │                │        │  │ (Edge Ingress & Auth)    │                 │  │
│  │  ┌─────────────▼────────────┐    │        │  └─────────────┬────────────┘                 │  │
│  │  │      FastAPI             │    │        │                │                              │  │
│  │  │  • JWT Validation        │    │        │  ┌─────────────▼────────────┐                 │  │
│  │  │  • API Key Mgmt          │    │        │  │      FastAPI             │                 │  │
│  │  │  • Job Tracker           │    │        │  │  (Replica / Standby)     │                 │  │
│  │  │  • JWKS cached locally   │    │        │  │  • JWKS cached locally   │                 │  │
│  │  └──────┬───────────────────┘    │        │  └──────┬───────────────────┘                 │  │
│  │         │                        │        │         │                                    │  │
│  │  ┌──────▼──────┐  ┌────────────┐ │        │  ┌──────▼──────┐  ┌────────────┐             │  │
│  │  │  RabbitMQ   │◄─┤  RabbitMQ  │ │        │  │  RabbitMQ   │◄─┤  Federation│             │  │
│  │  │  (Primary)  │  │  Federation│─┼────────┼─►│  (Replica)  │  │  Plugin    │             │  │
│  │  │  scan.repo  │  │  Shovel    │ │        │  │  scan.repo  │  │  Shovel    │             │  │
│  │  │  scan.delete│  │  Plugin    │ │        │  │  scan.delete│  │  Plugin    │             │  │
│  │  └──────┬──────┘  └────────────┘ │        │  └──────┬──────┘  └────────────┘             │  │
│  │         │                        │        │         │                                    │  │
│  │  ┌──────▼──────┐                 │        │  ┌──────▼──────┐                             │  │
│  │  │    Redis    │                 │        │  │    Redis    │                             │  │
│  │  │  (Primary)  │◄────────────────┼────────┼─►│  (Replica)  │                             │  │
│  │  │  Job States │   Active Repl.  │        │  │  Job States │                             │  │
│  │  │  Pub/Sub    │                 │        │  │  Pub/Sub    │                             │  │
│  │  └─────────────┘                 │        │  └─────────────┘                             │  │
│  │                                  │        │                                               │  │
│  │  ┌──────────────────────────┐    │        │  ┌──────────────────────────────────────────┐ │  │
│  │  │  PostgreSQL (Primary)    │    │        │  │  PostgreSQL (Replica)                    │ │  │
│  │  │  • api_keys              │    │        │  │  • Streaming Replication from Primary    │ │  │
│  │  │  • user info (name,email)│◄───┼────────┼─►│  • Auto-promote on Primary failure       │ │  │
│  │  │  [Write Primary]         │    │        │  │  [Read Replica → Write on Failover]      │ │  │
│  │  └──────────────────────────┘    │        │  └──────────────────────────────────────────┘ │  │
│  │                                  │        │                                               │  │
│  │  ┌──────────────────────────┐    │        │  ┌──────────────────────────────────────────┐ │  │
│  │  │  OpenSandbox Server      │    │        │  │  OpenSandbox Server (Standby)            │ │  │
│  │  │  + OCM Auto-Acceptor     │    │        │  │  + OCM Auto-Acceptor (Yields to Primary) │ │  │
│  │  │  + Placement Engine      │    │        │  │  + Placement Engine                      │ │  │
│  │  └──────┬───────────────────┘    │        │  └──────┬───────────────────────────────────┘ │  │
│  │         │                        │        │         │                                    │  │
│  │  ┌──────▼──────┐                 │        │  ┌──────▼──────┐                             │  │
│  │  │     PVC     │                 │        │  │     PVC     │                             │  │
│  │  │  (Primary)  │◄───────────────►│        │  │  (Replica)  │                             │  │
│  │  │  scan data  │  Volume Sync    │        │  │  scan data  │                             │  │
│  │  └─────────────┘  (Rook/Ceph or  │        │  └─────────────┘                             │  │
│  │                   Velero Backup) │        │                                               │  │
│  └──────────────────────────────────┘        └──────────────────────────────────────────────┘  │
│                                                                                                │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────┐   │
│  │  VIP Watchdog (on gateway-vm / dedicated controller)                                    │   │
│  │  • Continuously polls Primary Hub (10.99.0.1:6443)                                      │   │
│  │  • Threshold: 3 consecutive failures → triggers failover                                │   │
│  │  • Moves Virtual IP (10.99.0.100) from Primary → Secondary via iptables DNAT            │   │
│  │  • Secondary OCM Auto-Acceptor activates (spec.hubAcceptsClient: true)                  │   │
│  │  • Primary recovers → Secondary yields standby within 2 seconds                         │   │
│  └─────────────────────────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────┬─────────────────────────────────────────────────────┘
                                           │
                     ┌─────────────────────┼───────────────────────┐
                     │                     │                       │
           ──────────▼──────── ────────────▼──────── ─────────────▼────────────
          | Primary Binding  | | Primary Binding    | | Primary Binding         |
          | (on failover:    | | (on failover:      | | (on failover:           |
          | re-register to   | | re-register to     | | re-register to          |
          | Secondary Hub)   | | Secondary Hub)     | | Secondary Hub)          |
           ──────────────────   ──────────────────   ──────────────────────────
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│                        MANAGED SPOKE WORKLOAD LAYER                                            │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                │
│  ┌────────────────────────┐   ┌────────────────────────┐   ┌────────────────────────────────┐  │
│  │  Spoke: us-east-1      │   │  Spoke: us-central     │   │  Spoke: ap-south               │  │
│  │  WireGuard: 10.99.0.30 │   │  WireGuard: 10.99.0.31 │   │  WireGuard: 10.99.0.32         │  │
│  │                        │   │                        │   │                                │  │
│  │  ┌─────────────────┐   │   │  ┌─────────────────┐   │   │  ┌─────────────────────────┐   │  │
│  │  │  Klusterlet     │   │   │  │  Klusterlet     │   │   │  │  Klusterlet             │   │  │
│  │  │  Agent          │   │   │  │  Agent          │   │   │  │  Agent                  │   │  │
│  │  └────────┬────────┘   │   │  └────────┬────────┘   │   │  └──────────┬──────────────┘   │  │
│  │           │            │   │           │            │   │             │                  │  │
│  │  ┌────────▼────────┐   │   │  ┌────────▼────────┐   │   │  ┌──────────▼──────────────┐   │  │
│  │  │ sandbox pod     │   │   │  │ sandbox pod     │   │   │  │ sandbox pod             │   │  │
│  │  │ sandbox pod     │   │   │  │ sandbox pod     │   │   │  │ sandbox pod             │   │  │
│  │  └─────────────────┘   │   │  └─────────────────┘   │   │  └─────────────────────────┘   │  │
│  │                        │   │                        │   │                                │  │
│  │  surface gVisor /      │   │  surface gVisor /      │   │  surface gVisor / Kata         │  │
│  │  Kata Containers       │   │  Kata Containers       │   │  Containers                    │  │
│  └────────────────────────┘   └────────────────────────┘   └────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Components & Responsibilities

| Layer | Component | Role |
|---|---|---|
| **Global Entry** | DNS Load Balancer (Route53/Cloudflare) | Health-check aware DNS failover across 2 nginx instances |
| **Global Entry** | nginx-1 & nginx-2 (Hot Standby) | Public IP entry, forwards traffic to active VIP |
| **Hub Control** | VIP Watchdog | 3-failure threshold controller that moves `10.99.0.100` between hubs |
| **Hub Control** | AgentGateway | Edge JWT auth, rate limiting, cross-namespace routing |
| **Hub Control** | FastAPI | Job orchestration, API key management, repo scan lifecycle |
| **Hub Control** | RabbitMQ + Federation | Durable task queuing with cross-hub message mirroring |
| **Hub Control** | Redis (Primary + Replica) | Job state, Pub/Sub cancellation signals, active key sets |
| **Hub Control** | PostgreSQL (Primary + Streaming Replica) | API keys, user info, audit logs |
| **Hub Control** | PVC (Rook/Ceph or Velero) | Scan workspace data synced across hubs |
| **Hub Control** | OCM Auto-Acceptor + Placement Engine | Spoke cluster registration and workload placement |
| **Spoke** | Klusterlet Agent | Receives ManifestWork from hub, boots sandbox pods |
| **Spoke** | Sandbox Pods (gVisor/Kata) | Isolated code execution and security scanning |

---

## 4. Failover Flows

### A. Hub Failover (Primary → Secondary)

```
[VIP Watchdog] polls Primary Hub 10.99.0.1:6443
       │
       │ 3 consecutive failures detected
       ▼
[VIP Watchdog] moves iptables DNAT rule
       │  Virtual IP 10.99.0.100 now points → Secondary Hub 10.99.0.2
       ▼
[Secondary OCM Auto-Acceptor] activates
       │  spec.hubAcceptsClient: true
       │  Starts accepting spoke CSRs
       ▼
[Spoke Klusterlets] detect hub unreachable
       │  Re-register to VIP 10.99.0.100 (now pointing to secondary)
       ▼
[PostgreSQL Replica] promoted to Primary (write mode)
[RabbitMQ Replica] already has mirrored queues via Federation
[Redis Replica] already has replicated job states
       ▼
Platform fully operational on Secondary Hub
       │
       │  Primary Hub recovers
       ▼
[VIP Watchdog] detects Primary healthy
[Secondary Auto-Acceptor] yields (spec.hubAcceptsClient: false)
VIP moved back to Primary Hub → Full recovery
```

---

### B. Spoke-to-Hub Binding Model

```
Normal Operation:
  Spoke-us-east-1  ────────────► Primary Hub (solid binding)
  Spoke-us-central ────────────► Primary Hub (solid binding)
  Spoke-ap-south   ────────────► Primary Hub (solid binding)

During Hub Failover:
  Spoke-us-east-1  - - - - - - ► Secondary Hub (re-registration via VIP)
  Spoke-us-central - - - - - - ► Secondary Hub (re-registration via VIP)
  Spoke-ap-south   - - - - - - ► Secondary Hub (re-registration via VIP)
```

---

## 5. Data Replication Strategy

### PostgreSQL (api_keys, user info)
- **Mode:** Streaming Replication (Primary → Replica)
- **Write:** Primary Hub only
- **Read:** Both hubs (replica serves read queries)
- **Failover:** Replica auto-promoted to Primary via `pg_ctl promote` or Patroni

### Redis (job states, Pub/Sub, active_api_keys set)
- **Mode:** Redis Replica (async replication, acceptable small lag)
- **On failover:** Replica is promoted; in-flight job states recovered from Redis keys
- **Note:** Jobs cancelled via Pub/Sub during failover window may need re-cancellation signal

### RabbitMQ (scan.repo, scan.delete, notification.email)
- **Mode:** RabbitMQ Federation Plugin + Shovel Plugin for queue mirroring
- **Effect:** Messages in durable queues are mirrored to Secondary Hub
- **On failover:** Secondary consumers pick up from mirrored queues seamlessly

### PVC / Scan Workspace Data
- **Mode:** Rook/Ceph distributed storage (cross-hub volume replication) **or** Velero scheduled backups
- **RPO (Recovery Point Objective):** Near-zero with Rook/Ceph, ~15min with Velero scheduled snapshots

---

## 6. Auth0 JWKS Caching (Resilience Against Auth0 Outage)

```
Normal:
  FastAPI → Auth0 JWKS endpoint → Downloads public keys → Validates JWTs

During Auth0 Outage:
  FastAPI → Cached JWKS (local in-memory, TTL 1 hour)
          → Existing valid sessions continue to work
          → New logins fail gracefully (expected, Auth0 is the identity provider)
```

**Implementation:**
- Cache Auth0 JWKS keys on startup and refresh every 60 minutes
- On failed refresh, continue serving from cache with extended TTL
- Return `503 Auth Provider Unavailable` only if cache is expired AND refresh fails

---

## 7. Observability

| Signal | Tool | Scope |
|---|---|---|
| Metrics scraping | Prometheus (per hub) | FastAPI, RabbitMQ queue depths, Redis, Postgres connections |
| Cross-hub federation | Prometheus Federation | Central Grafana reads from both hub Prometheus endpoints |
| Dashboards | Grafana | Unified view of both hubs and all 3 spokes |
| Hub health | VIP Watchdog logs | Failover event audit trail |
| Alerts | AlertManager | PagerDuty/Slack: Hub down, DB replica lag, queue depth spike |

---

## 8. Network Topology Summary

```
WireGuard Mesh (10.99.0.0/24)
  ├── gateway-vm       10.99.0.254   (VIP controller, public entry)
  ├── hub1-vm          10.99.0.1     (Primary Hub)
  ├── hub2-vm          10.99.0.2     (Secondary Hub)
  ├── spoke-us-east    10.99.0.30    (Spoke Workload)
  ├── spoke-us-central 10.99.0.31    (Spoke Workload)
  └── spoke-ap-south   10.99.0.32    (Spoke Workload)

Virtual IP (Floating):
  10.99.0.100:6443 → Active Hub (Primary or Secondary based on VIP Watchdog)
```

---

## 9. Improvements Over Original Design

| Gap in Original Design | Improvement Applied |
|---|---|
| Single nginx = SPOF | Two nginx instances behind DNS health-check load balancer |
| Failover trigger was implicit | Explicit VIP Watchdog with 3-failure threshold and iptables DNAT routing |
| DB sync strategy was vague | PostgreSQL Streaming Replication with auto-promotion on failover |
| RabbitMQ in-flight messages lost on hub failure | RabbitMQ Federation + Shovel Plugin for cross-hub queue mirroring |
| Redis job states not replicated | Redis Replica with async replication; job states survive hub failover |
| Spoke-to-hub binding model unclear | Defined as primary solid binding + dotted failover re-registration via VIP |
| Auth0 outage = full platform outage | JWKS keys cached locally with 1-hour TTL for session continuity |
| No observability layer | Prometheus per hub + cross-hub federation + central Grafana dashboard |
