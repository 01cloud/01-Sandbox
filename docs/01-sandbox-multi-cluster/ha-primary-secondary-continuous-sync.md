# High-Availability Primary-Secondary Hub Continuous Sync (Warm Standby)

This document is the **complete, definitive reference** for the `primaryhub` ↔ `secondaryhub` high-availability architecture. It covers:

- **How everything works** — deep technical explanation of CloudNativePG, Valkey replication, CRDs, and the Helm chart structure
- **Ordered installation guide** — exactly what to do, and in what sequence, when standing up fresh hub and spoke clusters
- **Verification runbooks** — how to confirm each layer is operating correctly
- **Failover and failback lifecycle** — the complete operator-driven promotion and demotion workflow

This architecture is **100% Cloud-Native**, utilizing:
- **CloudNativePG (CNPG) Operator** for continuous physical WAL streaming replication, automated bootstrapping via `pg_basebackup`, and Kubernetes-native cluster lifecycle management.
- **Valkey** (`valkey/valkey:7.2-alpine`) as the high-performance in-memory cache with continuous master-replica memory synchronization.
- **Kubernetes-Native Networking**: Direct NodePort exposure and kernel-level iptables DNAT routing — no host-level `socat` daemons.
- **Umbrella Helm Chart (`codeInspector`)**: Fully declarative profiles (`values.yaml` for Primary RW Master, `values-secondary.yaml` for Secondary Warm Standby).

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [How CloudNativePG Works — Deep Technical Explanation](#2-how-cloudnativepg-works--deep-technical-explanation)
3. [How Valkey Replication Works — Deep Technical Explanation](#3-how-valkey-replication-works--deep-technical-explanation)
   - [3.8 Unified Linux Kernel NAT Architecture (PostgreSQL & Valkey)](#38-unified-linux-kernel-nat-architecture-replacing-systemd-socat-for-both-postgresql--valkey)
4. [How the Helm Chart, CRDs, and Operators are Structured](#4-how-the-helm-chart-crds-and-operators-are-structured)
5. [Service Replication Matrix & Write Behavior](#5-service-replication-matrix--write-behavior)
6. [Complete Ordered Installation Guide](#6-complete-ordered-installation-guide)
   - [Phase 0: Pre-Flight Checklist](#phase-0-pre-flight-checklist)
   - [Phase 1: Install Primary Hub](#phase-1-install-primary-hub)
   - [Phase 2: Install Secondary Hub (Warm Standby)](#phase-2-install-secondary-hub-warm-standby)
   - [Phase 3: Install Spoke Clusters](#phase-3-install-spoke-clusters)
7. [Helm Chart Component Reference](#7-helm-chart-component-reference)
8. [Verification & Testing Runbook](#8-verification--testing-runbook)
9. [Failover, Outage & Failback Lifecycle](#9-failover-outage--failback-lifecycle)
10. [Operational Gotchas & Troubleshooting](#10-operational-gotchas--troubleshooting)

---

## 1. Architecture Overview

### 1.1 The Problem This Solves

In a multi-cluster Open Cluster Management (OCM) control plane with two hubs (`primaryhub` and `secondaryhub`), naively applying the same Helm chart independently to both hubs creates:

- **Data Divergence**: Two independent database instances, each accepting writes — divergent primary keys, mismatched API keys, conflicting states.
- **Split-Brain Risk**: Both hubs simultaneously believe they are the source of truth.
- **Broken Failover**: If `primaryhub` goes down, `secondaryhub` has an empty or stale database.

### 1.2 The Solution

| Concern | Solution |
|:---|:---|
| Write conflicts | `secondaryhub` runs in hard read-only standby — writes are physically rejected by the database engine |
| Data synchronization | CloudNativePG physical WAL streaming — every byte committed on primary is streamed to secondary in sub-milliseconds |
| Session state | Valkey memory replication — in-memory keys (JWT tokens, rate-limit counters) mirrored in real time |
| Automated recovery | CNPG Operator handles bootstrapping from scratch via `pg_basebackup` — no manual data copy needed |
| CRD installation | CRDs are packaged inside `codeInspector/crds/` — Helm auto-installs them before any workload |

### 1.3 Visual Topology

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             CLIENT / AGENT GATEWAY                               │
│                         Shared Virtual IP: 10.99.0.100                           │
└────────────────────────────────────────┬─────────────────────────────────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 │ (Active)                                      │ (Warm Standby)
     ┌───────────▼───────────┐                       ┌───────────▼───────────┐
     │      PRIMARY HUB      │                       │     SECONDARY HUB     │
     │    192.168.100.20     │                       │    192.168.101.20     │
     │ (WireGuard: 10.99.0.1)│                       │ (WireGuard: 10.99.0.2)│
     ├───────────────────────┤                       ├───────────────────────┤
     │ • sandbox-api (RW)    │                       │ • sandbox-api (RO)    │
     │ • opensandbox-server  │                       │ • opensandbox-server  │
     │     (Active)          │   [stateless — no     │     (Standby)         │
     │                       │    cross-hub sync]    │                       │
     │ • CNPG Primary (RW)   │── Continuous WAL ────▶│ • CNPG Standby (RO)   │
     │   postgresql-primary  │   (NodePort 30432)    │  postgresql-secondary │
     │ • Valkey Master (RW)  │── Memory Sync ───────▶│ • Valkey Replica (RO) │
     │                       │   (NodePort 30379)    │                       │
     │ • RabbitMQ (Active)   │                       │ • RabbitMQ (Standby)  │
     │   [independent broker,│                       │   [independent broker,│
     │    not replicated]    │                       │    not replicated]    │
     └───────────┬───────────┘                       └───────────┬───────────┘
                 │                                               │
                 └───────────────────────┬───────────────────────┘
                                         │  OCM ManifestWork
                               ┌─────────▼─────────┐
                               │  SPOKE CLUSTERS   │
                               │(Single Klusterlet │
                               │  via 10.99.0.100) │
                               │ spoke1 & spoke2   │
                               └───────────────────┘
```

### 1.4 Network Address Reference

| Node | Physical LAN IP | WireGuard IP (`wg0`) | Kind Node IP | Role |
|:---|:---|:---|:---|:---|
| **`primaryhub`** | `192.168.100.20` | `10.99.0.1` | `172.18.0.2` | Active RW Master |
| **`secondaryhub`** | `192.168.101.20` | `10.99.0.2` | `172.18.0.2` | Warm Standby RO |
| **Virtual IP (VIP)** | Floating | `10.99.0.100` | N/A | Client Endpoint |
| **`gateway-vm`** | `192.168.100.10` | `10.99.0.100` | N/A | Floating VIP / DNAT Gateway |

### 1.5 Component HA Mode Reference

Each component in the system follows one of three distinct High-Availability patterns. Understanding which pattern a component uses — and **why** — is essential for operating, debugging, and extending this architecture.

| Component | Primary Hub | Secondary Hub | HA Mode | Rationale |
|:---|:---|:---|:---|:---|
| **PostgreSQL** (`postgresql-primary`) | **Active Master (RW)** | **Replica Standby (RO)** | **Stateful — Replicated** | Owns all durable persistent data (`api_keys`, `system_settings`, `rate_limits`). Must be single-writer to prevent split-brain. Cross-hub physical WAL streaming keeps secondary byte-identical to primary. |
| **CloudNativePG Operator** (`codeinspector-cloudnative-pg`) | **Active Controller** | **Active Controller** | **Stateless — Dual-Active** | Automation operator (not a database). Watches `Cluster` CRs on each hub, reconciles cluster state, configures replication, and auto-heals pods. Holds no SQL tables or application data. |
| **Valkey** (in-memory cache) | **Master (RW)** | **Replica (RO)** | **Stateful — Replicated** | Owns in-memory JWT active sessions, rate-limit counters, and API key cache. Must mirror PostgreSQL's master/replica model. Master-to-replica memory sync ensures failover continuity of auth state. |
| **`sandbox-api`** | **Active (RW)** | **Warm Standby (RO)** | **Stateless — Traffic-Routed** | Owns no data itself. Appears "read-only" on secondary only because the databases it connects to are read-only. Detects recovery mode via `pg_is_in_recovery()` at startup and skips DDL migrations. Ready to serve full RW traffic immediately upon VIP failover. |
| **`opensandbox-server`** | **Active** | **Standby** | **Stateless — Traffic-Routed** | Purely stateless job dispatcher. Receives scan requests, creates OCM `ManifestWork` on spokes, and ingests reports. Holds no persistent data. Nothing to replicate — runs pre-warmed on both hubs, becomes fully active on whichever hub is receiving traffic. |
| **`opensandbox-controller`** | **Active** | **Active Standby** | **Stateless — Dual-Active** | Watches `BatchSandbox` and `Pool` Custom Resources inside its own cluster. Each hub runs its own independent controller against its own Kubernetes API. No cross-hub coordination needed. |
| **RabbitMQ** | **Active Broker** | **Standby Broker** | **Transient State — Independent** | Manages short-lived async job queues (not durable cross-hub state). Queue replication between hubs is unnecessary because jobs are retried by clients on failover. Each hub runs a fully independent broker. On failover, new jobs route to the secondary's broker — in-flight jobs on the old broker are re-submitted. |
| **Spoke Clusters (OCM)** | **Active via VIP** | **Standby via VIP** | **Single Klusterlet via VIP** | Each spoke runs **one single Klusterlet agent** connected to the floating Virtual IP (`https://10.99.0.100:6443`). Gateway routes VIP to `primaryhub` during normal operation, and instantly floats to `secondaryhub` if primary fails. No duplicate agents needed. |

#### Why Only PostgreSQL and Valkey Need Cross-Hub Replication

The core rule is: **only components that own durable, long-lived state need cross-hub replication**.

- **PostgreSQL** stores API keys, user subscriptions, JWT signing keys, and scan job records — data that must survive indefinitely and be consistent across all operations.
- **Valkey** stores the live JWT session cache and rate-limit state — transient but critical for immediate auth continuity during and after failover.
- **Everything else** is either:
  - **Stateless** (sandbox-api, opensandbox-server) — connects to the databases and becomes consistent automatically
  - **Transient-state** (RabbitMQ) — queues are short-lived and re-submittable by clients
  - **Self-contained per cluster** (opensandbox-controller) — watches its own cluster's Custom Resources

---

## 2. How CloudNativePG Works — Deep Technical Explanation

### 2.1 What CloudNativePG Is

**CloudNativePG (CNPG)** is a Kubernetes Operator for managing PostgreSQL clusters. Unlike a standard `StatefulSet + ConfigMap` PostgreSQL deployment, CNPG understands PostgreSQL natively:

- It watches a Custom Resource called `Cluster` (`kind: Cluster, apiVersion: postgresql.cnpg.io/v1`)
- It automatically manages Pod creation, storage (PVC), service creation, health checks, backups, and replication — all declaratively through the Kubernetes API
- It handles **physical streaming replication** between clusters — not just logical replication

The operator itself runs as a Deployment (`codeinspector-cloudnative-pg`) inside `opensandbox-system`. It runs an infinite reconciliation loop — any time a `Cluster` CR is created, modified, or a PostgreSQL pod crashes, the operator detects the change and drives the cluster back to the desired state.

### 2.1.1 Operator Pod vs. Database Engine Pod (Where is Data Stored?)

When running `kubectl get pods -n opensandbox-system`, you will observe two PostgreSQL-related pods:

```text
NAME                                             READY   STATUS    RESTARTS   AGE
codeinspector-cloudnative-pg-5c585f7b58-w5rgt   1/1     Running   0          3d
postgresql-primary-1                             1/1     Running   0          3d
```

It is vital to understand the structural difference between these two pods:

#### 1. `postgresql-primary-1` (The Actual Database Engine ✅)
* **What it is:** This is the **real PostgreSQL database server instance**.
* **Can you store data here?** **YES.** All SQL tables, rows, API keys, and persistent volume data (`PVC`) live exclusively inside this pod on the mounted path `/var/lib/postgresql/data`.
* **Port:** Listens on standard PostgreSQL port `5432`.
* **How microservices connect:** Microservices connect via the Kubernetes Service `postgresql-primary-rw.opensandbox-system.svc:5432`.
* **Direct interactive SQL access:**
  ```bash
  kubectl exec -it -n opensandbox-system postgresql-primary-1 -c postgres -- psql -U postgres -d apikeys
  ```

#### 2. `codeinspector-cloudnative-pg-5c585f7b58-w5rgt` (The Automation Operator ❌)
* **What it is:** This is the **CloudNativePG Operator controller**, a compiled Golang program.
* **Can you store data here?** **NO.** It has no PostgreSQL database engine, no database storage volume, and cannot execute SQL statements.
* **Its only role:** It acts as an automated "robot Database Administrator (DBA)":
  * Watches Kubernetes Custom Resources (`kind: Cluster`)
  * Provisions, starts, and monitors `postgresql-primary-1` (and `postgresql-secondary-1`)
  * Configures streaming replication slots, `pg_hba.conf`, and `postgresql.conf`
  * Automatically provisions persistent volume claims (`PVC`) and handles pod auto-healing if the database engine crashes.

#### Summary Comparison

| Pod Name | Component Type | Stores SQL Data / Tables? | Port | Managed By |
| :--- | :--- | :---: | :---: | :--- |
| **`postgresql-primary-1`** | **PostgreSQL Database Server** | **YES** (`/var/lib/postgresql/data`) | `5432` | CloudNativePG Operator |
| **`postgresql-secondary-1`** | **PostgreSQL Read-Only Standby** | **YES** (Replicated read-only copy) | `5432` | CloudNativePG Operator |
| **`codeinspector-cloudnative-pg-...`** | **Operator / Controller** | **NO** (Stateless controller pod) | None (Metrics: `8080`) | Helm Deployment |

> [!IMPORTANT]
> **Never attempt to send database queries or SQL connections to `codeinspector-cloudnative-pg-...`**. All application connections, schema migrations, and API key lookups must be directed to `postgresql-primary-rw` (`postgresql-primary-1`) on Primary Hub, or read from `postgresql-secondary-ro` (`postgresql-secondary-1`) on Secondary Hub.

### 2.1.2 How and Where `postgresql.conf` is Generated

In CloudNativePG, `postgresql.conf` is not a static file deployed via a Kubernetes ConfigMap. Instead, it is **dynamically assembled by the CloudNativePG Operator** through an automated pipeline:

#### 1. Input: Source Code Helm Template
The desired parameters and authentication rules are defined under `spec.postgresql` in:
[`codeInspector/charts/apiServer/templates/cnpg-cluster.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/cnpg-cluster.yaml)

```yaml
spec:
  postgresql:
    parameters:
      max_connections: "200"
      wal_level: logical
      max_wal_senders: "10"
      wal_keep_size: "1GB"
    pg_hba:
      - host replication all all scram-sha-256
      - host replication all all md5
      - host all all all scram-sha-256
      - host all all all md5
```

When Helm applies this template, it renders into a Kubernetes `Cluster` Custom Resource (`postgresql.cnpg.io/v1`).

#### 2. Assembly: Operator Generation Engine
The operator (`codeinspector-cloudnative-pg`) detects the `Cluster` CR and executes its configuration reconciliation:
1. It initializes the base PostgreSQL cluster engine (`initdb`).
2. It generates **`custom.conf`** containing all user-specified `parameters` (`max_connections`, `wal_level`, `wal_keep_size`, etc.).
3. It generates **`override.conf`** containing mandatory system parameters enforced by the operator (such as `ssl = 'on'`, `archive_command`, socket paths, and logging formats).
4. At the end of the main `postgresql.conf`, the operator appends:
   ```ini
   # load CloudNativePG custom.conf configuration
   include 'custom.conf'

   # load CloudNativePG override.conf configuration
   include 'override.conf'
   ```
5. It compiles `pg_hba.conf` using the rules provided under `spec.postgresql.pg_hba`.

#### 3. Output: On-Disk Physical Location Inside the Pod
All configuration files reside on the persistent storage volume inside `postgresql-primary-1` (and replicated/standby on `postgresql-secondary-1`):

| File Path Inside Pod | Purpose | Generated By |
| :--- | :--- | :--- |
| **`/var/lib/postgresql/data/pgdata/postgresql.conf`** | The main configuration file loaded by PostgreSQL at startup | CNPG Operator Init Process |
| **`/var/lib/postgresql/data/pgdata/custom.conf`** | **Where your `parameters:` are written** | CNPG Operator (from `Cluster` CR) |
| **`/var/lib/postgresql/data/pgdata/override.conf`** | Mandatory system/operator parameters | CNPG Operator (system defaults) |
| **`/var/lib/postgresql/data/pgdata/pg_hba.conf`** | Client and replication authentication rules | CNPG Operator (from `pg_hba:` list) |

#### 4. Live Inspection Commands

Verify the active config file path from PostgreSQL:
```bash
kubectl exec -it -n opensandbox-system postgresql-primary-1 -c postgres -- \
  psql -U postgres -d postgres -t -c "SHOW config_file;"
# Expected: /var/lib/postgresql/data/pgdata/postgresql.conf
```

Inspect the live user parameters generated from your Helm values:
```bash
kubectl exec -it -n opensandbox-system postgresql-primary-1 -c postgres -- \
  cat /var/lib/postgresql/data/pgdata/custom.conf
```
**Example Live Content:**
```ini
archive_command = '/controller/manager wal-archive --log-destination /controller/log/postgres.json %p'
cluster_name = 'postgresql-primary'
max_connections = '200'
max_wal_senders = '10'
port = '5432'
wal_keep_size = '1GB'
wal_level = 'logical'
...
```

#### 5. Dynamic Update Lifecycle (`helm upgrade`)
Whenever you update `parameters` in `values.yaml` or `cnpg-cluster.yaml` and run `helm upgrade`:
1. The operator detects the `Cluster` CR change.
2. It writes the updated settings to `/var/lib/postgresql/data/pgdata/custom.conf`.
3. For dynamic parameters (e.g. `wal_keep_size`, `max_wal_senders`), the operator automatically runs `pg_ctl reload` with **zero downtime**.
4. For static parameters requiring a full restart (e.g. `max_connections`, `wal_level`), the operator coordinates a safe restart.

### 2.1.3 Deep Dive: Complete Parameter Breakdown of `custom.conf`

When you inspect `/var/lib/postgresql/data/pgdata/custom.conf` inside `postgresql-primary-1`, you find the exact parameters that drive PostgreSQL's runtime behavior, high-availability replication, and enterprise security. Below is the complete content of this file, followed by a category-by-category breakdown explaining what every parameter does and its critical role in our multi-cluster HA architecture.

#### Live `custom.conf` Contents

```ini
archive_command = '/controller/manager wal-archive --log-destination /controller/log/postgres.json %p'
archive_mode = 'on'
archive_timeout = '5min'
cluster_name = 'postgresql-primary'
dynamic_shared_memory_type = 'posix'
full_page_writes = 'on'
hot_standby = 'true'
listen_addresses = '*'
log_destination = 'csvlog'
log_directory = '/controller/log'
log_filename = 'postgres'
log_rotation_age = '0'
log_rotation_size = '0'
log_truncate_on_rotation = 'false'
logging_collector = 'on'
max_connections = '200'
max_parallel_workers = '32'
max_replication_slots = '32'
max_wal_senders = '10'
max_worker_processes = '32'
port = '5432'
restart_after_crash = 'false'
shared_memory_type = 'mmap'
shared_preload_libraries = ''
ssl = 'on'
ssl_ca_file = '/controller/certificates/client-ca.crt'
ssl_cert_file = '/controller/certificates/server.crt'
ssl_key_file = '/controller/certificates/server.key'
ssl_max_protocol_version = 'TLSv1.3'
ssl_min_protocol_version = 'TLSv1.3'
unix_socket_directories = '/controller/run'
wal_keep_size = '1GB'
wal_level = 'logical'
wal_log_hints = 'on'
wal_receiver_timeout = '5s'
wal_sender_timeout = '5s'
cnpg.config_sha256 = 'cff442edde9b51a5c2fb64cad03d229bc73de60159ba4bc57d4ed88eb57cff04'
```

---

#### 1. Replication & Cross-Hub High Availability

These parameters govern how PostgreSQL logs changes and streams transactions over WireGuard from `primaryhub` to `secondaryhub`:

| Parameter | Value | Source | Technical Significance & Role in Multi-Cluster HA |
| :--- | :--- | :--- | :--- |
| **`wal_level`** | `'logical'` | Helm Chart (`cnpg-cluster.yaml`) | Controls the granularity of the Write-Ahead Log. Set to `logical` to write sufficient data for both **physical streaming replication** (which powers our standby hub) and **logical decoding** (allowing change data capture or selective table subscriptions if required). |
| **`wal_keep_size`** | `'1GB'` | Helm Chart (`cnpg-cluster.yaml`) | Guarantees that at least 1 Gigabyte of past WAL files will be retained in `pg_wal` before the primary is allowed to prune them. **Critical for WAN/WireGuard HA:** if the WireGuard VPN tunnel temporarily drops between hubs, the primary holds up to 1GB of transaction history, allowing `secondaryhub` to catch up automatically upon reconnecting without needing a full, heavy basebackup resync. |
| **`max_wal_senders`** | `'10'` | Helm Chart (`cnpg-cluster.yaml`) | Specifies the maximum number of concurrent WAL streaming connections. Allows `primaryhub` to simultaneously stream to the secondary standby cluster, run live `pg_basebackup` sessions, and support replication monitoring processes without running out of slots. |
| **`max_replication_slots`** | `'32'` | CNPG Default | The maximum number of physical and logical replication slots PostgreSQL can reserve. Replication slots track the exact LSN (Log Sequence Number) of the standby, ensuring the master never purges WAL segments that the standby has not yet written to disk. |
| **`wal_sender_timeout`** | `'5s'` | CNPG Default | Replication connection heartbeat timeout from the sender (primary) side. If no data or keepalive ping is exchanged over WireGuard within 5 seconds, the master terminates the stale socket, freeing the slot for the standby to reconnect cleanly. |
| **`wal_receiver_timeout`** | `'5s'` | CNPG Default | Inactivity timeout on the receiver (standby) side. If the standby receives no traffic from the master for 5 seconds, it tears down the connection and immediately attempts to re-establish replication. This rapid 5-second detection ensures low Recovery Point Objective (RPO) alerting. |

---

#### 2. Network, IPC & Client Connections

These parameters determine how microservices and internal agents connect to PostgreSQL:

| Parameter | Value | Source | Technical Significance |
| :--- | :--- | :--- | :--- |
| **`listen_addresses`** | `'*'` | CNPG Default | Instructs the PostgreSQL process to bind to all available network interfaces inside the container. Allows traffic arriving from within the pod, from Kubernetes Service cluster IPs (`10.96.0.0/12`), and from cross-cluster WireGuard IPs (`10.99.0.0/24`). |
| **`port`** | `'5432'` | CNPG Default | The standard TCP port PostgreSQL listens on. Exposed via the Kubernetes Service `postgresql-primary-rw`. |
| **`max_connections`** | `'200'` | Helm Chart (`cnpg-cluster.yaml`) | Maximum number of concurrent client database connections allowed. Accommodates the connection pooling requirements of `sandbox-api`, `opensandbox-server`, and database administration tools simultaneously. |
| **`unix_socket_directories`** | `'/controller/run'` | CNPG Default | Relocates the local UNIX domain socket (`.s.PGSQL.5432`) from the traditional `/tmp` to `/controller/run`. This directory is shared exclusively between the PostgreSQL server and the CNPG pod controller/sidecar process for local, zero-network IPC management. |

---

#### 3. High Availability, Standby & Archiving

These parameters govern replica behavior and continuous transaction archiving:

| Parameter | Value | Source | Technical Significance |
| :--- | :--- | :--- | :--- |
| **`cluster_name`** | `'postgresql-primary'` | CNPG Operator | Identifies the database cluster instance. Injected into process process names (`ps`) and application connection strings, making it easy to identify which cluster is primary or secondary in logs. |
| **`hot_standby`** | `'true'` | CNPG Default | Specifies whether queries can be executed while PostgreSQL is in recovery / standby mode. This enables `postgresql-secondary-1` to act as a **read-only replica**, allowing microservices to offload read traffic or inspect database state during standby operation. |
| **`archive_mode`** | `'on'` | CNPG Operator | Enables continuous WAL archiving. Whenever a 16MB WAL segment is filled, PostgreSQL triggers the command defined in `archive_command`. |
| **`archive_command`** | `'/controller/manager wal-archive ... %p'` | CNPG Operator | Executes the CloudNativePG internal binary (`/controller/manager wal-archive`) with the relative path of the WAL file (`%p`). The operator compresses and routes the WAL file to designated backup storage (e.g. S3/MinIO, Barman Object Store, or local archive volume) for Point-In-Time Recovery (PITR). |
| **`archive_timeout`** | `'5min'` | CNPG Default | Forces PostgreSQL to complete and rotate the current WAL segment if 5 minutes have elapsed without reaching the 16MB boundary. This ensures that even during periods of very low database activity, new transactions are written to the archive within at most 5 minutes, capping potential data loss at 5 minutes. |

---

#### 4. Enterprise Security & Mutual TLS (mTLS)

CloudNativePG enforces end-to-end cryptographic encryption for both client application traffic and replication streams:

| Parameter | Value | Source | Technical Significance |
| :--- | :--- | :--- | :--- |
| **`ssl`** | `'on'` | CNPG Default | Enforces SSL/TLS encryption on all network connections. Unencrypted plain-text connections over TCP are blocked. |
| **`ssl_ca_file`** | `'/controller/certificates/client-ca.crt'` | CNPG Operator | Certificate Authority bundle used by PostgreSQL to authenticate connecting clients. When client certificates are presented, they must be signed by this CA to gain access (Mutual TLS / mTLS). |
| **`ssl_cert_file`** | `'/controller/certificates/server.crt'` | CNPG Operator | The server's public X.509 certificate, generated and rotated automatically by CloudNativePG using Kubernetes Secrets. |
| **`ssl_key_file`** | `'/controller/certificates/server.key'` | CNPG Operator | The server's private key, mounted with strict file permissions (`0600`) inside the container. |
| **`ssl_min_protocol_version`** | `'TLSv1.3'` | CNPG Default | Sets the minimum allowed TLS version to **TLS 1.3**. Legacy and vulnerable protocols (SSLv3, TLS 1.0, TLS 1.1, TLS 1.2) are explicitly rejected. |
| **`ssl_max_protocol_version`** | `'TLSv1.3'` | CNPG Default | Locks the maximum TLS protocol version to **TLS 1.3**, ensuring state-of-the-art cipher suites and forward secrecy across all cross-hub communication. |

---

#### 5. Crash Safety, Memory Architecture & Resilience

Parameters optimized specifically for running PostgreSQL reliably inside containerized Kubernetes environments:

| Parameter | Value | Source | Technical Significance |
| :--- | :--- | :--- | :--- |
| **`restart_after_crash`** | `'false'` | CNPG Default | **Cloud-Native Best Practice:** In traditional bare-metal setups, PostgreSQL attempts internal crash recovery if a backend process crashes unexpectedly. In Kubernetes, CNPG disables this (`false`) so that the pod immediately terminates and lets Kubernetes trigger a clean pod restart or failover to a healthy replica, preventing corrupt, half-broken crash-loop states. |
| **`full_page_writes`** | `'on'` | CNPG Default | Protects against torn pages. In the event of a sudden VM crash or power failure midway through writing a disk block, PostgreSQL writes the full page image to WAL during the first modification after a checkpoint. This guarantees crash recovery can restore blocks to a consistent state. |
| **`wal_log_hints`** | `'on'` | CNPG Default | Writes non-critical page modifications (hint bits) to the WAL. **Mandatory for `pg_rewind`**: when an old primary recovers after a failover, `pg_rewind` reads these WAL records to rewind the old master's state to match the newly promoted master without performing a complete re-clone from scratch. |
| **`dynamic_shared_memory_type`**| `'posix'` | CNPG Default | Uses POSIX shared memory primitives (`shm_open`) mounted under `/dev/shm` for parallel query worker communication. |
| **`shared_memory_type`** | `'mmap'` | CNPG Default | Selects `mmap` anonymous shared memory allocation for PostgreSQL's main buffer cache. |
| **`shared_preload_libraries`** | `''` | CNPG Default | Declares C libraries to load at server start. Defaults to empty; when enabled, allows modules such as `pg_stat_statements` or `pg_trgm` for deep query performance analytics. |

---

#### 6. Worker Parallelism & Process Concurrency

Controls the concurrency of parallel query execution and background tasks:

| Parameter | Value | Source | Technical Significance |
| :--- | :--- | :--- | :--- |
| **`max_worker_processes`** | `'32'` | CNPG Default | Sets the total ceiling for background processes that the database system can support simultaneously (including logical replication workers, parallel query workers, and autovacuum workers). |
| **`max_parallel_workers`** | `'32'` | CNPG Default | Caps the maximum number of workers that can be dedicated specifically to executing parallel query operations across all active queries. |

---

#### 7. Logging Collector & Observability

Standardizes PostgreSQL logging for automated ingestion by Kubernetes logging agents:

| Parameter | Value | Source | Technical Significance |
| :--- | :--- | :--- | :--- |
| **`logging_collector`** | `'on'` | CNPG Default | Spawns a dedicated background process to capture log messages sent to stderr or csvlog and redirect them into managed files. |
| **`log_destination`** | `'csvlog'` | CNPG Default | Emits logs in structured CSV format alongside stderr. Allows CNPG's sidecar metrics exporter and log forwarders to parse timestamps, query durations, errors, and client IPs without fragile regexes. |
| **`log_directory`** | `'/controller/log'`| CNPG Default | Dedicated mount path where all raw log files reside. |
| **`log_filename`** | `'postgres'` | CNPG Default | Prefix for generated log files (`postgres.csv`, `postgres.log`). |
| **`log_rotation_age`** | `'0'` | CNPG Default | Disables time-based log file splitting inside PostgreSQL. Log rotation is managed externally by CloudNativePG to avoid race conditions with log shippers. |
| **`log_rotation_size`** | `'0'` | CNPG Default | Disables size-based log file splitting for the same reason. |
| **`log_truncate_on_rotation`** | `'false'` | CNPG Default | Ensures existing logs are never overwritten if an unexpected rotation event occurs. |

---

#### 8. Operator State & Live Reload Detection

| Parameter | Value | Source | Technical Significance |
| :--- | :--- | :--- | :--- |
| **`cnpg.config_sha256`** | `'cff442edde9...'` | CNPG Operator | A custom PostgreSQL GUC (Grand Unified Configuration) parameter injected by the CloudNativePG operator. It holds the cryptographic SHA-256 hash of the generated configuration. Whenever a developer modifies `values.yaml` or `cnpg-cluster.yaml`, the operator renders the new configuration, recalculates this hash, and compares it: <br>• If only dynamic parameters changed: operator invokes `pg_ctl reload` with **zero downtime**.<br>• If static parameters changed: operator marks the pod as needing a rolling restart and coordinates a safe switchover. |

---

#### Parameter Classification: Hot-Reload (`pg_ctl reload`) vs. Restart Required

| Hot-Reloadable (Zero Downtime via `pg_ctl reload`) | Requires Database Restart (`pg_ctl restart`) |
| :--- | :--- |
| `wal_keep_size` | `wal_level` |
| `max_wal_senders` | `max_connections` |
| `archive_timeout` | `port` |
| `archive_command` | `listen_addresses` |
| `archive_mode` (when changing target) | `shared_memory_type` |
| `wal_sender_timeout` | `dynamic_shared_memory_type` |
| `wal_receiver_timeout` | `max_worker_processes` |
| `ssl` / certificate paths (`ssl_cert_file`, etc.) | `max_replication_slots` |
| `log_destination`, `log_directory` | `shared_preload_libraries` |
| `cnpg.config_sha256` | `restart_after_crash` |

---

### 2.1.4 How and Where `pg_hba.conf` is Generated (The 3-Layer Authentication Pipeline)

PostgreSQL client authentication is governed by `pg_hba.conf` (Host-Based Authentication). In CloudNativePG, this file is **not a static ConfigMap**; it is **dynamically compiled by the CloudNativePG Operator** and written directly to the persistent storage volume inside the database pod at:

```text
/var/lib/postgresql/data/pgdata/pg_hba.conf
```

#### 1. Input: Source Code Helm Template

The user-defined access rules are declared under `spec.postgresql.pg_hba` in:
[`codeInspector/charts/apiServer/templates/cnpg-cluster.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/cnpg-cluster.yaml#L34-L38)

```yaml
spec:
  postgresql:
    pg_hba:
      - host replication all all scram-sha-256
      - host replication all all md5
      - host all all all scram-sha-256
      - host all all all md5
```

When Helm applies this chart, it renders these rules directly into the Kubernetes `Cluster` Custom Resource (`kind: Cluster, apiVersion: postgresql.cnpg.io/v1`).

#### 2. The 3-Layer Assembly Pipeline

When CloudNativePG generates `pg_hba.conf`, its internal reconciliation engine stitches together three distinct rule layers in a strict hierarchical order:

```
┌─────────────────────────────────────────────────────────────┐
│ 1. FIXED RULES (Injected automatically by CNPG)             │
│    • UNIX domain socket peer auth                           │
│    • Strict mTLS cert auth for streaming_replica & pooler   │
├─────────────────────────────────────────────────────────────┤
│ 2. USER-DEFINED RULES (Rendered from cnpg-cluster.yaml)     │
│    • User password authentication (scram-sha-256 / md5)    │
│    • Cross-cluster replication client rules                 │
├─────────────────────────────────────────────────────────────┤
│ 3. DEFAULT RULES (CNPG Fallback)                            │
│    • Fallback scram-sha-256 for all remaining connections   │
└─────────────────────────────────────────────────────────────┘
```

#### 3. Live File Contents on `postgresql-primary-1`

```ini
#
# FIXED RULES
#

# Grant local access ('local' user map)
local all all peer map=local

# Require client certificate authentication for the streaming_replica user
hostssl postgres streaming_replica all cert
hostssl replication streaming_replica all cert
hostssl all cnpg_pooler_pgbouncer all cert

#
# USER-DEFINED RULES
#

host replication all all scram-sha-256
host replication all all md5
host all all all scram-sha-256
host all all all md5

#
# DEFAULT RULES
#
host all all all scram-sha-256
```

#### 4. Detailed Breakdown of Each Rule

| Section | Rule | Type & Target | Authentication Method | Purpose in Architecture |
| :--- | :--- | :--- | :--- | :--- |
| **Fixed Rules** | `local all all peer map=local` | Local UNIX Domain Socket (`/controller/run`) | `peer map=local` | Grants passwordless socket access to local container processes mapped under the `local` user map (such as the CNPG instance manager and `postgres` OS user). |
| **Fixed Rules** | `hostssl postgres streaming_replica all cert` | TCP over SSL (`hostssl`) to database `postgres` | `cert` (mTLS) | Enforces strict Mutual TLS client certificate validation for the `streaming_replica` system user. Only clients possessing a valid certificate signed by `client-ca.crt` can connect. |
| **Fixed Rules** | `hostssl replication streaming_replica all cert` | TCP over SSL (`hostssl`) for WAL Replication | `cert` (mTLS) | Secures streaming physical WAL replication across hubs. Standby nodes must present the `streaming_replica.crt` signed by the cluster CA to pull WAL streams. |
| **Fixed Rules** | `hostssl all cnpg_pooler_pgbouncer all cert` | TCP over SSL (`hostssl`) to all databases | `cert` (mTLS) | Pre-provisions mTLS authentication for CloudNativePG's integrated PgBouncer connection pooler instances. |
| **User Rules** | `host replication all all scram-sha-256`<br>`host replication all all md5` | TCP (`host`) for replication | `scram-sha-256` / `md5` | Injected from Helm chart. Allows replication connections authenticated with passwords as a fallback or for external cross-cluster replication tools. |
| **User Rules** | `host all all all scram-sha-256`<br>`host all all all md5` | TCP (`host`) to all databases for all users | `scram-sha-256` / `md5` | Injected from Helm chart. Allows microservices (`sandbox-api`, `opensandbox-server`, schema migrators) to connect from Kubernetes pods and external management endpoints using standard password credentials. |
| **Default Rules**| `host all all all scram-sha-256` | Fallback TCP (`host`) | `scram-sha-256` | CloudNativePG operator safety net. Guarantees that any TCP connection not caught by user-defined rules requires encrypted SCRAM password authentication. |

#### 5. Dynamic Zero-Downtime Reload Lifecycle

Authentication rules in `pg_hba.conf` do **not** require a database restart:
1. When you edit `spec.postgresql.pg_hba` in `cnpg-cluster.yaml` or `values.yaml` and execute `helm upgrade`:
2. The CloudNativePG operator detects the Custom Resource modification.
3. It regenerates `/var/lib/postgresql/data/pgdata/pg_hba.conf` inside the running pod.
4. It executes `pg_ctl reload` (sending `SIGHUP` to PostgreSQL).
5. All new client connections are evaluated against the updated rules immediately, with **zero connection drops or database downtime**.

#### 6. Live Verification Command

```bash
kubectl exec -it -n opensandbox-system postgresql-primary-1 -c postgres -- \
  cat /var/lib/postgresql/data/pgdata/pg_hba.conf
```

---

### 2.2 The CNPG Operator Process (Step by Step)

When you apply the Helm chart, the following sequence occurs automatically:

#### On `primaryhub` (role: `primary`)

```
Helm Apply
    │
    ▼
CNPG Operator reads Cluster CR: postgresql-primary
    │
    ├─ Creates PersistentVolumeClaim (5Gi) for PGDATA
    ├─ Generates PostgreSQL configuration (postgresql.conf):
    │     max_connections = 200
    │     wal_level = logical
    │     max_wal_senders = 10
    │     wal_keep_size = 1GB
    ├─ Generates pg_hba.conf (authentication rules — who can connect and replicate)
    ├─ Initializes the database cluster (initdb):
    │     Creates database: apikeys
    │     Sets superuser credentials from Secret: postgresql-primary-credentials
    ├─ Starts postgresql-primary-1 Pod
    │     Labels: cnpg.io/cluster=postgresql-primary, role=primary
    └─ Opens WAL streaming slot — listens for standby connections on port 5432
```

#### On `secondaryhub` (role: `standby`)

```
Helm Apply
    │
    ▼
CNPG Operator reads Cluster CR: postgresql-secondary
    │
    ├─ Reads: replica.enabled = true
    ├─ Reads: bootstrap.pg_basebackup.source = postgresql-primary
    ├─ Reads: externalClusters[0].connectionParameters.host = 10.99.0.1 [primaryhub VM WireGuard IP]
    │
    ├─ Initiates pg_basebackup over WireGuard:
    │     Connects to 10.99.0.1:5432 [primaryhub VM WireGuard Ingress]
    │     → iptables DNAT on primaryhub host → Kind NodePort 172.18.0.2:30432 [KinD Node Container] → postgresql-primary-1 Pod [10.244.0.x:5432]
    │     Authenticates with postgres / password123
    │     Copies entire PGDATA directory byte-by-byte to secondaryhub PVC
    │     Duration: ~30–120 seconds depending on database size
    │
    ├─ Starts postgresql-secondary-1 Pod in recovery mode:
    │     Creates standby.signal (signals PostgreSQL engine to enter Hot Standby)
    │     Writes override.conf: primary_conninfo pointing to primaryhub (10.99.0.1:5432 [primaryhub VM WireGuard Ingress])
    │     Mounts secure passfile: /controller/external/postgresql-primary/pgpass
    │     Opens WAL receiver process (walreceiver)
    │
    └─ Continuous streaming begins:
          primaryhub WAL sender → TCP over WireGuard → secondaryhub WAL receiver
          Lag: sub-millisecond under normal conditions
          Every INSERT/UPDATE/DELETE on primary → immediately visible on secondary
```

### 2.2.1 Deep Dive: How Recovery & Standby Mode Works (The Modern Replacement for `recovery.conf`)

In legacy PostgreSQL (version 11 and earlier), replication standby parameters were placed into a standalone file called `recovery.conf`. However, **starting in PostgreSQL 12 (and continuing in modern PostgreSQL 14, 15, and 16 used by CloudNativePG), `recovery.conf` was officially deprecated and removed from PostgreSQL**.

To achieve recovery mode, CloudNativePG and modern PostgreSQL divide the recovery architecture into **three distinct files** on the persistent volume inside `postgresql-secondary-1`:

```
/var/lib/postgresql/data/pgdata/
├── standby.signal     <-- [1. Standby Trigger] Signals PostgreSQL to boot in recovery mode
├── override.conf      <-- [2. Recovery Settings] primary_conninfo, restore_command, timeline
└── /controller/external/postgresql-primary/
    └── pgpass         <-- [3. Authentication] Secure credential passfile for the primary
```

---

#### 1. The Trigger: `standby.signal` (The 0-Byte Sentinel File)

* **File Location:** `/var/lib/postgresql/data/pgdata/standby.signal`
* **File Size:** `0 bytes` (Empty file)

> [!NOTE]
> **Why is `cat standby.signal` completely empty?**
> Running `cat standby.signal` returns nothing because `standby.signal` is **not a configuration file**. In operating systems and database design, it is a **sentinel file** (or "flag file"). PostgreSQL does not parse any text from it — its physical presence on disk is a pure boolean flag (`true` / `false`).

##### Internal Engine Mechanics (How PostgreSQL Reads It):
During startup, the PostgreSQL core engine checks for the file's presence in the filesystem using standard POSIX `access()`:

```c
/* PostgreSQL core engine internal startup logic */
if (access("standby.signal", F_OK) == 0) {
    /* File exists -> Boot into Standby / Recovery Mode */
    ArchiveRecoveryRequested = true;
    StandbyModeRequested = true;
} else {
    /* File does NOT exist -> Boot as active Read-Write Primary */
    ArchiveRecoveryRequested = false;
    StandbyModeRequested = false;
}
```

##### Clean Separation of Concerns:
```
                  ┌────────────────────────────────────────┐
                  │          standby.signal                │
                  │             (0 bytes)                  │
                  │   "YES, I am a standby replica"        │
                  │       (The ON/OFF Switch)              │
                  └──────────────────┬─────────────────────┘
                                     │
                    Tells PostgreSQL to look for:
                                     │
                  ┌──────────────────▼─────────────────────┐
                  │           override.conf                │
                  │  primary_conninfo = 'host=10.99.0.1...'│
                  │  "WHERE and HOW to stream data"        │
                  │          (The Wiring)                  │
                  └────────────────────────────────────────┘
```

##### Standby Mode Behavioral Contract:
When `standby.signal` is present on disk:
1. **Enforces Read-Only Operation:** Sets `hot_standby = true`. Applications can query tables, but any write (`INSERT`, `UPDATE`, `DELETE`, `CREATE TABLE`, migrations) is strictly blocked.
2. **Infinite WAL Streaming:** Tells PostgreSQL: *"Do not exit recovery when you reach the end of current WAL records. Instead, keep the connection open and continuously stream new incoming WAL chunks from `primary_conninfo`."*
3. **Difference vs. `recovery.signal`:**
   - `standby.signal`: Instructs the engine to stay in standby replication mode **indefinitely**.
   - `recovery.signal`: Instructs the engine to perform a targeted Point-In-Time Recovery (PITR) from archives, and automatically exit recovery once the target timestamp/LSN is reached.

##### Failover & Promotion Lifecycle:
When `primaryhub` fails and `secondaryhub` is promoted:
1. CloudNativePG invokes `pg_ctl promote`.
2. PostgreSQL completes replaying any unapplied WAL records left in its buffer.
3. PostgreSQL **deletes (`rm` / `unlink`) `standby.signal`** from `/var/lib/postgresql/data/pgdata/`.
4. The database engine immediately transitions into a full **Read-Write Primary** and opens port `5432` for write transactions.

---

#### 2. The Recovery Settings: `override.conf`

* **File Location:** `/var/lib/postgresql/data/pgdata/override.conf`
* **Generated By:** The CloudNativePG Operator instance manager on `secondaryhub`, derived directly from `values-secondary.yaml`:
  ```yaml
  replication:
    enabled: true
    role: "standby"
    primaryHost: "10.99.0.1"
    primaryPort: 5432
  ```
* **How PostgreSQL Loads It:** The main `postgresql.conf` appends `include 'override.conf'` at its final line, ensuring these operator-managed settings override any conflicting defaults.

#### Live `override.conf` Contents on `postgresql-secondary-1`:

```ini
recovery_target_timeline = 'latest'
restore_command = '/controller/manager wal-restore --log-destination /controller/log/postgres.json %f %p'
primary_slot_name = ''
primary_conninfo = 'dbname=''apikeys'' host=''10.99.0.1'' passfile=''/controller/external/postgresql-primary/pgpass'' port=''5432'' sslmode=''prefer'' user=''postgres'''
```

#### Detailed Parameter Breakdown:

| Parameter | Live Value | Purpose & Architectural Role |
| :--- | :--- | :--- |
| **`primary_conninfo`** | `dbname='apikeys' host='10.99.0.1' passfile='/controller/external/postgresql-primary/pgpass' port='5432' sslmode='prefer' user='postgres'` | The exact connection string used by the secondary's `walreceiver` process to stream WAL over the WireGuard tunnel (`10.99.0.1:5432`). It delegates authentication to a dedicated secure `passfile`. |
| **`recovery_target_timeline`** | `'latest'` | In PostgreSQL, each failover creates a new timeline branch (Timeline 1, Timeline 2, etc.). Setting this to `'latest'` instructs the standby to automatically follow the newest timeline branch, preventing split timeline divergence. |
| **`restore_command`** | `'/controller/manager wal-restore --log-destination /controller/log/postgres.json %f %p'` | Fallback WAL recovery mechanism. If real-time TCP streaming replication lags or disconnects, PostgreSQL invokes the CloudNativePG manager to pull archived WAL files (`%f`) from backup storage into target path (`%p`). |
| **`primary_slot_name`** | `''` | Physical replication slot identifier on the primary. If empty, the standby streams WAL directly based on `wal_keep_size = 1GB`. |

---

#### 3. Secure Credentials Storage: `pgpass`

* **File Location:** `/controller/external/postgresql-primary/pgpass`
* **File Permissions:** `0600` (Strictly restricted to user `postgres`)
* **Live Content:**
  ```text
  10.99.0.1:5432:*:postgres:password123
  ```
* **Security Rationale:**
  - Traditional configurations embedded plaintext passwords directly in connection strings (`primary_conninfo = '... password=password123'`).
  - Plaintext passwords in configuration files pose serious security risks because they can be exposed in PostgreSQL system views (`pg_settings`), log files, and process listings (`ps aux`).
  - CloudNativePG decouples authentication by mounting the credentials from a Kubernetes Secret into `/controller/external/postgresql-primary/pgpass`. The `primary_conninfo` parameter only references the file path (`passfile='...'`).

---

#### 4. Verification & Inspection Commands on `secondaryhub`

You can verify the entire recovery architecture directly on `secondaryhub`:

```bash
# 1. Verify that the standby trigger file exists (0 bytes):
kubectl exec -it -n opensandbox-system postgresql-secondary-1 -c postgres -- \
  ls -la /var/lib/postgresql/data/pgdata/standby.signal

# 2. View the active recovery and primary_conninfo parameters:
kubectl exec -it -n opensandbox-system postgresql-secondary-1 -c postgres -- \
  cat /var/lib/postgresql/data/pgdata/override.conf

# 3. View the secure credentials passfile:
kubectl exec -it -n opensandbox-system postgresql-secondary-1 -c postgres -- \
  cat /controller/external/postgresql-primary/pgpass

# 4. Check whether PostgreSQL confirms it is in recovery mode:
kubectl exec -it -n opensandbox-system postgresql-secondary-1 -c postgres -- \
  psql -U postgres -d apikeys -t -c "SELECT pg_is_in_recovery();"
# Expected Output: t (true)
```

---

### 2.2.2 End-to-End Replication Summary: Primary Hub to Secondary Hub Mechanism

Below is the complete architectural summary of how CloudNativePG synchronizes and coordinates database state between `primaryhub` and `secondaryhub`:

```
┌───────────────────────────────────────────────────┐               ┌───────────────────────────────────────────────────┐
│              PRIMARYHUB (10.99.0.1)               │               │             SECONDARYHUB (10.99.0.2)              │
│                                                   │               │                                                   │
│   ┌───────────────────────────────────────────┐   │               │   ┌───────────────────────────────────────────┐   │
│   │ CNPG Operator: codeinspector-cloudnative-pg│   │               │   │ CNPG Operator: codeinspector-cloudnative-pg│   │
│   └─────────────────────┬─────────────────────┘   │               │   └─────────────────────┬─────────────────────┘   │
│                         │ Reconciles              │               │                         │ Reconciles              │
│                         ▼                         │               │                         ▼                         │
│   ┌───────────────────────────────────────────┐   │               │   ┌───────────────────────────────────────────┐   │
│   │ Pod: postgresql-primary-1 (Read-Write)    │   │               │   │ Pod: postgresql-secondary-1 (Read-Only)   │   │
│   │                                           │   │               │   │                                           │   │
│   │  • Transactions committed to local WAL    │   │               │   │  • standby.signal (0-byte sentinel flag)  │   │
│   │  • wal_keep_size = 1GB (resilience buffer)│   │               │   │  • override.conf (primary_conninfo)       │   │
│   │  • max_wal_senders = 10                   │   │               │   │  • pgpass (mounted 0600 auth secret)      │   │
│   │                                           │   │               │   │                                           │   │
│   │        [walsender process]                │   │  WireGuard    │   │        [walreceiver process]              │   │
│   │                 │                         │   │  wg0 overlay  │   │                 ▲                         │   │
│   └─────────────────┼─────────────────────────┘   │               │   └─────────────────┼─────────────────────────┘   │
│                     │                             │  10.99.0.0/24 │                     │                             │
│                     └─────────────────────────────┼───────────────┼─────────────────────┘                             │
│                                                   │  TCP :5432    │                                                   │
│                             Continuous WAL Stream │ (TLS 1.3)     │                                                   │
│                             < 1ms sub-millisecond ├───────────────► Replays WAL byte-by-byte into PGDATA              │
│                                                   │               │                                                   │
└───────────────────────────────────────────────────┘               └───────────────────────────────────────────────────┘
```

#### Step-by-Step Lifecycle

##### 1. Initial Bootstrap (`pg_basebackup` via WireGuard)
* **Trigger:** When Helm applies `values-secondary.yaml`, the secondary `Cluster` Custom Resource declares:
  ```yaml
  bootstrap:
    pg_basebackup:
      source: postgresql-primary # Points to externalClusters[0] (10.99.0.1:5432)
  ```
* **Execution:** Before starting PostgreSQL, the CloudNativePG operator on `secondaryhub` connects over WireGuard (`10.99.0.1:5432`) to `primaryhub`.
* **Action:** It initiates a streaming `pg_basebackup`, copying the entire `PGDATA` directory byte-by-byte into `postgresql-secondary-1`'s persistent volume claim (PVC). The secondary now holds a bit-for-bit identical snapshot.

##### 2. Preparing Recovery Mode
Once `pg_basebackup` completes, the operator configures PostgreSQL to start as a **standby replica** rather than an independent master:
1. **Injects `standby.signal`:** Creates an empty 0-byte file in `/var/lib/postgresql/data/pgdata/standby.signal` (the hardware-like switch that forces the engine into Hot Standby).
2. **Generates `override.conf`:** Writes the replication connection directives:
   ```ini
   primary_conninfo = 'host=10.99.0.1 port=5432 dbname=apikeys user=postgres passfile=/controller/external/postgresql-primary/pgpass'
   recovery_target_timeline = 'latest'
   ```
3. **Mounts `pgpass`:** Securely mounts credentials (`10.99.0.1:5432:*:postgres:password123`) with `0600` permissions, keeping plaintext passwords out of logs and process listings.

##### 3. Starting the Engine in Hot Standby
PostgreSQL starts up on `secondaryhub`:
* Detects `standby.signal` on disk and initializes the **`walreceiver`** process.
* Enforces `hot_standby = true`:
  * Read queries (`SELECT`) can be served locally on `secondaryhub` for monitoring or cache pre-warming.
  * Write queries (`INSERT`, `UPDATE`, `DELETE`, DDL) are strictly blocked to eliminate split-brain hazards.

##### 4. Continuous Physical Streaming Replication (WAL Streaming)
1. **Application write:** `sandbox-api` on `primaryhub` executes an `INSERT` into `api_keys`.
2. **Local WAL append:** The transaction is written to the primary's local Write-Ahead Log (`pg_wal`).
3. **Streaming over WireGuard:** The primary's `walsender` process immediately pushes the binary WAL bytes across WireGuard TCP to `10.99.0.2:5432`.
4. **Local replay:** The secondary's `walreceiver` receives the bytes and PostgreSQL's internal `startup` process immediately replays them into the secondary data pages.
* **Latency:** Under normal network conditions, replication lag across hubs is **sub-millisecond (< 1ms)**.

##### 5. Network Drop Resilience (`wal_keep_size = 1GB`)
* If the WireGuard tunnel briefly drops or the secondary VM reboots:
  * The primary continues accepting client writes.
  * Because `custom.conf` specifies `wal_keep_size = '1GB'`, `primaryhub` keeps up to 1 Gigabyte of past transaction logs in its disk buffer.
  * When `secondaryhub` reconnects, its `walreceiver` requests missing WAL records starting from its last known LSN. The primary transmits them, and the standby catches up in seconds without a heavy re-clone.

##### 6. Automated Failover & Promotion
* If `primaryhub` suffers an unrecoverable failure:
  1. The administrator or operator triggers promotion on the secondary (`pg_ctl promote`).
  2. PostgreSQL finishes replaying any uncommitted WAL buffers.
  3. PostgreSQL **deletes `standby.signal`**.
  4. The secondary instance immediately transitions into an active **Read-Write Primary**, enabling write traffic on port `5432`.

---

#### Architectural Comparison

| Feature | `primaryhub` (`postgresql-primary-1`) | `secondaryhub` (`postgresql-secondary-1`) |
| :--- | :--- | :--- |
| **Role** | Active Master | Warm Standby Replica |
| **I/O Capability** | **Read-Write (RW)** | **Read-Only (RO)** |
| **Trigger File** | *None* | `standby.signal` (0-byte sentinel flag) |
| **Replication Process**| `walsender` (pushes WAL bytes) | `walreceiver` (pulls & replays WAL bytes) |
| **Network Endpoint** | Listens on `0.0.0.0:5432` | Connects out to `10.99.0.1:5432` |
| **Data Synchronization**| Master of record (ACID durability) | Byte-for-byte replica (< 1ms lag) |

---

### 2.3 What is WAL (Write-Ahead Log)?

Every database write in PostgreSQL is first written to the **Write-Ahead Log (WAL)** before being applied to the actual data files. This guarantees durability — if a crash happens midway through a write, the WAL is replayed during recovery.

Physical streaming replication works by **continuously shipping WAL records** from primary to standby:

```
primaryhub:
  Application → INSERT INTO api_keys (id, name) VALUES ('abc', 'mykey')
      │
      ├─ WAL record created: { lsn: 0/8000100, data: <binary INSERT record> }
      ├─ Record written to WAL file on disk (guaranteed durable)
      ├─ Record applied to PGDATA (data files updated)
      └─ WAL sender process ships record → over TCP (WireGuard tunnel) → secondaryhub

secondaryhub:
  WAL receiver process receives the binary WAL record
      │
      └─ Applies the WAL record byte-by-byte to its own PGDATA
         The row now exists in secondaryhub's database
         secondaryhub did NOT independently execute SQL — it only replayed bytes
         Lag from write on primary to visible on secondary: < 1ms
```

This is **physical replication** — it operates at the storage level (WAL bytes), not the SQL level. The standby is a perfect bit-for-bit replica of the primary at every moment.

### 2.4 Why the Standby is Read-Only

Because the standby is constantly receiving and applying WAL records from the primary, it is in **recovery mode** (`pg_is_in_recovery() = true`). PostgreSQL enforces:

- **All writes are rejected**: `ERROR: cannot execute INSERT in a read-only transaction`
- **Reads are fully supported**: `SELECT`, `EXPLAIN`, aggregates, joins — all work normally
- **The standby does not generate its own WAL** — it only consumes WAL from the primary

This is enforced **at the PostgreSQL engine level** — it cannot be overridden by application code, environment variables, or database users (even superusers).

### 2.5 WAL Keep Size and Network Resilience

The primary is configured with `wal_keep_size = 1GB`. This means:

- PostgreSQL retains up to 1GB of old WAL segment files on disk after they are applied
- If the WireGuard tunnel drops temporarily (e.g., 10–30 minutes of disconnection), the standby can reconnect and **stream-resume from where it left off** using the retained WAL segments
- Only if the standby falls **more than 1GB of WAL behind** (e.g., extreme database write load + extended outage) would a full `pg_basebackup` re-bootstrap be required

This provides resilience without requiring permanent WAL archiving (e.g., to S3 or a backup store).

### 2.6 pg_hba.conf — Allowing Remote Replication

The primary is configured with these `pg_hba` rules (in the CNPG Cluster CR spec):

```
host replication all all scram-sha-256
host replication all all md5
host all all all scram-sha-256
host all all all md5
```

These rules allow **any host** — including `secondaryhub` connecting over the WireGuard interface (`10.99.0.2`) — to authenticate for:
1. Physical streaming replication (using `replication` database)
2. Normal database access (for `pg_basebackup`, monitoring, application connections)

In production you would restrict these to specific CIDRs (e.g., `10.99.0.0/24`). For this deployment, the WireGuard tunnel already provides network-level isolation.

### 2.7 The ExternalCluster Reference on Standby

The secondary Cluster CR (`postgresql-secondary`) contains this critical section:

```yaml
externalClusters:
- name: postgresql-primary
  connectionParameters:
    host: "10.99.0.1"   # primaryhub WireGuard IP
    port: "5432"
    user: "postgres"
    dbname: "apikeys"
    sslmode: prefer
  password:
    name: postgresql-primary-credentials   # Kubernetes Secret
    key: password
```

This tells the CNPG Operator:
- Where to connect for `pg_basebackup` (initial full copy)
- Where to stream WAL from (ongoing replication)

The traffic path: `10.99.0.1:5432` [primaryhub VM WireGuard Ingress] → iptables PREROUTING DNAT on `primaryhub` host → `172.18.0.2:30432` [KinD Node Container - PostgreSQL NodePort] → `postgresql-primary-1` Pod on port `5432` [primaryhub Pod IP: 10.244.0.x:5432].

### 2.7.1 PostgreSQL Cross-Cluster Networking Data Path via Linux Kernel NAT

To establish continuous WAL streaming between `postgresql-secondary-1` on `secondaryhub` and `postgresql-primary-1` on `primaryhub`, the network packets traverse a multi-tier pipeline:

```
[postgresql-secondary-1 Pod] (secondaryhub Pod IP: 10.244.0.x)
       │
       │ TCP connect to 10.99.0.1:5432 [primaryhub VM WireGuard Ingress]
       ▼
[WireGuard wg0 Overlay] (10.99.0.0/24 [Inter-VM Mesh] - UDP :51820)
       │
       ▼ Arrives at hub1-vm interface wg0 [10.99.0.1 - primaryhub VM WireGuard IP]
[Linux Kernel Netfilter PREROUTING table] (hub1-vm Host)
  Rule: iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 5432 -j DNAT \
               --to-destination 172.18.0.2:30432 [KinD Node Container - PostgreSQL NodePort]
       │
       │ Hardware wire-speed packet header rewriting (< 0.2ms)
       │ Zero userspace daemons; conntrack tracks bidirectional state
       ▼
[Docker Bridge br-xxx] (hub1-vm Host Bridge: 172.18.0.1 -> 172.18.0.2)
       │
       ▼ Enters KinD container primaryhub-control-plane (172.18.0.2:30432 [KinD Node Container])
[Kube-Proxy iptables Engine inside KinD]
  Chain: KUBE-NODEPORTS -> KUBE-SVC-POSTGRESQL-REPL -> KUBE-SEP-*
       │
       ▼ DNATs to Pod IP (10.244.0.x:5432 [primaryhub PostgreSQL Pod IP])
[postgresql-primary-1 Pod] (primaryhub Pod IP: 10.244.0.x:5432)
```

#### Why the Legacy Systemd Socat Service (`primaryhub-pg-forward.service`) Was Permanently Eliminated:
1. **Userspace Latency & Context Switching**: `socat` required every database packet to transition from kernel space to user space, traverse the socat process buffer, and transition back to kernel space. This added ~0.8ms – 1.2ms latency to database transaction commits. Kernel NAT processes packets at Layer 3/4 inside the Linux kernel at wire speed (< 0.2ms).
2. **Daemon Crash Vulnerability**: Userspace `socat` processes can be killed by the Linux Out-Of-Memory (OOM) killer or hang on unclosed sockets. The Linux kernel Netfilter engine cannot crash independently of the OS.
3. **Clean Host OS Hygiene**: Eliminating `/etc/systemd/system/primaryhub-pg-forward.service` ensures the host VM's systemd directory remains 100% clean and unpolluted.
4. **Boot Auto-Recovery (Phase 4 Modernization)**: In Phase 4, ad-hoc host boot scripts were eliminated. Kernel NAT rules are persisted via standard `netfilter-persistent`, while KinD clusters declaratively map NodePort `30432` to host port `5432` via `extraPortMappings`.

### 2.8 Standby Schema Guard in sandbox-api

The `sandbox-api` microservice (`app_state.py`) runs this check at startup:

```python
cursor.execute("SELECT pg_is_in_recovery();")
is_standby = cursor.fetchone()[0]

if is_standby:
    # Skip all DDL migrations — they would fail with:
    # "ERROR: cannot execute CREATE TABLE in a read-only transaction"
    print("[startup] Standby mode — skipping schema migrations")
else:
    # Primary: run all CREATE TABLE IF NOT EXISTS migrations normally
    cursor.execute("CREATE TABLE IF NOT EXISTS api_keys (...)")
    cursor.execute("CREATE TABLE IF NOT EXISTS system_settings (...)")
    cursor.execute("CREATE TABLE IF NOT EXISTS rate_limits (...)")
```

Without this guard, `sandbox-api` on `secondaryhub` would crash at startup attempting DDL operations on a read-only database.

---

## 3. How Valkey Replication Works — Deep Technical Explanation

### 3.1 What Valkey Is & Why We Replaced Redis With It

**Valkey** is a high-performance, open-source, in-memory data store forked from Redis 7.2. It is an exact **100% binary and API drop-in replacement for Redis**, maintained under the **Linux Foundation** with the backing of AWS, Google Cloud, Oracle, Ericsson, and Snap.

#### The Origin: The Redis License Change
In March 2024, Redis Inc. abandoned the open-source BSD 3-Clause license and moved to restrictive dual proprietary licenses (RSALv2 and SSPLv1). This prohibited commercial redistribution and cloud hosting. In response, the open-source community created **Valkey** under the original permissive BSD license.

#### 100% Protocol & Client Compatibility
Valkey uses the identical Redis Serialization Protocol (RESP), listens on standard port `6379`, and supports all standard Redis commands (`GET`, `SET`, `SADD`, `INCR`, `PSYNC`). The existing `sandbox-api` microservice connects using the standard `redis-py` client library without requiring any application code modifications.

#### The 4 Critical Roles of Valkey in Our Sandbox Platform

1. **Fast-Path API Key & JWT JTI Validation (`active_api_keys`)**:
   - Querying PostgreSQL on every single API request creates severe database I/O bottlenecks.
   - Instead, `sandbox-api` caches valid API key IDs (JTIs) in an in-memory Valkey Set named `active_api_keys`.
   - Authentication lookups complete in **< 0.2 milliseconds** in memory.
   - When an API key is revoked, deleting it from Valkey triggers **instant, cluster-wide revocation**.

2. **Distributed API Rate Limiting (`ratelimit`)**:
   - Tracks rolling per-user and per-IP request counters using atomic `INCR` and `EXPIRE` operations.
   - If a client exceeds their tier limit (e.g. 60 requests/min), Valkey signals `sandbox-api` to immediately return **HTTP 429 (Too Many Requests)** without hitting PostgreSQL.

3. **Scan Job Tracking & Cascading Deletion (`job:{id}:child_jobs`)**:
   - In `file_scanner.py`, when a parent scan job generates multiple sub-tasks across spoke clusters, parent-to-child mappings are tracked in Valkey (`sadd f"job:{parent_id}:child_jobs"`).
   - If a scan is canceled midway, the system queries Valkey to cascade cancellations to all child tasks across `spoke1` and `spoke2`.

4. **Temporary Session State & Expiry Timers**:
   - Manages short-lived worker lease locks, session tokens, and background worker garbage collection timers (`expiry_checker.py`).

---

### 3.2 Working Principle of Cross-Cluster Valkey Memory Synchronization

Valkey achieves real-time state synchronization between `primaryhub` and `secondaryhub` using an **asynchronous, event-driven in-memory replication pipeline** combined with **Linux Kernel-level packet transformation (DNAT)** across the WireGuard encrypted mesh.

#### 1. Complete End-to-End Architectural Data Path

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                               PRIMARYHUB (10.99.0.1 [primaryhub VM WireGuard IP])           │
│                                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────────────────────┐   │
│   │ [Layer 1: Application Ingress]                                                      │   │
│   │ Client microservice (sandbox-api) performs write:                                   │   │
│   │   valkey_client.set("active_session:101", "valid")                                 │   │
│   └──────────────────────────────────────────┬──────────────────────────────────────────┘   │
│                                              │ Internal Pod Network (10.244.0.0/16 [Pod CIDR])
│                                              ▼                                              │
│   ┌─────────────────────────────────────────────────────────────────────────────────────┐   │
│   │ [Layer 2: Valkey Primary In-Memory Engine]                                          │   │
│   │  • Role: Master (RW) | slave-read-only: no                                          │   │
│   │  • Updates key-value hash table directly in RAM (< 0.1ms)                           │   │
│   │  • Increments replication byte counter: master_repl_offset += byte_len              │   │
│   │  • Appends command to Circular Replication Backlog (1MB memory buffer)              │   │
│   │  • Serializes command into RESP wire protocol:                                      │   │
│   │      *3\r\n$3\r\nSET\r\n$18\r\nactive_session:101\r\n$5\r\nvalid\r\n                │   │
│   └──────────────────────────────────────────┬──────────────────────────────────────────┘   │
│                                              │ Binds to container TCP :6379 [Pod Port]      │
│                                              ▼                                              │
│   ┌─────────────────────────────────────────────────────────────────────────────────────┐   │
│   │ [Layer 3: Kubernetes Service Abstraction]                                           │   │
│   │  • Service: redis-replication (Type: NodePort)                                      │   │
│   │  • NodePort: 30379 [KinD NodePort]                                                  │   │
│   │  • Handled by Kube-Proxy inside KinD container (172.18.0.2 [Node IP]) via iptables: │   │
│   │      KUBE-NODEPORTS -> KUBE-SVC-REDIS-REPL -> KUBE-SEP (Pod IP 10.244.0.22:6379)   │   │
│   └──────────────────────────────────────────▲──────────────────────────────────────────┘   │
│                                              │ Forwarded to NodePort 172.18.0.2:30379       │
│                                              │                                              │
│   ┌──────────────────────────────────────────┴──────────────────────────────────────────┐   │
│   │ [Layer 4: Linux Kernel Netfilter & NAT (hub1-vm Host)]                              │   │
│   │  • Zero-Userspace packet forwarding managed by /usr/local/bin/ocm-mesh-boot.sh      │   │
│   │  • Kernel Netfilter PREROUTING table intercepts TCP port 6379 from wg0:             │   │
│   │      iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 6379 \                  │   │
│   │               -j DNAT --to-destination 172.18.0.2:30379 [KinD Node Container]      │   │
│   │  • Hardware wire-speed packet rewrite; ZERO systemd forwarding daemons              │   │
│   └──────────────────────────────────────────▲──────────────────────────────────────────┘   │
│                                              │                                              │
└──────────────────────────────────────────────┼──────────────────────────────────────────────┘
                                               │
                               WireGuard Mesh  │ Persistent Bidirectional TCP Socket
                               (10.99.0.0/24   │ WireGuard wg0 overlay (port 51820 UDP)
                               [Inter-VM Mesh])│ Sub-millisecond latency (< 0.5ms)
                                               │
┌──────────────────────────────────────────────┴──────────────────────────────────────────────┐
│                              SECONDARYHUB (10.99.0.2 [secondaryhub VM WireGuard IP])        │
│                                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────────────────────┐   │
│   │ [Layer 5: Valkey Standby Replication Engine]                                        │   │
│   │  • Command: exec valkey-server --replicaof 10.99.0.1 6379                           │   │
│   │  • Role: Slave (RO) | slave-read-only: yes                                          │   │
│   │  • Initiates outbound socket connection to 10.99.0.1:6379 [primaryhub Ingress]      │   │
│   │  • Event Loop (epoll) reads incoming RESP byte stream from socket                   │   │
│   │  • Replays *3\r\n$3\r\nSET... directly into local RAM (< 0.1ms)                     │   │
│   │  • Updates slave_repl_offset to match master_repl_offset                            │   │
│   │  • Sends heartbeat every 1s: REPLCONF ACK <slave_repl_offset>                       │   │
│   └─────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                             │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

#### 2. Detailed Step-by-Step Working Principle

The synchronization between the two clusters follows an eight-stage sequence:

##### Step 1: Write Ingress & Memory Mutex on Primary
When an API client interacts with `primaryhub`:
1. The microservice (`sandbox-api`) executes a write command against `redis-service:6379` (e.g. `SET active_session:101 valid`).
2. Valkey's single-threaded core engine processes the command, acquires the dictionary lock, and updates the key-value structure in RAM.
3. The engine atomically increments its internal byte counter: `master_repl_offset += byte_length_of_command`.

##### Step 2: Protocol Serialization (RESP)
Valkey converts the executed command into the binary-safe **Redis Serialization Protocol (RESP)**:
```text
*3\r\n$3\r\nSET\r\n$18\r\nactive_session:101\r\n$5\r\nvalid\r\n
```
* `*3`: Array with 3 arguments.
* `$3\r\nSET\r\n`: Bulk string of length 3 containing the command name.
* `$18\r\nactive_session:101\r\n`: Bulk string of length 18 containing the key.
* `$5\r\nvalid\r\n`: Bulk string of length 5 containing the value.

##### Step 3: Circular Backlog Buffering
Before sending over the network, the serialized command is written into a 1MB circular in-memory ring buffer (**Replication Backlog Buffer**).
* **Purpose:** If network connectivity between `primaryhub` and `secondaryhub` briefly glitches, the primary does not need to recreate an entire disk snapshot (RDB dump). It uses this buffer to send only the delta upon reconnection (Partial Resynchronization).

##### Step 4: Kubernetes NodePort & `kube-proxy` Translation
The primary Valkey pod exposes port `6379`. Inside the KinD cluster:
1. The Kubernetes Service `redis-replication` defines `spec.type: NodePort` with port `30379` [KinD NodePort].
2. Internal `kube-proxy` writes iptables rules inside the KinD node container (`172.18.0.2` [KinD Docker Container IP]):
   ```text
   KUBE-NODEPORTS chain:
   tcp dpt:30379 [KinD NodePort] -> KUBE-SVC-REDIS-REPLICATION -> KUBE-SEP-* -> DNAT to Pod IP (10.244.0.22:6379 [primaryhub Valkey Pod IP])
   ```

##### Step 5: Linux Kernel Layer 3/4 DNAT Forwarding (Host VM)
Because KinD clusters run in isolated Docker containers, external traffic arriving on the VM's WireGuard interface (`wg0`, `10.99.0.1` [primaryhub VM WireGuard IP]) cannot reach the container without host-level routing.
* **Legacy Method (Eliminated):** Previously, a userspace `socat` daemon ran via `/etc/systemd/system/primaryhub-redis-forward.service`. This introduced userspace context switching overhead, process instability, and polluted host systemd directories.
* **Current Method (Linux Kernel NAT):** Packet rewriting happens directly inside the Linux Kernel via netfilter `PREROUTING`:
  ```bash
  iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 6379 -j DNAT \
           --to-destination 172.18.0.2:30379
  ```
  1. A TCP packet arrives on `hub1-vm` interface `wg0` with destination `10.99.0.1:6379` [primaryhub VM WireGuard Ingress].
  2. The Linux kernel Netfilter engine matches `! -i br-+` (not originating from Docker bridge) and `--dport 6379`.
  3. The kernel rewrites the destination IP/port to `172.18.0.2:30379` [KinD Node Container - Valkey NodePort] in hardware/kernel space.
  4. The host routing table forwards the packet over Docker bridge `br-xxx` (`172.18.0.1` -> `172.18.0.2`) to KinD.
  5. The return packets are automatically reverse-translated by the kernel's connection tracking table (`conntrack`).
* **Persistence:** This rule is baked into `/usr/local/bin/ocm-mesh-boot.sh`, ensuring it starts automatically on VM reboot alongside K8s API (`6443`), OCM (`8091`), etcd (`32379`), and PostgreSQL (`5432`).

##### Step 6: Encrypted WireGuard Overlay Mesh Transit
The secondary Valkey pod initiates and maintains the replication socket across the WireGuard tunnel:
* Source: `hub2-vm` / `secondaryhub` (`10.99.0.2` [secondaryhub VM WireGuard IP])
* Destination: `hub1-vm` / `primaryhub` (`10.99.0.1:6379` [primaryhub VM WireGuard Ingress])
* Transport: ChaCha20-Poly1305 encrypted UDP packets on port `51820`.
* Network latency between VMs is typically **< 0.3 milliseconds**.

##### Step 7: Standby Ingestion & Command Execution in Secondary RAM
The secondary Valkey process runs with `--replicaof 10.99.0.1 6379` [primaryhub VM WireGuard Ingress]:
1. Valkey's non-blocking event loop (`epoll`) wakes up as soon as bytes arrive on the TCP socket.
2. The RESP parser deserializes `*3\r\n$3\r\nSET...`.
3. The engine executes the command directly in its local memory table.
4. It increments its local `slave_repl_offset` by the byte length received.
5. Because no disk I/O or WAL flushes are required, memory replay takes **< 0.1 milliseconds**.

##### Step 8: Heartbeat & Repl-Offset Synchronization (`REPLCONF ACK`)
* Every 1000 milliseconds (1 second), the replica sends a TCP packet back to the primary:
  ```text
  REPLCONF ACK <current_slave_repl_offset>
  ```
* The primary updates its replica state table. When `master_repl_offset == slave_repl_offset`, replication lag is **0 bytes / 0 ms**.
* In `valkey-cli info replication`, `master_last_io_seconds_ago` stays at `0` or `1`, indicating an active, live memory link.

---

#### 3. Architectural Evolution: Userspace vs. Kernel NAT vs. Production K8s

The following table explains how this memory synchronization mechanism has evolved and how it compares to real production:

| Dimension | 1. Legacy Approach (Deprecated) | 2. Current Implementation (Active Sandbox) | 3. Pure Production K8s (Bare-Metal / Cloud) |
| :--- | :--- | :--- | :--- |
| **Mechanism** | Userspace `socat` forwarder | **Linux Kernel Netfilter NAT (`PREROUTING DNAT`)** | **Kubernetes-Native CNI / NodePort / MCS** |
| **Host Configuration** | `/etc/systemd/system/primaryhub-redis-forward.service` | Managed via declarative KinD `extraPortMappings` & `netfilter-persistent` (Phase 4) | **Zero host-level files or scripts** |
| **Execution Layer** | Userspace context switching (Kernel $\leftrightarrow$ `socat` $\leftrightarrow$ Kernel) | **Pure Linux Kernel Layer 3/4 packet rewrite** | Native `kube-proxy` or Cilium eBPF |
| **Reliability** | Vulnerable to daemon crashes, pid file locks | **Crash-proof** (Netfilter engine in Linux kernel) | Native Kubernetes reconciliation |
| **Systemd Footprint** | Polluted `/etc/systemd/system` | **100% Clean** (`Unit could not be found`) | 100% Clean |
| **Network Latency** | ~0.8ms - 1.2ms | **< 0.3ms (Wire speed)** | < 0.2ms (Direct CNI routing) |

---

#### 4. The 3-Phase Internal Replication Lifecycle

##### Phase 1: Handshake & Initial Full Sync (RDB Snapshot)
When the replica pod starts up for the first time:
1. Replica connects to `10.99.0.1:6379` over WireGuard and sends: `PSYNC ? -1` (*"I have no replication ID, give me full initial sync"*).
2. The primary forks an in-memory background process to dump the current dataset into a compact binary format (**RDB snapshot**).
3. While the snapshot is being generated, the primary opens a circular **Replication Backlog Buffer** (`repl_backlog_size = 1MB`) to hold any new incoming writes from client microservices.
4. Primary streams the RDB snapshot directly over the WireGuard TCP socket to the secondary.
5. Secondary flushes its own memory, loads the RDB snapshot into RAM, and then applies all buffered writes from the replication backlog.

##### Phase 2: Continuous Asynchronous Command Streaming
Once initialized, both instances maintain an open, persistent TCP connection.
Whenever a write occurs on `primaryhub`:
```text
sandbox-api executes: SADD active_api_keys "token_jti_9941"
```
1. Master executes `SADD` in its local RAM.
2. Master immediately translates the command into the standard RESP wire protocol format:
   ```text
   *3\r\n$4\r\nSADD\r\n$15\r\nactive_api_keys\r\n$14\r\ntoken_jti_9941\r\n
   ```
3. Master streams these bytes over the WireGuard socket to `secondaryhub`.
4. Secondary executes the exact same command in its local memory.
* **Latency:** Because memory operations require no disk I/O, latency across hubs is **sub-millisecond (< 0.5 ms)**.

##### Phase 3: Heartbeat & Offset Tracking (`REPLCONF ACK`)
* Every second, the secondary sends a heartbeat: `REPLCONF ACK <offset>`
* This confirms to the master that the replica has processed the replication stream up to byte offset X.
* When `master_repl_offset` matches `slave_repl_offset`, replication lag is **0 ms**.

---

#### 5. Live Replication Verification & Health Check

To verify that Valkey continuous memory replication is actively operating through the Linux Kernel NAT pipeline:

##### Verification 1: Inspect Replication Telemetry on Secondary
Run from `secondaryhub` (`ubuntu@192.168.101.20`):
```bash
kubectl exec -n opensandbox-system deploy/valkey -- valkey-cli info replication
```
**Expected Output:**
```text
# Replication
role:slave
master_host:10.99.0.1
master_port:6379
master_link_status:up
master_last_io_seconds_ago:1
master_sync_in_progress:0
slave_read_repl_offset:6120
slave_repl_offset:6120
slave_priority:100
slave_read_only:1
replica_announced:1
connected_slaves:0
master_failover_state:no-failover
master_replid:da4994cec3126e77fb90855c7a2e199064ec6a92
master_repl_offset:6120
repl_backlog_active:1
repl_backlog_size:1048576
```
Key indicators:
* `master_link_status: up`: The TCP connection over WireGuard through Kernel NAT is alive.
* `slave_read_only: 1`: Standby mode is strictly enforced; prevents split-brain.
* `master_last_io_seconds_ago: 0` or `1`: Active heartbeats received every second.
* `slave_repl_offset == master_repl_offset`: Zero replication lag.

##### Verification 2: End-to-End Real-Time Write/Read Test
Execute a write on `primaryhub` and immediately query it on `secondaryhub`:
```bash
# 1. Write key on primary:
kubectl exec -n opensandbox-system deploy/valkey -- valkey-cli set live_test_key "synced_via_kernel_nat"

# 2. Query key on secondary:
kubectl exec -n opensandbox-system deploy/valkey -- valkey-cli get live_test_key
```
**Expected Result:**
`synced_via_kernel_nat` returns instantly (< 1ms).

##### Verification 3: Confirm Kernel NAT Rule & Clean Systemd on Hub1
Run on `primaryhub` host (`ubuntu@192.168.100.20`):
```bash
# Verify kernel NAT rule:
sudo iptables -t nat -L PREROUTING -n -v | grep 6379
# Output:
# DNAT tcp -- !br-+ * 0.0.0.0/0 0.0.0.0/0 tcp dpt:6379 to:172.18.0.2:30379

# Confirm systemd is 100% clean:
systemctl status primaryhub-redis-forward.service
# Output:
# Unit primaryhub-redis-forward.service could not be found.
```

---

### 3.3 Deep Dive: Process Breakdown of `exec valkey-server --replicaof 10.99.0.1 6379`

In `values-secondary.yaml`, the replication configuration is declared as:

```yaml
valkey:
  replication:
    enabled: true
    role: "standby"
    primaryHost: "10.99.0.1"    # primaryhub WireGuard IP
    primaryPort: 6379
```

When Helm renders `valkey.yaml`, the secondary container boots with the command:

```bash
exec valkey-server --replicaof 10.99.0.1 6379
```

This single line executes three critical operations across Linux, container, and database layers:

#### 1. What `exec` Does (Linux & Kubernetes Process Management)
* Inside container entrypoint scripts (`/bin/sh`), the shell normally becomes Process ID 1 (`PID 1`).
* Using **`exec`** instructs the Linux kernel to **replace the shell process completely with `valkey-server`**, ensuring `valkey-server` runs as **PID 1**.
* **Kubernetes Resilience Rationale:** When Kubernetes terminates or restarts a pod during updates or node maintenance, it sends `SIGTERM` directly to PID 1. Without `exec`, the `/bin/sh` shell would swallow the signal, preventing graceful shutdown and causing Kubernetes to forcefully kill the pod (`SIGKILL`), risking uncommitted memory state loss.

#### 2. What `valkey-server` Does (Engine Boot)
* Spawns the Valkey in-memory storage engine.
* Allocates memory buffers and registers high-performance Linux event loops (`epoll`).
* Binds to port `6379` inside the pod, enabling local microservices on `secondaryhub` to execute read operations.

#### 3. What `--replicaof 10.99.0.1 6379` Does (Standby Activation)
Instructs Valkey not to start as an independent master, but to execute an automated **5-step standby sequence**:
1. **Enforces Read-Only Mode:** Sets `role: slave` and locks the instance into read-only mode (`slave-read-only yes`), blocking accidental local writes.
2. **Opens WireGuard Socket:** Initiates a persistent TCP connection to `10.99.0.1:6379`.
3. **Replication Handshake:** Exchanges `PING` $\rightarrow$ `PONG` and registers its listening port with the master.
4. **Memory Sync (`PSYNC`):** Downloads and loads the primary's memory snapshot directly into RAM.
5. **Continuous Stream Replay:** Enters infinite event-driven loop, immediately applying all incoming memory updates.

---

### 3.4 Why Replica Valkey Rejects Writes

Valkey replicas enforce protocol-level write protection (`slave-read-only yes`). Any write attempt returns:

```text
SET test_key 1
→ (error) READONLY You can't write against a read only replica.
```

This enforcement happens directly within the Valkey C engine — no application code, configuration flag, or environment variable can bypass it. Even if a microservice on `secondaryhub` mistakenly attempts to write to Valkey, the server rejects the command before it can execute, guaranteeing **zero split-brain memory corruption**.

---

### 3.5 Replication Backlog & Network Resilience

Valkey maintains a circular **replication backlog buffer** (`repl_backlog_size = 1MB`) on the master. If the WireGuard tunnel briefly drops or the secondary VM reboots:
1. The primary continues serving client writes, buffering each command in the circular backlog.
2. When the secondary reconnects, it issues: `PSYNC <master_replid> <last_known_offset>`.
3. Primary inspects its backlog buffer:
   * **If the offset is within the 1MB buffer (Partial Resync):** Primary sends `+CONTINUE` and streams only the missed byte delta. Memory catches up in **milliseconds** without needing an RDB dump.
   * **If the offset fell off the buffer (Full Resync):** Primary triggers a full RDB snapshot sync.

---

### 3.6 Failover & Promotion (`replicaof no one`)

When `primaryhub` fails and VIP traffic switches to `secondaryhub`:
1. The administrator or failover script issues:
   ```bash
   kubectl exec -n opensandbox-system deploy/valkey -c valkey -- valkey-cli replicaof no one
   ```
2. Valkey on `secondaryhub` immediately breaks the replica link and converts to an independent **Master** (`role: master`).
3. It unlocks write permissions (`slave_read_only: 0`).
4. Microservices on `secondaryhub` can now write session tokens, API rate limits, and scan job tracking records with **zero restart and zero downtime**.

---

### 3.7 Compatibility Aliases for Existing Microservices

The `sandbox-api` codebase was originally written to connect to a service named `redis-service:6379`. To preserve zero-code-change compatibility, the Helm chart creates a ClusterIP service named `redis-service` that points to the Valkey pod:

```text
On primaryhub:   redis-service:6379 → Valkey Master  (reads and writes accepted)
On secondaryhub: redis-service:6379 → Valkey Replica (reads accepted, writes rejected)
```

The application startup detects which mode Valkey is in:

```python
info = self.redis_client.info("replication")
is_redis_slave = info.get("role") == "slave"
if not is_redis_slave:
    # Sync active API key registry to Redis cache (master only)
    ...
else:
    print("[startup] Connected to Redis replica — skipping registry write sync")
```

---

### 3.8 Unified Linux Kernel NAT Architecture: Replacing Systemd Socat for Both PostgreSQL & Valkey

In this multi-cluster platform, both stateful datastores—**CloudNativePG (PostgreSQL 5432)** and **Valkey (6379)**—require low-latency, bidirectional cross-cluster TCP connectivity between `secondaryhub` and `primaryhub`.

Originally, these ports were forwarded on `hub1-vm` using userspace `socat` processes managed by custom systemd unit files:
* `/etc/systemd/system/primaryhub-redis-forward.service`
* `/etc/systemd/system/primaryhub-pg-forward.service`

**Both systemd services have been permanently removed, and all cross-cluster data plane traffic has been completely transitioned to Linux Kernel Netfilter NAT (`PREROUTING DNAT`).**

#### 1. Why the Transition Was Implemented (The 5 Core Rationale Factors)

1. **Elimination of Userspace Context Switching Overhead (`socat`)**:
   - **The Problem with Socat**: `socat` runs in Linux userspace. When a database packet arrives from WireGuard (`wg0`), the packet must transition from kernel space to user space into `socat`'s buffer, then transition back from user space to kernel space to be forwarded to the Docker bridge.
   - **The Kernel NAT Advantage**: Linux Kernel NAT (`iptables -t nat -A PREROUTING`) rewrites the TCP destination IP and port (`10.99.0.1:port` $\rightarrow$ `172.18.0.2:nodeport`) directly within the kernel's network subsystem (`netfilter`). It operates at hardware wire-speed with sub-millisecond latency (< 0.2ms) and zero CPU context switching.

2. **Elimination of Daemon Fragility & Crashes**:
   - Userspace `socat` daemons are individual processes subject to:
     - Out-Of-Memory (OOM) killer termination during high memory spikes.
     - Unhandled socket drops or PID file locks.
     - Failure to reconnect if the KinD container restarts or changes sockets.
   - The Linux Kernel Netfilter engine is embedded directly in the Linux OS kernel. It has zero process ID, zero memory leak risk, cannot crash, and natively utilizes Linux connection tracking (`conntrack`) to automatically reverse-translate return packets.

3. **100% Clean Host Systemd Hygiene (IaC Principles)**:
   - Having ad-hoc systemd unit files (`primaryhub-*.service`) scattered across the host OS creates configuration drift, violates Infrastructure-as-Code (IaC) principles, and clutters host system administration.
   - Removing these services leaves `/etc/systemd/system/` completely clean.

4. **Solving the Submariner KinD Duplicate IP Collision**:
   - In cloud or bare-metal Kubernetes environments, Submariner connects pod networks across clusters natively.
   - However, in this sandbox, all KinD clusters run inside Docker containers on their respective VMs, and Docker automatically assigned the identical container node IP (`172.18.0.2`) to both `primaryhub` and `secondaryhub`.
   - Submariner's IPsec cable driver (Libreswan) throws an error (`whack exit status 20`) because an IPsec endpoint cannot peer with its own identical IP (`172.18.0.2 <-> 172.18.0.2`).
   - Linux Kernel NAT solves this elegantly by routing packets across the host VMs' unique WireGuard overlay IPs (`10.99.0.1` vs `10.99.0.2`) directly into KinD NodePorts.

5. **100% Automated Multi-VM Portability (Phase 4)**:
   - Setting up `socat` systemd services manually on new sets of VMs is tedious, error-prone, and requires multi-step manual intervention.
   - In Phase 4, ad-hoc boot scripts were retired in favor of native WireGuard `PostUp`/`PreDown` policy routing, `netfilter-persistent`, and KinD `extraPortMappings` inside [`multi-cluster-sync.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/multi-cluster-sync.sh).

#### 2. Master Unified Ingress Routing Matrix on `hub1-vm`

All cross-cluster traffic arriving on WireGuard overlay IP `10.99.0.1` [primaryhub VM WireGuard IP] is managed natively via Linux Kernel NAT & KinD extraPortMappings:

| Traffic Type | Ingress WireGuard Port | Forwarded Destination | Protocol | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| **Kubernetes API** | `10.99.0.1:6443` [primaryhub Ingress] | `172.18.0.2:6443` [KinD API Server Container] | TCP | Remote `kubectl`, Spoke Cluster Registration, Submariner Broker |
| **OCM Hub API** | `10.99.0.1:8091` [primaryhub Ingress] | `172.18.0.2:8091` [KinD OCM Hub Container] | TCP | Spoke Klusterlet Registration Agent Ingress |
| **etcd / Cilium** | `10.99.0.1:32379` [primaryhub Ingress] | `172.18.0.2:32379` [KinD etcd / Cilium Container] | TCP | Cilium ClusterMesh / etcd Synchronization |
| **Valkey Replication** | `10.99.0.1:6379` [primaryhub Ingress] | `172.18.0.2:30379` [KinD Valkey NodePort] | TCP | In-Memory Master-Replica Stream (RESP protocol) |
| **PostgreSQL Replication**| `10.99.0.1:5432` [primaryhub Ingress] | `172.18.0.2:30432` [KinD PostgreSQL NodePort] | TCP | CloudNativePG Physical WAL Streaming & `pg_basebackup` |

#### 3. Automated Multi-VM Portability Runbook (`install-auto-recovery.sh`)

When deploying this architecture on a new set of VMs:
1. All rules are generated dynamically:
   ```bash
   DOCKER_IP=$(docker inspect $CONTAINER --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' 2>/dev/null || echo '172.18.0.2')
   ```
2. Any legacy socat services are automatically detected, stopped, and removed:
   ```bash
   systemctl stop primaryhub-redis-forward.service primaryhub-pg-forward.service socat-redis.service socat-pg.service 2>/dev/null || true
   rm -f /etc/systemd/system/primaryhub-redis-forward.service /etc/systemd/system/primaryhub-pg-forward.service 2>/dev/null || true
   ```
3. The script configures `/usr/local/bin/ocm-mesh-boot.sh` and enables the single unified systemd auto-recovery unit `/etc/systemd/system/ocm-mesh-boot.service`.
4. Result: **Zero manual configuration needed when running on any new environment.**

---

## 4. How the Helm Chart, CRDs, and Operators are Structured

### 4.1 The Umbrella Chart (`codeInspector`)

The `codeInspector` directory is an **umbrella Helm chart** — a parent chart that packages multiple subcharts as dependencies:

```
codeInspector/
├── Chart.yaml              ← lists all subchart dependencies
├── Chart.lock              ← pinned dependency versions
├── values.yaml             ← PRIMARY hub profile: active RW master
├── values-secondary.yaml   ← SECONDARY hub profile: warm standby RO
├── crds/                   ← ⚠️ AUTO-INSTALLED by Helm BEFORE any templates
│   ├── cloudnative-pg-crds.yaml   (Cluster, Pooler, ScheduledBackup, Backup CRDs)
│   ├── opensandbox-crds.yaml      (BatchSandbox, Pool CRDs)
│   ├── gateway-api-crds.yaml      (HTTPRoute, Gateway, GatewayClass CRDs)
│   ├── agentgateway-crds.yaml     (AgentGateway-specific CRDs)
│   ├── metallb-crds.yaml          (IPAddressPool, L2Advertisement CRDs)
│   └── sealed-secrets-crd.yaml    (SealedSecret CRD)
└── charts/                 ← bundled subchart directories
    ├── apiServer/           ← sandbox-api Deployment, CNPG Cluster CRs, Valkey, RabbitMQ
    ├── cloudnative-pg/      ← CNPG Operator Deployment (the controller)
    ├── opensandbox/         ← opensandbox-server and opensandbox-controller
    ├── agentgateway/        ← AgentGateway proxy deployment
    ├── agentgateway-controller/
    └── sealed-secrets/
```

### 4.2 How CRDs Are Automatically Installed

**This is the most important thing to understand about Helm and CRDs:**

> **Helm v3 automatically applies all YAML files inside a chart's top-level `crds/` directory BEFORE rendering and applying any templates.**

When you run:

```bash
helm upgrade --install codeinspector ./codeInspector -n opensandbox-system ...
```

Helm executes in this **exact order**:

```
STEP 1 — CRD Installation (codeInspector/crds/):
  kubectl apply -f codeInspector/crds/cloudnative-pg-crds.yaml
  kubectl apply -f codeInspector/crds/opensandbox-crds.yaml
  kubectl apply -f codeInspector/crds/gateway-api-crds.yaml
  kubectl apply -f codeInspector/crds/agentgateway-crds.yaml
  kubectl apply -f codeInspector/crds/metallb-crds.yaml
  kubectl apply -f codeInspector/crds/sealed-secrets-crd.yaml
  ↑ All 6 files are applied before any templates run.

STEP 2 — Template Rendering:
  Helm processes values.yaml / values-secondary.yaml
  Renders conditional logic (primary vs standby cluster CR, etc.)

STEP 3 — Workload Application:
  kubectl apply all rendered manifests:
  - CNPG Operator Deployment
  - CNPG Cluster CR (postgresql-primary or postgresql-secondary)
  - Valkey Deployment
  - sandbox-api Deployment
  - etc.
```

Because CRDs are installed in Step 1, by the time the CNPG `Cluster` Custom Resource (which uses `apiVersion: postgresql.cnpg.io/v1`) is applied in Step 3, the Kubernetes API server already knows what `postgresql.cnpg.io/v1/Cluster` is and can validate and store it.

**You do NOT need to manually run `kubectl apply -f crds/` before running Helm.** Helm handles it automatically.

> **Why are the CRDs in `codeInspector/crds/` and not inside `cloudnative-pg/crds/`?**
> Helm only auto-installs CRDs from the **root chart's** `crds/` directory. CRDs inside **subchart** `crds/` directories are completely ignored by Helm. Since `cloudnative-pg` is a subchart, its CRDs would never be installed automatically. We extracted them into `codeInspector/crds/` (root chart) to guarantee auto-installation.

### 4.3 What Each CRD File Registers

| File | CRDs Registered | Used By |
|:---|:---|:---|
| `cloudnative-pg-crds.yaml` | `Cluster`, `Pooler`, `ScheduledBackup`, `Backup` | CNPG Operator and `cnpg-cluster.yaml` template |
| `opensandbox-crds.yaml` | `BatchSandbox`, `Pool` | opensandbox-controller |
| `gateway-api-crds.yaml` | `Gateway`, `GatewayClass`, `HTTPRoute`, `TCPRoute` | agentgateway |
| `agentgateway-crds.yaml` | AgentGateway-specific routing CRDs | agentgateway-controller |
| `metallb-crds.yaml` | `IPAddressPool`, `L2Advertisement` | MetalLB load balancer |
| `sealed-secrets-crd.yaml` | `SealedSecret` | sealed-secrets controller |

### 4.4 `Chart.yaml` Dependency Declaration

```yaml
# codeInspector/Chart.yaml
apiVersion: v2
name: codeInspector
description: All-in-one Helm chart for deploying CodeInspector components
type: application
version: 0.1.0
appVersion: "1.0.0"

dependencies:
  - name: agentgateway
    version: 0.1.0
    condition: agentgateway.enabled       # Skip if agentgateway.enabled: false
  - name: agentgateway-controller
    version: v1.0.1
    condition: agentgateway-controller.enabled
  - name: apiServer
    version: 0.1.0
    condition: apiServer.enabled          # sandbox-api, CNPG CRs, Valkey, RabbitMQ
  - name: cloudnative-pg
    version: 0.29.0
    condition: cloudnative-pg.enabled     # CNPG Operator controller
  - name: opensandbox
    version: 0.1.0
    condition: opensandbox.enabled
  - name: sealed-secrets
    version: 2.5.19
    condition: sealed-secrets.enabled
```

Each `condition` key lets you disable entire subcharts by setting the corresponding value to `false`. For example, to deploy without the AgentGateway: `agentgateway.enabled: false`.

### 4.5 How Values Flow from Parent to Subcharts

Values are scoped by the subchart name prefix. In `values.yaml`:

```yaml
apiServer:           # ← These values are passed to charts/apiServer/
  cnpg:
    enabled: true
    replication:
      role: "primary"

cloudnative-pg:      # ← These values are passed to charts/cloudnative-pg/
  enabled: true
  crds:
    create: false    # We manage CRDs ourselves in codeInspector/crds/
```

Inside `charts/apiServer/templates/cnpg-cluster.yaml`, you access values as `{{ .Values.cnpg.replication.role }}` — Helm automatically strips the `apiServer.` prefix when passing values to the subchart.

---

## 5. Service Replication Matrix & Write Behavior

### 5.1 Component Roles on Each Hub

| Service / Component | Role on Primary | Role on Secondary | Replication Mechanism |
|:---|:---|:---|:---|
| **PostgreSQL (CNPG Operator)** | **Active Master Cluster (`postgresql-primary`)** | **Replica Cluster (`postgresql-secondary`)** | **Physical WAL Streaming**: Continuous real-time sync of all databases, tables, sequences, and indexes. Auto-bootstrapped via `pg_basebackup`. |
| **Valkey (`7.2-alpine`)** | **Master (RW)** | **Replica (`--replicaof`) (RO)** | **Master-Replica Memory Mirroring**: Streams JWT tokens, session states, rate-limit counters in real time. |
| **`sandbox-api`** | **Active (RW)** | **Warm Standby (RO)** | Stateless microservice. Detects standby mode via `pg_is_in_recovery()`, skips DDL migrations, serves read queries. |
| **`opensandbox-server`** | **Active** | **Warm Standby** | Stateless controller. Running at `replicaCount: 1`, connected to local storage and OCM placement. |
| **`opensandbox-controller`** | **Active** | **Active Standby** | Watches Custom Resources (`BatchSandbox`, `Pool`) in both clusters. |
| **RabbitMQ** | **Independent Broker** | **Independent Broker** | Handles transient async job queues. In failover, traffic shifts to secondary's broker. |
| **Spoke Clusters (OCM)** | **Primary Hub** | **Secondary Hub** | **Dual Registration (Klusterlet)**: Spokes registered to both hubs for redundancy. |

### 5.2 Write Behavior on Secondary Hub

**Direct writes on `secondaryhub` are strictly rejected at the database/cache engine level:**

```sql
-- PostgreSQL on postgresql-secondary-1:
INSERT INTO api_keys(id, name) VALUES('test', 'test');
-- ERROR:  cannot execute INSERT in a read-only transaction
-- (Same for UPDATE, DELETE, CREATE TABLE, ALTER TABLE, DROP TABLE)
```

```bash
# Valkey on secondaryhub:
valkey-cli set test_key 1
# READONLY You can't write against a read only replica.
```

This enforcement is at the **protocol level** — no application code, configuration flag, or environment variable can bypass it. It is built into the PostgreSQL recovery state machine and the Valkey `replica-read-only` configuration.

**Why this is critical**: Allowing both hubs to accept writes simultaneously without a distributed consensus engine (like Raft or Paxos) leads to **split-brain** — irreversible data corruption where rows, API keys, and sequences diverge permanently. By keeping `secondaryhub` strictly read-only, `primaryhub` is the undisputed Single Source of Truth.

---

## 6. Complete Ordered Installation Guide

> **Important**: Read this section fully before touching any cluster. The order matters — `secondaryhub` requires `primaryhub` to be running and reachable before it can bootstrap its replica cluster.

### Phase 0: Pre-Flight Checklist

Verify these conditions before beginning installation on either hub.

#### 0.1 WireGuard Tunnel Active

Both VMs must be connected via WireGuard (`wg0`):

```bash
# From primaryhub (192.168.100.20):
ping -c 3 10.99.0.2    # Must reach secondaryhub WireGuard IP

# From secondaryhub (192.168.101.20):
ping -c 3 10.99.0.1    # Must reach primaryhub WireGuard IP
```

Expected: `3 packets transmitted, 3 received, 0% packet loss, time ~0.5ms`

If ping fails, check `wg show` on both VMs and verify the WireGuard peers are configured.

#### 0.2 Kind Clusters Running

```bash
# On each VM (run on primaryhub AND secondaryhub separately):
kind get clusters
# Expected output on primaryhub: primaryhub
# Expected output on secondaryhub: secondaryhub

docker ps --format "table {{.Names}}\t{{.Status}}"
# Expected: primaryhub-control-plane   Up X hours
#           (or secondaryhub-control-plane on secondaryhub)
```

#### 0.3 kubectl Connectivity

```bash
kubectl cluster-info
# Expected: Kubernetes control plane is running at https://127.0.0.1:...

kubectl get nodes
# Expected: primaryhub-control-plane   Ready
```

#### 0.4 iptables DNAT Rules for Replication Ports (on primaryhub ONLY)

Kind's cluster node runs inside a Docker container at `172.18.0.2` [KinD Control-Plane Docker Container IP]. When `secondaryhub` needs to connect to `primaryhub`'s PostgreSQL or Valkey for replication, the traffic arrives on `primaryhub`'s WireGuard interface (`10.99.0.1` [primaryhub VM WireGuard IP]) and must be DNAT-forwarded to the Kind container's NodePorts (`172.18.0.2:30432` [PostgreSQL NodePort] and `172.18.0.2:30379` [Valkey NodePort]).

```bash
# On primaryhub — check if DNAT rules exist:
sudo iptables -t nat -L PREROUTING -n --line-numbers | grep -E "5432|6379"
```

If rules are **missing**, add them:

```bash
# 1. Remove any legacy socat/forward services:
sudo systemctl stop primaryhub-redis-forward.service primaryhub-pg-forward.service socat-pg.service socat-redis.service 2>/dev/null || true
sudo systemctl disable primaryhub-redis-forward.service primaryhub-pg-forward.service socat-pg.service socat-redis.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/primaryhub-redis-forward.service /etc/systemd/system/primaryhub-pg-forward.service 2>/dev/null || true
sudo systemctl daemon-reload

# 2. Add kernel-level DNAT rules (if not already applied):
# PostgreSQL: WireGuard port 5432 [primaryhub Ingress] → Kind NodePort 30432 [KinD Container 172.18.0.2]
sudo iptables -t nat -C PREROUTING ! -i br-+ -p tcp --dport 5432 -j DNAT --to-destination 172.18.0.2:30432 2>/dev/null || \
sudo iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 5432 -j DNAT --to-destination 172.18.0.2:30432

# Valkey: WireGuard port 6379 [primaryhub Ingress] → Kind NodePort 30379 [KinD Container 172.18.0.2]
sudo iptables -t nat -C PREROUTING ! -i br-+ -p tcp --dport 6379 -j DNAT --to-destination 172.18.0.2:30379 2>/dev/null || \
sudo iptables -t nat -A PREROUTING ! -i br-+ -p tcp --dport 6379 -j DNAT --to-destination 172.18.0.2:30379

# 3. Persistence across reboots:
# In Phase 4, these rules are persisted natively via netfilter-persistent:
# sudo netfilter-persistent save  (saves to /etc/iptables/rules.v4)
# WireGuard return-path routing is managed by PostUp/PreDown in /etc/wireguard/wg0.conf.
```

> **Why `! -i br-+`**: Excludes traffic already inside the Docker bridge network from being DNAT'd again. Only external traffic (from WireGuard) is redirected.
> **Portability Tip**: When provisioning a fresh multi-cluster sandbox, running `install-auto-recovery.sh` automatically configures both rules dynamically without manual intervention.

Test connectivity **from `secondaryhub`** after `primaryhub` Helm is deployed (Step 1.8):

```bash
nc -zv 10.99.0.1 5432   # PostgreSQL replication port [primaryhub VM WireGuard Ingress]
nc -zv 10.99.0.1 6379   # Valkey replication port [primaryhub VM WireGuard Ingress]
```

---

### Quick Start: Modernized End-to-End Bootstrap (How to Start)

> [!TIP]
> **Automated Multi-Cluster Deployment**: Instead of manual step-by-step VM configuration, the entire topology (WireGuard mesh, KinD clusters with declarative port mappings, OCM, Envoy active-passive gateway, and HA databases) can be bootstrapped automatically in 6 steps:
>
> 1. **Configure `cluster.env`**:
>    ```bash
>    cat << 'EOF' > cluster.env
>    GATEWAY_IP="192.168.100.10"
>    HUB1_IP="192.168.100.20"
>    HUB2_IP="192.168.101.20"
>    SPOKE1_IP="192.168.102.20"
>    SPOKE2_IP="192.168.103.20"
>    SSH_USER="ubuntu"
>    SSH_KEY="/home/berrybytes/.ssh/kamal-kvm"
>    EOF
>    ```
> 2. **Run Master Infrastructure Bootstrap**:
>    ```bash
>    ./multi-cluster-sync.sh --env-file ./cluster.env
>    ```
> 3. **Deploy Envoy Active Gateway on `gateway-vm`**:
>    ```bash
>    ssh -i ~/.ssh/kamal-kvm ubuntu@192.168.100.10 "mkdir -p ~/gateway"
>    scp -i ~/.ssh/kamal-kvm codeInspector/gateway/* ubuntu@192.168.100.10:~/gateway/
>    ssh -i ~/.ssh/kamal-kvm ubuntu@192.168.100.10 "bash ~/gateway/deploy-gateway.sh"
>    ```
> 4. **Deploy PrimaryHub Stack (`hub1-vm`)**:
>    ```bash
>    ssh -i ~/.ssh/kamal-kvm ubuntu@192.168.100.20 "helm upgrade --install codeinspector ~/01-Sandbox/codeInspector/charts/apiServer -f ~/01-Sandbox/codeInspector/values.yaml -n opensandbox-system --create-namespace"
>    ```
> 5. **Deploy SecondaryHub Stack + Controller (`hub2-vm`)**:
>    ```bash
>    ssh -i ~/.ssh/kamal-kvm ubuntu@192.168.101.20 "helm upgrade --install codeinspector ~/01-Sandbox/codeInspector/charts/apiServer -f ~/01-Sandbox/codeInspector/values-secondary.yaml -n opensandbox-system --create-namespace"
>    ```
> 6. **Detailed Documentation**: For step-by-step live verification and split-brain failover testing, see [`multi-cluster-automated-failover-split-brain-safe-failback.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/01-sandbox-multi-cluster/multi-cluster-automated-failover-split-brain-safe-failback.md).

---

### Phase 1: Install Primary Hub (Manual Step-by-Step Reference)

Execute **all steps in order** on `primaryhub` (`ubuntu@192.168.100.20`).

#### Step 1.1: Install Helm v3

```bash
curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
chmod 700 get_helm.sh
./get_helm.sh

# Verify installation:
helm version
# Expected: version.BuildInfo{Version:"v3.x.x", ...}
```

#### Step 1.2: Clone/Sync the Repository

```bash
cd ~
# First time:
git clone <your-repo-url> 01-Sandbox
# Or update existing:
cd ~/01-Sandbox && git pull origin main
```

#### Step 1.3: Pre-pull Container Images into Kind

Kind uses `containerd` as its container runtime. Pre-pulling images prevents timeouts during Helm deployment:

```bash
# CloudNativePG Operator controller image:
docker exec primaryhub-control-plane \
  crictl pull ghcr.io/cloudnative-pg/cloudnative-pg:1.23.0

# PostgreSQL 15.6 database image (managed by CNPG Operator):
docker exec primaryhub-control-plane \
  crictl pull ghcr.io/cloudnative-pg/postgresql:15.6

# Valkey in-memory cache:
docker exec primaryhub-control-plane \
  crictl pull docker.io/valkey/valkey:7.2-alpine

# RabbitMQ message broker:
docker exec primaryhub-control-plane \
  crictl pull docker.io/library/rabbitmq:3.13-management-alpine

# Application microservice images:
docker exec primaryhub-control-plane \
  crictl pull docker.io/01community/01sandbox-api:v0.7.9
docker exec primaryhub-control-plane \
  crictl pull docker.io/01community/01sandbox-opensandbox-server:v0.7.10-ocm
docker exec primaryhub-control-plane \
  crictl pull docker.io/01community/01sandbox-opensandbox-controller:v0.7.3
```

#### Step 1.4: Deploy the Primary Hub Helm Release

```bash
cd ~/01-Sandbox

helm upgrade --install codeinspector ./codeInspector \
  --namespace opensandbox-system \
  --create-namespace \
  --values ./codeInspector/values.yaml
```

**What happens during this command (in exact order):**

1. **Helm installs CRDs** from `codeInspector/crds/` (all 6 files, before any other resource):
   - `cloudnative-pg-crds.yaml` → registers `Cluster`, `Pooler`, `ScheduledBackup`, `Backup` CRDs
   - `opensandbox-crds.yaml` → registers `BatchSandbox`, `Pool` CRDs
   - `gateway-api-crds.yaml` → registers `HTTPRoute`, `Gateway`, `GatewayClass` CRDs
   - `agentgateway-crds.yaml`, `metallb-crds.yaml`, `sealed-secrets-crd.yaml`
2. **Helm renders templates** with `values.yaml` values (substitutes `role: primary`, etc.)
3. **Helm applies workloads**:
   - CNPG Operator Deployment starts → watches for `Cluster` CRs
   - CNPG `Cluster` CR `postgresql-primary` is applied → operator bootstraps the database
   - Valkey Deployment starts in **master mode** (no `--replicaof`)
   - NodePort Services `postgresql-replication` (30432) and `redis-replication` (30379) are created
   - `sandbox-api`, `opensandbox-server`, `opensandbox-controller` Deployments are applied
   - RabbitMQ is deployed

#### Step 1.5: Wait for All Pods to be Running

```bash
kubectl get pods -n opensandbox-system -w
# Press Ctrl+C when all are Running
```

Expected final state:

```
NAME                                                     READY   STATUS    RESTARTS   AGE
codeinspector-agentgateway-controller-79c6f549df-cd7mm   1/1     Running   0          3m
codeinspector-cloudnative-pg-5c585f7b58-nssq9            1/1     Running   0          3m
codeinspector-sealed-secrets-57dc877cb9-4p847            1/1     Running   0          3m
opensandbox-controller-86c99b4948-mnd7z                  1/1     Running   0          3m
opensandbox-server-b49897c6b-q9gps                       1/1     Running   0          3m
postgresql-primary-1                                     1/1     Running   0          2m
rabbitmq-5685746466-sp7qk                                1/1     Running   0          3m
sandbox-api-5c454bb69-sr5dt                              1/1     Running   0          3m
valkey-984b6d658-stz27                                   1/1     Running   0          3m
```

> `postgresql-primary-1` is created by the CNPG Operator after it reads the `Cluster` CR. It appears 30–60 seconds after other pods.

#### Step 1.6: Verify Primary PostgreSQL is Healthy

```bash
kubectl get cluster -n opensandbox-system postgresql-primary
```

Expected:

```
NAME                 AGE   INSTANCES   READY   STATUS                     PRIMARY
postgresql-primary   3m    1           1       Cluster in healthy state   postgresql-primary-1
```

```bash
kubectl exec -n opensandbox-system postgresql-primary-1 -- \
  psql 'postgresql://postgres:password123@127.0.0.1:5432/apikeys' -c \
  "SELECT current_database(), pg_is_in_recovery(), version();"
```

Expected:

```
 current_database | pg_is_in_recovery |        version
------------------+-------------------+------------------------
 apikeys          | f                 | PostgreSQL 15.6 on ...
```

`pg_is_in_recovery = f` (false) = **active read-write master**.

#### Step 1.7: Verify Primary Valkey is in Master Mode

```bash
kubectl exec -n opensandbox-system \
  $(kubectl get pod -n opensandbox-system -l app=valkey -o jsonpath='{.items[0].metadata.name}') -- \
  valkey-cli info replication | grep -E "^role|connected_slaves"
```

Expected:

```
role:master
connected_slaves:0   # 0 until secondaryhub is deployed
```

#### Step 1.8: Verify NodePort Replication Services Exist

```bash
kubectl get svc -n opensandbox-system | grep replication
```

Expected:

```
postgresql-replication   NodePort   10.x.x.x   <none>   5432:30432/TCP   3m
redis-replication        NodePort   10.x.x.x   <none>   6379:30379/TCP   3m
```

> **`primaryhub` is now fully deployed and ready for `secondaryhub` to replicate from it.**

---

### Phase 2: Install Secondary Hub (Warm Standby)

Execute **all steps in order** on `secondaryhub` (`ubuntu@192.168.101.20`).

> **Prerequisite**: Phase 1 must be fully complete. `primaryhub`'s PostgreSQL must be `Running` and `Cluster in healthy state` before proceeding.

#### Step 2.1: Install Helm v3

```bash
curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
chmod 700 get_helm.sh && ./get_helm.sh
```

#### Step 2.2: Clone/Sync the Repository

```bash
cd ~
git clone <your-repo-url> 01-Sandbox
# or: cd ~/01-Sandbox && git pull
```

#### Step 2.3: Verify Connectivity to Primary Hub Replication Ports

Before deploying, confirm that `secondaryhub` can reach `primaryhub`'s replication endpoints:

```bash
nc -zv 10.99.0.1 5432
# Expected: Connection to 10.99.0.1 5432 port [tcp/postgresql] succeeded!

nc -zv 10.99.0.1 6379
# Expected: Connection to 10.99.0.1 6379 port [tcp] succeeded!
```

If these fail: check Phase 0 Step 0.4 — the iptables DNAT rules on `primaryhub` must be in place and `primaryhub`'s Helm release must be deployed.

#### Step 2.4: Pre-pull Container Images into Kind

```bash
docker exec secondaryhub-control-plane \
  crictl pull ghcr.io/cloudnative-pg/cloudnative-pg:1.23.0
docker exec secondaryhub-control-plane \
  crictl pull ghcr.io/cloudnative-pg/postgresql:15.6
docker exec secondaryhub-control-plane \
  crictl pull docker.io/valkey/valkey:7.2-alpine
docker exec secondaryhub-control-plane \
  crictl pull docker.io/library/rabbitmq:3.13-management-alpine
docker exec secondaryhub-control-plane \
  crictl pull docker.io/01community/01sandbox-api:v0.7.9
docker exec secondaryhub-control-plane \
  crictl pull docker.io/01community/01sandbox-opensandbox-server:v0.7.10-ocm
docker exec secondaryhub-control-plane \
  crictl pull docker.io/01community/01sandbox-opensandbox-controller:v0.7.3
```

#### Step 2.5: Understand `values-secondary.yaml`

The key differences from the primary profile:

```yaml
# codeInspector/values-secondary.yaml — key sections

apiServer:
  cnpg:
    replication:
      role: "standby"           # ← Renders postgresql-secondary Cluster CR (pg_basebackup)
      primaryHost: "10.99.0.1"  # ← WireGuard IP of primaryhub
      primaryPort: 5432

  valkey:
    replication:
      role: "standby"           # ← Starts Valkey with --replicaof
      primaryHost: "10.99.0.1"  # ← WireGuard IP of primaryhub Valkey
      primaryPort: 6379
```

These two `role: "standby"` values are what make this deployment a warm standby instead of an independent master.

#### Step 2.6: Deploy the Secondary Hub Helm Release

```bash
cd ~/01-Sandbox

helm upgrade --install codeinspector ./codeInspector \
  --namespace opensandbox-system \
  --create-namespace \
  --values ./codeInspector/values-secondary.yaml
```

**What happens (in order):**

1. Helm installs all 6 CRD files from `codeInspector/crds/` (idempotent — safe to re-apply)
2. CNPG Operator Deployment starts on `secondaryhub`
3. CNPG Operator reads `postgresql-secondary` Cluster CR:
   - Detects `bootstrap.pg_basebackup.source: postgresql-primary`
   - Connects to `10.99.0.1:5432` → runs `pg_basebackup` → copies entire PGDATA to `secondaryhub` PVC (30–120 seconds)
   - Starts `postgresql-secondary-1` in recovery mode
   - WAL receiver opens, continuous streaming begins
4. Valkey Deployment starts with `--replicaof 10.99.0.1 6379`:
   - Connects to `10.99.0.1:6379` → full RDB snapshot transfer → continuous replication
5. `sandbox-api` starts → detects `pg_is_in_recovery() = true` → skips DDL, enters warm standby mode

#### Step 2.7: Monitor the pg_basebackup Bootstrap

```bash
# Watch CNPG Operator logs:
kubectl logs -n opensandbox-system deployment/codeinspector-cloudnative-pg -f
# Watch for: "pg_basebackup completed" then "WAL receiver started"

# Or watch cluster status:
kubectl get cluster -n opensandbox-system postgresql-secondary -w
```

Expected status progression:

```
postgresql-secondary   Setting up primary cluster connection
postgresql-secondary   Bootstrapping via pg_basebackup   ← takes 30–120s
postgresql-secondary   Cluster in healthy state           ← bootstrap complete
```

#### Step 2.8: Wait for All Pods Running

```bash
kubectl get pods -n opensandbox-system -w
```

Expected:

```
NAME                                                     READY   STATUS    RESTARTS   AGE
codeinspector-agentgateway-controller-...                1/1     Running   0          5m
codeinspector-cloudnative-pg-...                         1/1     Running   0          5m
codeinspector-sealed-secrets-...                         1/1     Running   0          5m
opensandbox-controller-...                               1/1     Running   0          5m
opensandbox-server-...                                   1/1     Running   0          5m
postgresql-secondary-1                                   1/1     Running   0          3m
rabbitmq-...                                             1/1     Running   0          5m
sandbox-api-...                                          1/1     Running   0          5m
valkey-...                                               1/1     Running   0          5m
```

#### Step 2.9: Verify Secondary PostgreSQL WAL Streaming

```bash
kubectl get cluster -n opensandbox-system postgresql-secondary
# Expected: Cluster in healthy state

kubectl logs -n opensandbox-system postgresql-secondary-1 --tail=10
# Expected: "started streaming WAL from primary at 0/8000000 on timeline 1"

kubectl exec -n opensandbox-system postgresql-secondary-1 -- \
  psql 'postgresql://postgres:password123@127.0.0.1:5432/apikeys' -c \
  "SELECT pg_is_in_recovery();"
# Expected: t  (true = standby mode confirmed)
```

#### Step 2.10: Verify Valkey Replication Active

```bash
kubectl exec -n opensandbox-system \
  $(kubectl get pod -n opensandbox-system -l app=valkey -o jsonpath='{.items[0].metadata.name}') -- \
  valkey-cli info replication
```

Expected output:

```
role:slave
master_host:10.99.0.1
master_port:6379
master_link_status:up      ← "up" = active replication
master_sync_in_progress:0  ← 0 = initial sync complete
slave_repl_offset:12345
```

> **`secondaryhub` is now fully deployed as a warm standby. All data from `primaryhub` is continuously replicated.**

---

### Phase 3: Install Spoke Clusters

Spoke clusters run code execution sandboxes dispatched via OCM `ManifestWork` from the hub(s). They do **not** run PostgreSQL or Valkey — they are pure compute nodes.

Execute the following on **each spoke VM** (`ubuntu@192.168.102.20` for `spoke1`, `ubuntu@192.168.103.20` for `spoke2`, etc.).

#### Step 3.1: Verify Spoke Kind Cluster is Running

```bash
kind get clusters            # Expected: spoke1
kubectl cluster-info         # Expected: control plane at https://127.0.0.1:...
kubectl get nodes            # Expected: spoke1-control-plane   Ready
```

#### Step 3.2: Register Spoke with Hub(s) via OCM

The klusterlet agent on each spoke registers with the hub. On **`primaryhub`**, approve the membership request:

```bash
kubectl get managedcluster
# Look for spoke1 with HUB ACCEPTED: false

kubectl patch managedcluster spoke1 \
  --type='json' \
  -p='[{"op":"replace","path":"/spec/hubAcceptsClient","value":true}]'

kubectl get managedcluster spoke1
# Expected: JOINED=True, AVAILABLE=True
```

#### Step 3.3: Create Namespace and Grant RBAC on Spoke

The OCM klusterlet agent needs permission to create resources in `opensandbox-workloads`. Run on the **spoke cluster**:

```bash
# Create dedicated workloads namespace:
kubectl create namespace opensandbox-workloads \
  --dry-run=client -o yaml | kubectl apply -f -

# Grant cluster-admin to the OCM klusterlet work service account:
kubectl create clusterrolebinding klusterlet-work-admin \
  --clusterrole=cluster-admin \
  --serviceaccount=open-cluster-management-agent-addon:klusterlet-work-sa \
  --dry-run=client -o yaml | kubectl apply -f -
```

> **Why cluster-admin?** The klusterlet creates `Pods`, `ConfigMaps`, and potentially `ServiceAccounts` inside `opensandbox-workloads`. Cluster-admin avoids permission gaps for any resource type the scanner pods need.

#### Step 3.4: Apply RuntimeClass Definitions

Scanner pods request `runtimeClassName: gvisor` (or `kata`, `kata-fc`). Apply the RuntimeClass definitions on the **spoke cluster**:

```bash
kubectl apply -f - <<'EOF'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
handler: runc
---
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: kata
handler: runc
---
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: kata-fc
handler: runc
EOF
```

> **Why `handler: runc`?** Inside nested Kind VMs, true gVisor/Kata hardware isolation is unavailable. Mapping to `runc` lets pods schedule successfully. The security boundary is provided by the scanner container image itself.

#### Step 3.5: Provision Swap Memory

Security scanners (Semgrep AST compilation) need more memory than typical workloads. Enable swap to prevent OOM kills:

```bash
swapon --show                         # Check if swap exists
sudo fallocate -l 2G /swapfile        # Create 2GB swap file
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab   # Persist
free -h                               # Verify: Swap: 2.0G
```

#### Step 3.6: Pre-pull Scanner Images on Spoke

```bash
docker exec spoke1-control-plane crictl pull docker.io/01community/01sandbox-scanner-python:1.0.0
docker exec spoke1-control-plane crictl pull docker.io/01community/01sandbox-scanner-go:1.0.0
docker exec spoke1-control-plane crictl pull docker.io/01community/01sandbox-scanner-java:1.0.0
docker exec spoke1-control-plane crictl pull docker.io/01community/01sandbox-scanner-node:1.0.0
docker exec spoke1-control-plane crictl pull docker.io/01community/01sandbox-scanner-k8s:1.0.0
docker exec spoke1-control-plane crictl pull docker.io/01community/01sandbox-scanner-rust:1.0.0
docker exec spoke1-control-plane crictl pull docker.io/01community/01sandbox-codeinterpreter:v0.7.10
```

#### Step 3.7: Verify Spoke Ready (from hub)

```bash
# On primaryhub:
kubectl get managedcluster
```

Expected:

```
NAME     HUB ACCEPTED   JOINED   AVAILABLE   AGE
spoke1   true           True     True        5m
spoke2   true           True     True        5m
```

---

## 7. Helm Chart Component Reference

### 7.1 CNPG Cluster Values

Configured under `apiServer.cnpg` in both `values.yaml` and `values-secondary.yaml`:

| Key | Primary Default | Secondary Override | Description |
|:---|:---|:---|:---|
| `enabled` | `true` | `true` | Deploy CNPG cluster CR |
| `instances` | `1` | `1` | Number of PostgreSQL pods |
| `imageName` | `ghcr.io/cloudnative-pg/postgresql:15.6` | (same) | PostgreSQL container image |
| `database` | `apikeys` | `apikeys` | Initial database to create |
| `user` | `postgres` | `postgres` | Superuser username |
| `password` | `password123` | `password123` | Superuser password — change in production! |
| `storage` | `5Gi` | `5Gi` | PVC size for PGDATA |
| `storageClassName` | `standard` | `standard` | StorageClass name |
| `replication.role` | `primary` | `standby` | `primary` = active master; `standby` = replica |
| `replication.primaryHost` | — | `10.99.0.1` | WireGuard IP of primaryhub (standby only) |
| `replication.primaryPort` | — | `5432` | PostgreSQL port on primaryhub (standby only) |

**Example: Increase storage to 20Gi:**
```yaml
apiServer:
  cnpg:
    storage: "20Gi"
```

**Example: Upgrade to PostgreSQL 16:**
```yaml
apiServer:
  cnpg:
    imageName: "ghcr.io/cloudnative-pg/postgresql:16.2"
```

**Example: Run 3 PostgreSQL pods for intra-cluster HA:**
```yaml
apiServer:
  cnpg:
    instances: 3   # CNPG Operator manages leader election internally
```

### 7.2 Valkey Values

Configured under `apiServer.valkey`:

| Key | Primary Default | Secondary Override | Description |
|:---|:---|:---|:---|
| `enabled` | `true` | `true` | Deploy Valkey |
| `image.repository` | `valkey/valkey` | (same) | Image repository |
| `image.tag` | `7.2-alpine` | (same) | Image tag |
| `host` | `redis-service` | `redis-service` | Service name for Redis-compat clients |
| `port` | `6379` | `6379` | Service port |
| `password` | `""` | `""` | Auth password (empty = no auth) |
| `replication.role` | `primary` | `standby` | `primary` = standalone master; `standby` = replica |
| `replication.primaryHost` | — | `10.99.0.1` | WireGuard IP of primaryhub Valkey (standby only) |
| `replication.primaryPort` | — | `6379` | Valkey port on primaryhub (standby only) |

---

## 8. Verification & Testing Runbook

### Test 1: CloudNativePG Physical Streaming Status

**On `secondaryhub`** — verify healthy cluster and active WAL streaming:

```bash
kubectl get cluster -n opensandbox-system postgresql-secondary
# Expected: Cluster in healthy state

kubectl logs -n opensandbox-system postgresql-secondary-1 --tail=20
# Expected:
# "database system is ready to accept read-only connections"
# "started streaming WAL from primary at 0/8000000 on timeline 1"
```

**On `primaryhub`** — verify standby is connected and zero-lag:

```bash
kubectl exec -n opensandbox-system postgresql-primary-1 -- \
  psql 'postgresql://postgres:password123@127.0.0.1:5432/postgres' -x -c \
  "SELECT client_addr, state, sent_lsn, replay_lsn, sync_state FROM pg_stat_replication;"
```

Expected:

```
-[ RECORD 1 ]---
client_addr | 10.99.0.2    ← secondaryhub WireGuard IP
state       | streaming
sent_lsn    | 0/8000100
replay_lsn  | 0/8000100    ← matches sent_lsn = zero lag
sync_state  | async
```

---

### Test 2: Real-Time Write Replication Test (< 50ms)

Insert on **`primaryhub`**:

```bash
kubectl exec -n opensandbox-system postgresql-primary-1 -- \
  psql 'postgresql://postgres:password123@127.0.0.1:5432/apikeys' -c \
  "CREATE TABLE IF NOT EXISTS ha_sync_test (id serial primary key, message text, created_at timestamp default now());
   INSERT INTO ha_sync_test (message) VALUES ('cnpg_replication_test_success');
   SELECT * FROM ha_sync_test;"
```

Expected:

```
CREATE TABLE
INSERT 0 1
 id |            message            |         created_at
----+-------------------------------+----------------------------
  1 | cnpg_replication_test_success | 2026-09-18 10:12:08.703039
```

Immediately query on **`secondaryhub`**:

```bash
kubectl exec -n opensandbox-system postgresql-secondary-1 -- \
  psql 'postgresql://postgres:password123@127.0.0.1:5432/apikeys' -c \
  "SELECT id, message, created_at FROM ha_sync_test;"
```

Expected — same row appears instantly:

```
 id |            message            |         created_at
----+-------------------------------+----------------------------
  1 | cnpg_replication_test_success | 2026-09-18 10:12:08.703039
```

> Sub-second zero-loss replication confirmed across Kind clusters over WireGuard.

---

### Test 3: Write-Conflict Protection Test

Attempt a direct write to **`secondaryhub`** PostgreSQL:

```bash
kubectl exec -n opensandbox-system postgresql-secondary-1 -- \
  psql 'postgresql://postgres:password123@127.0.0.1:5432/apikeys' -c \
  "INSERT INTO ha_sync_test (message) VALUES ('should_fail_on_secondary');"
# Expected: ERROR:  cannot execute INSERT in a read-only transaction
```

Attempt a direct write to **`secondaryhub`** Valkey:

```bash
kubectl exec -n opensandbox-system \
  $(kubectl get pod -n opensandbox-system -l app=valkey -o jsonpath='{.items[0].metadata.name}') -- \
  valkey-cli set fail_key 'fail'
# Expected: READONLY You can't write against a read only replica.
```

---

### Test 4: Valkey Real-Time Replication Verification

Write on **`primaryhub`**:

```bash
kubectl exec -n opensandbox-system \
  $(kubectl get pod -n opensandbox-system -l app=valkey -o jsonpath='{.items[0].metadata.name}') -- \
  valkey-cli set test_sync_valkey 'valkey_live_sync_success'
# Expected: OK
```

Read on **`secondaryhub`**:

```bash
kubectl exec -n opensandbox-system \
  $(kubectl get pod -n opensandbox-system -l app=valkey -o jsonpath='{.items[0].metadata.name}') -- \
  valkey-cli get test_sync_valkey
# Expected: valkey_live_sync_success
```

Check secondary Valkey replication log:

```bash
kubectl logs -n opensandbox-system \
  $(kubectl get pod -n opensandbox-system -l app=valkey -o jsonpath='{.items[0].metadata.name}') --tail=15
# Expected:
# Connecting to MASTER 10.99.0.1:6379
# MASTER <-> REPLICA sync: Finished with success
```

---

### Test 5: Standby Microservices Health Checks

```bash
# sandbox-api health on secondaryhub:
curl -s http://10.99.0.2/health | python3 -m json.tool
# Expected: "healthy": true, database/cache/queue all healthy

# opensandbox-server health:
kubectl exec -n opensandbox-system deployment/opensandbox-server -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health').read().decode())"
# Expected: {"status":"healthy"}
```

---

### Test 6: End-to-End API Key Creation and Scan (via VIP)

#### Create an API Key

```bash
curl -X POST http://10.99.0.100/api/v1/01sbx/api-keys \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <AUTH0_TOKEN>" \
  -d '{
    "name": "my-scanner-key",
    "backend": "Z1_SANDBOX",
    "ttl_hours": 720
  }'
```

Expected:

```json
{
  "api_key": "",
  "api_key_id": "",
  "status": "Key 'my-scanner-key' generated successfully. Valid for 720.0 hour(s)."
}
```

#### Verify API Key Replicated to `secondaryhub`

```bash
kubectl exec -n opensandbox-system postgresql-secondary-1 -- \
  psql 'postgresql://postgres:password123@127.0.0.1:5432/apikeys' -c \
  "SELECT id, name, created_at FROM api_keys ORDER BY created_at DESC LIMIT 5;"
# The newly created key must appear here — it was written on primaryhub and replicated instantly.
```

#### Submit a Scan Job

```bash
curl -X POST http://10.99.0.100/api/v1/01sbx/scan-jobs?async=true \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <API_KEY>" \
  -d '{
    "files": {
      "main.py": "import os\ncmd = \"ls\"\nos.system(cmd)"
    },
    "metadata": {"runtime": "gvisor"}
  }'
```

Expected:

```json
{"job_id": "136f85da-88ba-48f0-8a5b-daeaf95e1120", "status": "PROCESSING"}
```

#### Retrieve Completed Report

```bash
curl -s -H "Authorization: Bearer <API_KEY>" \
  http://10.99.0.100/api/v1/01sbx/v1/jobs/136f85da-88ba-48f0-8a5b-daeaf95e1120/result
```

Expected:

```json
{
  "job_id": "136f85da-88ba-48f0-8a5b-daeaf95e1120",
  "status": "COMPLETED",
  "report": {
    "summary": {"overall_status": "RISKS_FOUND", "findings_count": 6},
    "findings": [{"tool": "bandit", "severity": "high", "issue": "shell injection detected"}]
  }
}
```

---

### Test 7: Dynamic Language Scanner Routing

Submit Go code — verify Go-specific scanner image is selected:

```bash
curl -X POST http://10.99.0.100/api/v1/01sbx/scan-jobs?async=true \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"files": {"main.go": "package main\nimport \"fmt\"\nfunc main() { fmt.Println(\"hello\") }"}, "metadata": {"runtime": "gvisor"}}'
```

On `spoke1-vm`, check the scanner image used:

```bash
kubectl get pod -n opensandbox-workloads \
  -o jsonpath='{.items[-1].spec.containers[0].image}'
# Expected: 01community/01sandbox-scanner-go:1.0.0
```

Check logs — Bandit should NOT appear:

```bash
kubectl logs -n opensandbox-workloads <pod-name>
# Expected:
# [INFO] Classified Files: Go(1)
# [INFO] Enabled tools: semgrep, gosec, golangci_lint
# [INFO] Running Gosec scan...   ← Bandit absent
```

---

### Test 8: Spoke Region Placement Routing

```bash
curl -X POST http://10.99.0.100/api/v1/01sbx/scan-jobs?async=true \
  -H "Authorization: Bearer <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"files": {"main.py": "print(1)"}, "metadata": {"region": "eu-central-1", "runtime": "kata-fc"}}'
```

On `primaryhub`, verify ManifestWork went to the EU spoke:

```bash
kubectl get manifestwork -n spoke-eu-central-1
```

On `spoke2-vm`, verify `kata-fc` runtimeClass:

```bash
kubectl get pod -n opensandbox-workloads \
  -o jsonpath='{.items[-1].spec.runtimeClassName}'
# Expected: kata-fc
```

---

## 9. Failover, Outage & Failback Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: NORMAL OPERATIONS                                      │
│ primaryhub (CNPG Master RW) ═══ WAL ════════▶ secondaryhub RO  │
│ Valkey Master               ═══ Repl ═══════▶ Valkey Replica   │
└─────────────────────────────────────────────────────────────────┘
                ↓  [Primary Crashes ❌]
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 2: OPERATOR FAILOVER                                      │
│ primaryhub (DOWN ❌)                                            │
│ secondaryhub promoted to Master RW via kubectl cnpg promote ✅  │
│ Users write API keys → secondaryhub DB                         │
└─────────────────────────────────────────────────────────────────┘
                ↓  [Primary Recovers 🔄]
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 3: REVERSE SYNC (Failback)                                │
│ primaryhub rejoins as Standby RO ← replicates from secondaryhub│
│ All keys created during outage → appear on primaryhub          │
└─────────────────────────────────────────────────────────────────┘
                ↓  [Optional Planned Switchover]
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 4: RESTORE ORIGINAL ROLES                                 │
│ primaryhub promoted back to Master RW                           │
│ secondaryhub demoted to Standby RO via Helm redeploy           │
└─────────────────────────────────────────────────────────────────┘
```

### 9.1 Phase 2: Primary Down — Promote Secondary to Master

On **`secondaryhub`** (`ubuntu@192.168.101.20`):

```bash
# 1. Promote CNPG replica cluster to independent read-write master:
kubectl cnpg promote postgresql-secondary -n opensandbox-system
# Or equivalently:
# kubectl patch cluster postgresql-secondary -n opensandbox-system \
#   --type='json' -p='[{"op":"replace","path":"/spec/replica/enabled","value":false}]'

# 2. Promote Valkey to standalone master:
kubectl exec -n opensandbox-system \
  $(kubectl get pod -n opensandbox-system -l app=valkey -o jsonpath='{.items[0].metadata.name}') -- \
  valkey-cli replicaof no one

# 3. Verify PostgreSQL is now master (pg_is_in_recovery = f):
kubectl exec -n opensandbox-system postgresql-secondary-1 -- \
  psql 'postgresql://postgres:password123@127.0.0.1:5432/apikeys' -c \
  "SELECT pg_is_in_recovery();"
# Expected: f  (false = master mode)

# 4. Verify Valkey is now master:
kubectl exec -n opensandbox-system \
  $(kubectl get pod -n opensandbox-system -l app=valkey -o jsonpath='{.items[0].metadata.name}') -- \
  valkey-cli info replication | grep "^role"
# Expected: role:master
```

Float the Virtual IP (`10.99.0.100`) to `secondaryhub` (`10.99.0.2`) on `gateway-vm`.

Users can now create API keys, launch scans, and all state is committed to `secondaryhub`.

### 9.2 Phase 3: Primary Recovers — Rejoin as Standby (Reverse Sync)

When `primaryhub` boots back online, **do not allow it to restart as master** — that causes split-brain. Configure it to replicate FROM `secondaryhub`:

On **`primaryhub`** (`ubuntu@192.168.100.20`):

```bash
# 1. Reconfigure CNPG primary cluster to replicate from secondaryhub:
kubectl patch cluster postgresql-primary -n opensandbox-system \
  --type='merge' \
  -p='{
    "spec": {
      "replica": {"enabled": true, "source": "postgresql-secondary"},
      "externalClusters": [{
        "name": "postgresql-secondary",
        "connectionParameters": {
          "host": "10.99.0.2",
          "port": "5432",
          "user": "postgres",
          "dbname": "apikeys",
          "sslmode": "prefer"
        },
        "password": {"name": "postgresql-primary-credentials", "key": "password"}
      }]
    }
  }'

# 2. Reconfigure Valkey on primaryhub to replicate from secondaryhub:
kubectl exec -n opensandbox-system \
  $(kubectl get pod -n opensandbox-system -l app=valkey -o jsonpath='{.items[0].metadata.name}') -- \
  valkey-cli replicaof 10.99.0.2 6379

# 3. Verify reverse replication on secondaryhub (now master):
kubectl exec -n opensandbox-system postgresql-secondary-1 -- \
  psql 'postgresql://postgres:password123@127.0.0.1:5432/postgres' -x -c \
  "SELECT client_addr, state, sync_state FROM pg_stat_replication;"
# Expected: client_addr = 10.99.0.1 (primaryhub), state = streaming

# 4. Confirm all outage-period API keys are on primaryhub:
kubectl exec -n opensandbox-system postgresql-primary-1 -- \
  psql 'postgresql://postgres:password123@127.0.0.1:5432/apikeys' -c \
  "SELECT id, name, created_at FROM api_keys ORDER BY created_at DESC LIMIT 10;"
# All keys created while primaryhub was offline now appear here.
```

### 9.3 Phase 4: Optional Restore of Original Roles

If you want `primaryhub` to return to its original active master role:

```bash
# On primaryhub — after pausing writes on secondaryhub (VIP cutover):

# 1. Promote primaryhub back to master:
kubectl cnpg promote postgresql-primary -n opensandbox-system
kubectl exec -n opensandbox-system \
  $(kubectl get pod -n opensandbox-system -l app=valkey -o jsonpath='{.items[0].metadata.name}') -- \
  valkey-cli replicaof no one

# 2. Demote secondaryhub back to standby using standard Helm:
# (On secondaryhub)
helm upgrade --install codeinspector ./codeInspector \
  -n opensandbox-system \
  -f ./codeInspector/values-secondary.yaml

# 3. Float VIP back to primaryhub.
# Original warm standby topology fully restored — zero data loss.
```

---

## 10. Operational Gotchas & Troubleshooting

### 10.1 Primary PostgreSQL Pod Crashes / Restarts

- **Behavior**: `secondaryhub` logs `could not connect to server... retrying` in the WAL receiver.
- **Resolution**: Automatic. The CNPG Operator restarts the pod. `secondaryhub` reconnects and resumes streaming from the last confirmed WAL position — no data loss, no manual steps.

### 10.2 Temporary WireGuard Link Flapping

- **Behavior**: Short tunnel outage (< 30 minutes). Both sides log connection failures.
- **Resolution**: Automatic. `wal_keep_size = 1GB` on primary retains WAL during the outage. Valkey maintains a replication backlog. Both systems resync from last known position upon reconnect.

### 10.3 WireGuard Down for Extended Period (> WAL Retention)

- **Behavior**: If secondary falls more than 1GB of WAL behind the primary, stream-resume fails: `requested WAL segment has already been removed`.
- **Fix**: Force a full re-bootstrap of the secondary cluster:
  ```bash
  # On secondaryhub:
  kubectl delete cluster postgresql-secondary -n opensandbox-system
  # Then re-apply Helm to recreate the cluster (triggers fresh pg_basebackup):
  helm upgrade --install codeinspector ./codeInspector \
    -n opensandbox-system -f ./codeInspector/values-secondary.yaml
  ```

### 10.4 Kind Image Caching (`IfNotPresent`)

- **Gotcha**: After modifying source code but keeping the same image tag, `kind load` may serve the old cached image because containerd sees the tag already present.
- **Fix**: Delete the old image before reloading:
  ```bash
  docker exec secondaryhub-control-plane \
    crictl rmi docker.io/01community/01sandbox-api:v0.7.9
  kind load docker-image 01community/01sandbox-api:v0.7.9 --name secondaryhub
  ```

### 10.5 pg_basebackup Hangs / Times Out

- **Symptom**: `postgresql-secondary` Cluster status stuck at `Bootstrapping via pg_basebackup` for > 5 minutes. CNPG Operator logs `could not connect to server`.
- **Diagnosis**:
  ```bash
  nc -zv 10.99.0.1 5432   # From secondaryhub — fails = iptables DNAT missing
  kubectl get svc -n opensandbox-system postgresql-replication   # Must exist on primaryhub
  kubectl get cluster -n opensandbox-system postgresql-primary   # Must be "healthy state"
  ```
- **Fix**: Ensure iptables DNAT rules exist on `primaryhub` (Phase 0 Step 0.4) and `postgresql-primary` is running.

### 10.6 Database Selection: `apikeys` vs `postgres`

- **Gotcha**: PostgreSQL holds multiple databases. Application tables (`api_keys`, `system_settings`, `rate_limits`) live in the **`apikeys`** database. Replication monitoring queries (`pg_stat_replication`, `pg_is_in_recovery()`) run against the **`postgres`** database.
- **Rule**: Always use the correct connection string:
  - Application tables: `...@127.0.0.1:5432/apikeys`
  - Replication monitoring: `...@127.0.0.1:5432/postgres`

### 10.7 Valkey Replica `master_link_status: down`

- **Symptom**: `valkey-cli info replication` on secondaryhub shows `master_link_status:down`.
- **Diagnosis**:
  ```bash
  # From secondaryhub:
  nc -zv 10.99.0.1 6379   # Test reachability
  # On primaryhub — check NodePort service exists:
  kubectl get svc -n opensandbox-system redis-replication
  # On primaryhub — check iptables DNAT rule:
  sudo iptables -t nat -L PREROUTING -n | grep 6379
  ```
- **Fix**: Ensure the `redis-replication` NodePort Service exists and the iptables DNAT rule for port 6379 is in place on `primaryhub`.

### 10.8 sandbox-api Crashes on secondaryhub (Read-Only Error)

- **Symptom**: `sandbox-api` pod in `CrashLoopBackOff`. Logs show `cannot execute CREATE TABLE in a read-only transaction`.
- **Cause**: Running an older version of `app_state.py` that does not include the `pg_is_in_recovery()` standby guard.
- **Fix**: Rebuild the `01community/01sandbox-api:v0.7.9` image with the patched `app_state.py`, delete the old cached image from Kind, and reload:
  ```bash
  docker exec secondaryhub-control-plane \
    crictl rmi docker.io/01community/01sandbox-api:v0.7.9
  kind load docker-image 01community/01sandbox-api:v0.7.9 --name secondaryhub
  kubectl rollout restart deployment/sandbox-api -n opensandbox-system
  ```

### 10.9 CRD "No Kind Matches" Error During Helm Apply

- **Symptom**: `helm upgrade` fails with `no matches for kind "Cluster" in version "postgresql.cnpg.io/v1"`.
- **Cause**: CRDs were not installed. This should not happen with `helm upgrade --install` (Helm auto-installs `crds/`), but it occurs if using `helm template | kubectl apply` (which bypasses CRD installation).
- **Fix**: Apply CRDs manually first:
  ```bash
  kubectl apply -f ~/01-Sandbox/codeInspector/crds/
  # Then re-run:
  helm upgrade --install codeinspector ./codeInspector \
    -n opensandbox-system -f ./codeInspector/values.yaml
  ```
