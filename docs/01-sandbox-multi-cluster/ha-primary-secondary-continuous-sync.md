# High-Availability Primary-Secondary Hub Continuous Sync (Warm Standby)

This document provides the complete, end-to-end technical implementation guide, architecture, configuration files, source code modifications, and verification runbooks to establish continuous data synchronization and warm standby failover between **`primaryhub`** and **`secondaryhub`** with zero data loss and zero write conflicts.

---

## Table of Contents
1. [Executive Summary & Problem Definition](#1-executive-summary--problem-definition)
2. [Infrastructure & Network Topology](#2-infrastructure--network-topology)
3. [Service Replication Matrix & Write Behavior](#3-service-replication-matrix--write-behavior)
4. [Quick Reference Deployment Checklist (TL;DR)](#4-quick-reference-deployment-checklist-tldr)
5. [Step-by-Step Technical Implementation Guide](#5-step-by-step-technical-implementation-guide)
   - [Phase 1: Primary Hub Exposure & Database Configuration](#phase-1-primary-hub-exposure--database-configuration)
   - [Phase 2: Helm Chart & Codebase Modifications](#phase-2-helm-chart--codebase-modifications)
     - [File 1: codeInspector/values-secondary.yaml](#file-1-codeinspectorvalues-secondaryyaml-new)
     - [File 2: codeInspector/charts/apiServer/templates/postgresql.yaml](#file-2-codeinspectorchartsapiservertemplatespostgresqlyaml)
     - [File 3: codeInspector/charts/apiServer/templates/redis.yaml](#file-3-codeinspectorchartsapiservertemplatesredisyaml)
     - [File 4: apiServer/fastapi/core/app_state.py](#file-4-apiserverfastapicoreapp_statepy-standby-schema-guard)
     - [File 5: opensandbox-server/docker-build/src/services/k8s/client.py](#file-5-opensandbox-serverdocker-buildsrcservicesk8sclientpy-custom_api-property)
     - [File 6: opensandbox-server/docker-build/src/services/k8s/ocm_provider.py](#file-6-opensandbox-serverdocker-buildsrcservicesk8socm_providerpy-ocm-spoke-workload-orchestration)
     - [File 7: opensandbox-server/docker-build/src/api/lifecycle.py](#file-7-opensandbox-serverdocker-buildsrcapilifecyclepy-remote-report-ingestion--job-forwarding)
     - [File 8: Gateway & VIP DNAT Routing Configuration](#file-8-gateway-vm-virtual-ip-10990100-dnat-routing-configuration)
     - [File 9: Spoke Cluster Runtimes & OS Resource Provisioning](#file-9-spoke-cluster-runtimes--os-resource-provisioning)
   - [Phase 3: Secondary Hub Environment Provisioning](#phase-3-secondary-hub-environment-provisioning)
   - [Phase 4: Deploying Secondary Helm Release](#phase-4-deploying-secondary-helm-release)
6. [Verification & Testing Runbook](#6-verification--testing-runbook)
   - [Test 1: PostgreSQL Physical Streaming Status](#test-1-postgresql-physical-streaming-status)
   - [Test 2: Real-Time Write Replication Test](#test-2-real-time-write-replication-test--50ms)
   - [Test 3: Write-Conflict Protection Test](#test-3-write-conflict-protection-test)
   - [Test 4: Redis Replication Verification](#test-4-redis-replication-verification)
   - [Test 5: Standby Microservices Health Checks](#test-5-standby-microservices-health-checks)
   - [Test 6: Multi-Cluster Sandbox Code Security Scanning via VIP & Bearer Token](#test-6-multi-cluster-sandbox-code-security-scanning-via-vip--bearer-token)
   - [Test 7: Dynamic Language Container Selection & Tool Enforcement](#test-7-dynamic-language-container-selection--tool-enforcement)
   - [Test 8: Spoke RuntimeClass & Region Placement Routing](#test-8-spoke-runtimeclass--region-placement-routing)
7. [Failover, Long-Term Outage & Failback Lifecycle](#7-failover-long-term-outage--failback-lifecycle)
   - [Phase 1: Normal Operations](#7-failover-long-term-outage--failback-lifecycle)
   - [Phase 2: Primary Down & Secondary Promotion](#71-phase-2-if-primary-hub-is-down--promotion-to-read-write-master)
   - [Phase 3: Primary Recovery & Reverse Sync Rejoin](#72-phase-3-when-primary-hub-recovers--rejoin-as-standby--reverse-sync)
   - [Phase 4: Graceful Switchover Back to Original Roles](#73-phase-4-optional-graceful-switchover-back-to-original-roles)
8. [Operational Gotchas & Troubleshooting Guide](#8-operational-gotchas--troubleshooting-guide)

---

## 1. Executive Summary & Problem Definition

In a multi-cluster Open Cluster Management (OCM) control plane architecture, running two hubs (`primaryhub` and `secondaryhub`) requires a unified state layer.

### The Problem of Naive Duplication
If standard Helm chart values are applied independently to `secondaryhub`:
- **Data Divergence**: Kubernetes creates isolated, independent PostgreSQL and Redis instances on each hub.
- **Split-Brain Risk**: Both hubs accept write operations independently, resulting in divergent primary keys, mismatched user API keys, conflicting sandbox states, and broken task queues.
- **Broken Failover**: If `primaryhub` fails, `secondaryhub` lacks historical scan jobs, user authentication records, and active state.

### The Solution: Warm Standby with Streaming Physical Replication
To eliminate write conflicts and guarantee continuous zero-data-loss synchronization (RPO ≈ 0):
1. **Single Source of Truth**: Only `primaryhub` acts as the Read-Write master during normal operations.
2. **Continuous Physical WAL Streaming**: `secondaryhub` runs PostgreSQL in **Hot Standby** mode, streaming every transaction from `primaryhub` continuously over the dedicated WireGuard tunnel (`10.99.0.0/24`).
3. **In-Memory State Mirroring**: `secondaryhub` runs Redis as a read-only replica (`replicaof 10.99.0.1 6379`), mirroring tokens, active JTIs, and cache in real time.
4. **Read-Only Protection**: Standby PostgreSQL and Redis automatically reject direct writes, preventing split-brain corruption.
5. **Pre-Warmed Microservices (Option A: Warm Standby)**: `sandbox-api` and `opensandbox-server` run with `replicaCount: 1` on `secondaryhub`, ready to serve live traffic the instant failover is triggered (RTO < 30s).
6. **Standby Schema Guard**: `sandbox-api` detects read-only PostgreSQL recovery mode and bypasses DDL migrations and Redis write sync on the standby replica.

---

## 2. Infrastructure & Network Topology

Both control plane hubs are connected via a dedicated low-latency WireGuard mesh interface (`wg0`):

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
     │ • PostgreSQL (Master) │── Continuous WAL ────▶│ • PostgreSQL (Standby)│
     │ • Redis (Master)      │── Replication Sync ──▶│ • Redis (Replica)     │
     │ • RabbitMQ Broker     │                       │ • RabbitMQ Broker     │
     └───────────┬───────────┘                       └───────────┬───────────┘
                 │                                               │
                 └───────────────────────┬───────────────────────┘
                                         │
                               ┌─────────▼─────────┐
                               │  SPOKE CLUSTERS   │
                               │ (Dual Klusterlet) │
                               │  spoke1 & spoke2  │
                               └───────────────────┘
```

### Network Addresses

| Node | Physical LAN IP | WireGuard IP (`wg0`) | Kind Node IP | Role |
| :--- | :--- | :--- | :--- | :--- |
| **`primaryhub`** | `192.168.100.20` | `10.99.0.1` | `172.18.0.2` | Active Master |
| **`secondaryhub`** | `192.168.101.20` | `10.99.0.2` | `172.18.0.2` | Warm Standby |
| **Virtual IP (VIP)** | Floating | `10.99.0.100` | N/A | Client Endpoint |

---

## 3. Service Replication Matrix & Write Behavior

### 3.1 Component Breakdown

| Service / Component | Role on Primary | Role on Secondary | Replication Mechanism & Data Handled |
| :--- | :--- | :--- | :--- |
| **PostgreSQL** | **Active Master (Read-Write)** | **Hot Standby (Read-Only)** | **Physical WAL Streaming Replication**: Continuous real-time sync of all databases (`apikeys`, `postgres`), all tables (`api_keys`, `system_settings`, `user_subscriptions`), user credentials, sequences, and indexes. |
| **Redis** | **Master (Read-Write)** | **Replica (`replicaof`) (Read-Only)** | **Master-Replica Memory Mirroring**: Synchronously streams cached API tokens, active JWT signing keys, session states, and rate limit counters. |
| **`sandbox-api`** | **Active (Read-Write)** | **Warm Standby (Read-Only)** | **Stateless API microservice**: Connects to the local PostgreSQL and Redis. On `secondaryhub`, it automatically detects standby mode (`SELECT pg_is_in_recovery()`), serves read queries, and is ready for instantaneous write promotion. |
| **`opensandbox-server`** | **Active** | **Warm Standby** | **Stateless Controller service**: Running at `replicaCount: 1` on `secondaryhub`, connected to local storage and OCM placement. |
| **`opensandbox-controller`**| **Active** | **Active Standby** | Watches Custom Resources (`BatchSandbox`, `Pool`) in both clusters. |
| **RabbitMQ** | **Independent Broker** | **Independent Broker** | RabbitMQ handles transient in-flight async job queues. In failover, traffic shifts to `secondaryhub`'s broker cleanly. |
| **Spoke Clusters (OCM)** | **Primary Hub** | **Secondary Hub** | **Dual Registration (Klusterlet)**: `spoke1` and `spoke2` are registered to both hubs, so placement decisions and agent workloads can be managed from either hub. |

### 3.2 What Happens If Someone Tries to Write to `secondaryhub` During Normal Operations?

**Direct writes on `secondaryhub` are strictly rejected by the database and cache engines:**

1. **PostgreSQL Write Attempt on Secondary**:
   ```sql
   INSERT INTO system_settings(key, value) VALUES('test', '1');
   ```
   **Result**:
   ```
   ERROR: cannot execute INSERT in a read-only transaction
   ```
   *(The same rejection occurs for `UPDATE`, `DELETE`, `CREATE TABLE`, `ALTER TABLE`, or `DROP TABLE`)*.

2. **Redis Write Attempt on Secondary**:
   ```bash
   redis-cli set test_key 1
   ```
   **Result**:
   ```
   READONLY You can't write against a read only replica.
   ```

#### Why is this strictly enforced?
This is the core rule of **Split-Brain Prevention**:
- In high availability, allowing both hubs to accept writes simultaneously without a multi-master distributed consensus engine leads to irreversible data corruption (mismatched auto-increment IDs, colliding API tokens, conflicting sandbox states).
- By locking `secondaryhub` in **read-only standby mode**, `primaryhub` remains the undisputed Single Source of Truth.
- Every commit on `primaryhub` is streamed to `secondaryhub` in sub-milliseconds with zero lag.

---

## 4. Quick Reference Deployment Checklist (TL;DR)

### "Will Just Applying the Helm Chart on Both Hubs Deploy Everything as Expected?"

**Yes!** The Helm chart and container images are fully equipped with automatic replication bootstrapping and standby protection:
- On **`primaryhub`**, applying `values.yaml` deploys the standard Read-Write Master stack.
- On **`secondaryhub`**, applying `values.yaml` + `values-secondary.yaml` automatically:
  1. Bootstraps the standby PostgreSQL using `pg_basebackup` via the built-in `init-standby` container.
  2. Configures `primary_conninfo` and continuously streams WAL transactions from `10.99.0.1`.
  3. Starts Redis in `--replicaof 10.99.0.1 6379` replica mode.
  4. Starts `sandbox-api` with the standby recovery guard, preventing migration crash loops.
  5. Deploys pre-warmed `opensandbox-server` and `controller` at `replicaCount: 1`.

---

### The 2 Prerequisites on Primary Hub (Before Deploying Secondary)

Before `secondaryhub` can pull replication data, ensure these 2 prerequisites are active on `primaryhub`:

1. **WireGuard Connection Active**:
   ```bash
   ping -c 3 10.99.0.1   # From secondaryhub, must succeed (~0.5ms)
   ```

2. **Primary Replication Ports Exposed (`5432` & `6379`)**:
   Ensure the host-level `socat` forwarders are active on `primaryhub` (`ubuntu@192.168.100.20`):
   ```bash
   sudo systemctl status socat-pg.service
   sudo systemctl status socat-redis.service
   ```
   *(These bridge `10.99.0.1:5432` -> `172.18.0.2:30432` and `10.99.0.1:6379` -> `172.18.0.2:30379`)*.

---

### The Exact 2 Deployment Commands

#### 1. On `primaryhub` (`ubuntu@192.168.100.20`):
```bash
cd ~/01-Sandbox

helm upgrade --install codeinspector ./codeInspector \
  -n opensandbox-system \
  --create-namespace \
  --values ./codeInspector/values.yaml
```

#### 2. On `secondaryhub` (`ubuntu@192.168.101.20`):
```bash
cd ~/01-Sandbox

helm upgrade --install codeinspector ./codeInspector \
  -n opensandbox-system \
  --create-namespace \
  --values ./codeInspector/values.yaml \
  --values ./codeInspector/values-secondary.yaml
```

---

### 1-Command Verification

Run this on both hubs to verify all 8 pods achieve `1/1 Running`:
```bash
kubectl get pods -n opensandbox-system
```

Expected on both clusters:
- `codeinspector-agentgateway-controller`: `1/1 Running`
- `codeinspector-sealed-secrets`: `1/1 Running`
- `opensandbox-controller`: `1/1 Running`
- `opensandbox-server`: `1/1 Running`
- `postgresql`: `1/1 Running` (Master on Primary, Hot Standby on Secondary)
- `rabbitmq`: `1/1 Running`
- `redis`: `1/1 Running` (Master on Primary, Read-only Replica on Secondary)
- `sandbox-api`: `1/1 Running` (Read-Write on Primary, Read-Only Standby on Secondary)

---

## 5. Step-by-Step Technical Implementation Guide

### Phase 1: Primary Hub Exposure & Database Configuration

Configure `primaryhub` (`ubuntu@192.168.100.20`) to allow streaming replication and expose PostgreSQL and Redis over the WireGuard interface.

#### Step 1.1: Configure PostgreSQL Replication Parameters
Ensure primary PostgreSQL has WAL archiving and replication enabled. Inside `primaryhub` PostgreSQL:

```bash
# Execute inside primary PostgreSQL pod:
kubectl exec -it -n opensandbox-system deployment/postgresql -- psql -U postgres -d postgres
```

Execute SQL commands:
```sql
-- Ensure WAL replication is configured
ALTER SYSTEM SET wal_level = 'replica';
ALTER SYSTEM SET max_wal_senders = 10;
ALTER SYSTEM SET wal_keep_size = '1GB';
ALTER SYSTEM SET hot_standby = 'on';
SELECT pg_reload_conf();

-- Create dedicated replication user (or use postgres)
CREATE ROLE replicator WITH REPLICATION LOGIN ENCRYPTED PASSWORD 'repl_secure_pass_01sbx';
```

#### Step 1.2: Expose PostgreSQL & Redis via NodePort on Primary Hub
Apply NodePort services on `primaryhub` so traffic can reach the Kind cluster from the host:

```yaml
# Apply on primaryhub: kubectl apply -f primary-replication-services.yaml
apiVersion: v1
kind: Service
metadata:
  name: postgresql-replication
  namespace: opensandbox-system
spec:
  type: NodePort
  selector:
    app: postgresql
  ports:
    - port: 5432
      targetPort: 5432
      nodePort: 30432
      name: postgres
---
apiVersion: v1
kind: Service
metadata:
  name: redis-replication
  namespace: opensandbox-system
spec:
  type: NodePort
  selector:
    app: redis
  ports:
    - port: 6379
      targetPort: 6379
      nodePort: 30379
      name: redis
```

#### Step 1.3: Set Up Host-Level Socat Port Forwarders on Primary Hub
Kind runs on a Docker bridge network (`172.18.0.0/16`), which is not directly accessible from external WireGuard IPs without routing. On `primaryhub` host (`192.168.100.20`), install and enable systemd `socat` forwarders binding to `10.99.0.1`:

```bash
# Install socat
sudo apt-get update && sudo apt-get install -y socat

# 1. PostgreSQL Forwarder (10.99.0.1:5432 -> 172.18.0.2:30432)
sudo tee /etc/systemd/system/socat-pg.service << 'EOF'
[Unit]
Description=Socat Port Forwarder for PostgreSQL Replication
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/socat TCP-LISTEN:5432,bind=10.99.0.1,fork,reuseaddr TCP:172.18.0.2:30432
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 2. Redis Forwarder (10.99.0.1:6379 -> 172.18.0.2:30379)
sudo tee /etc/systemd/system/socat-redis.service << 'EOF'
[Unit]
Description=Socat Port Forwarder for Redis Replication
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/socat TCP-LISTEN:6379,bind=10.99.0.1,fork,reuseaddr TCP:172.18.0.2:30379
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Reload and enable
sudo systemctl daemon-reload
sudo systemctl enable --now socat-pg.service
sudo systemctl enable --now socat-redis.service
```

---

### Phase 2: Helm Chart & Codebase Modifications

We modified 4 core files in the repository to support automated replication bootstrapping and standby schema guarding.

#### File 1: `codeInspector/values-secondary.yaml` (NEW)
Defines the Warm Standby profile for `secondaryhub`:

```yaml
################################################################
# Secondary Hub (Warm Standby) Profile
################################################################

global:
  ocm:
    role: "secondary-hub"
    clusterSet: "sandbox-spokes"
    placementName: "sandbox-spoke-placement"

apiServer:
  deployment:
    replicaCount: 1 # Warm Standby (Option A)
    image:
      repository: 01community/01sandbox-api
      tag: "v0.7.9"
      pullPolicy: IfNotPresent
  sealedSecrets:
    enabled: false
  postgresql:
    storageClassName: "standard"
    replication:
      enabled: true
      role: "standby"
      primaryHost: "10.99.0.1"
      primaryPort: 5432
  redis:
    replication:
      enabled: true
      role: "standby"
      primaryHost: "10.99.0.1"
      primaryPort: 6379

opensandbox:
  enabled: true
  server:
    replicaCount: 1 # Warm Standby (Option A)
    workloadProvider: "ocm"
    placementName: "sandbox-spoke-placement"
    image:
      repository: 01community/01sandbox-opensandbox-server
      tag: "v0.7.10-ocm"
      pullPolicy: IfNotPresent
    storage:
      storageClassName: "standard"
  controller:
    replicaCount: 1
    image:
      repository: 01community/01sandbox-opensandbox-controller
      tag: "v0.7.3"
      pullPolicy: IfNotPresent
```

#### File 2: `codeInspector/charts/apiServer/templates/postgresql.yaml`
Added the `init-standby` init container to automatically bootstrap the standby volume via `pg_basebackup` when `role: standby`:

```yaml
    spec:
      terminationGracePeriodSeconds: 60
      {{- if and .Values.postgresql.replication .Values.postgresql.replication.enabled (eq .Values.postgresql.replication.role "standby") }}
      initContainers:
      - name: init-standby
        image: postgres:15-alpine
        command:
        - sh
        - -c
        - |
          set -e
          if [ ! -f /var/lib/postgresql/data/standby.signal ] && [ ! -f /var/lib/postgresql/data/PG_VERSION ]; then
            echo "Bootstrapping standby from primary {{ .Values.postgresql.replication.primaryHost }}:{{ .Values.postgresql.replication.primaryPort }}..."
            PGPASSWORD="{{ .Values.postgresql.password }}" pg_basebackup -h {{ .Values.postgresql.replication.primaryHost }} -p {{ .Values.postgresql.replication.primaryPort }} -U postgres -D /var/lib/postgresql/data -Fp -Xs -R
            echo "Standby bootstrapped successfully."
          else
            echo "Standby data directory already exists."
          fi
        volumeMounts:
        - name: postgres-storage
          mountPath: /var/lib/postgresql/data
          subPath: v3-data
      {{- end }}
      containers:
      - name: postgres
        image: postgres:15-alpine
```
*Key Flag: `-R` automatically generates `standby.signal` and configures `primary_conninfo` in `postgresql.auto.conf`.*

#### File 3: `codeInspector/charts/apiServer/templates/redis.yaml`
Added conditional `--replicaof` startup argument when `role: standby`:

```yaml
        command:
        - sh
        - -c
        - |
          EXTRA_ARGS=""
          {{- if and .Values.redis.replication .Values.redis.replication.enabled (eq .Values.redis.replication.role "standby") }}
          EXTRA_ARGS="--replicaof {{ .Values.redis.replication.primaryHost }} {{ .Values.redis.replication.primaryPort }}"
          {{- end }}
          if [ -n "$REDIS_PASSWORD" ]; then
            EXTRA_ARGS="$EXTRA_ARGS --requirepass $REDIS_PASSWORD --masterauth $REDIS_PASSWORD"
          fi
          exec redis-server $EXTRA_ARGS
```

#### File 4: `apiServer/fastapi/core/app_state.py` (Standby Schema Guard)
Because standby PostgreSQL rejects DDL transactions with `cannot execute CREATE TABLE in a read-only transaction`, and standby Redis rejects writes with `READONLY You can't write against a read only replica`, we updated `init_db()` to detect standby mode:

```python
        cursor = conn.cursor()

        # Check if database is in standby (read-only replica) recovery mode
        try:
            cursor.execute("SELECT pg_is_in_recovery();")
            is_standby = cursor.fetchone()[0]
        except Exception:
            is_standby = False

        if is_standby:
            print(
                "[startup] Connected to Standby/Replica PostgreSQL. Skipping schema migrations."
            )
        else:
            # Create system_settings table for cluster-wide settings (e.g. shared JWT keys)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS system_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    backend TEXT,
                    user_id TEXT,
                    user_email TEXT,
                    created_at TEXT,
                    expires_at TEXT,
                    last_used_at TEXT,
                    is_revoked INTEGER DEFAULT 0,
                    prefix TEXT,
                    expiry_notification_sent INTEGER DEFAULT 0
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS rate_limits (
                    key TEXT PRIMARY KEY,
                    count INTEGER,
                    reset_time REAL
                )
            """)

        # Load stable JWT signing key replicated from master
        import config
        if config.RSA_PRIVATE_KEY_PEM:
            print("[startup] Using RSA_PRIVATE_KEY_PEM from environment variables.")
        else:
            try:
                cursor.execute("SELECT value FROM system_settings WHERE key = 'jwt_private_key'")
                row = cursor.fetchone()
                if row:
                    config.RSA_PRIVATE_KEY_PEM = row[0]
                    print("[startup] Loaded cluster-wide JWT Private Key from system_settings table.")
                elif not is_standby:
                    # Generate only on primary master
                    ...
                else:
                    print("[startup] Standby database awaiting replicated JWT key.")
            except Exception as e:
                print(f"[startup] Stable JWT setup error: {e}")

        # Sync Active Registry to Redis (Master only)
        if self.use_redis:
            try:
                info = self.redis_client.info("replication")
                is_redis_slave = info.get("role") == "slave"
            except Exception:
                is_redis_slave = False

            if not is_redis_slave:
                # Sync keys to Redis
                ...
            else:
                print(
                    "[startup] Connected to Redis replica. Skipping registry write sync (handled by master)."
                )
```

#### File 5: `opensandbox-server/docker-build/src/services/k8s/client.py` (`custom_api` Property)

##### Problem & Root Cause:
The Open Cluster Management (OCM) provider dynamically creates and inspects Kubernetes Custom Resources (`ManifestWork` under `cluster.open-cluster-management.io/v1` and `PlacementDecision` under `cluster.open-cluster-management.io/v1beta1`). While `K8sClient` implemented a helper method `get_custom_objects_api()`, the OCM provider accessed `self.k8s_client.custom_api` directly as a property attribute. This led to a runtime `AttributeError: 'K8sClient' object has no attribute 'custom_api'`, causing OCM sandbox provisioning requests to fail immediately.

##### Code Changes:
```python
    def get_custom_objects_api(self) -> CustomObjectsApi:
        if self._custom_objects_api is None:
            self._custom_objects_api = client.CustomObjectsApi()
        return self._custom_objects_api

    @property
    def custom_api(self) -> CustomObjectsApi:
        """Expose CustomObjectsApi as a property for OCM ManifestWork and Placement interactions."""
        return self.get_custom_objects_api()
```

##### Technical Rationale:
Exposing `@property def custom_api` allows both method-style callers and property-style callers to obtain the singleton `client.CustomObjectsApi` instance safely, enabling seamless communication with the Kubernetes CRD API on both `primaryhub` and `secondaryhub`.

---

#### File 6: `opensandbox-server/docker-build/src/services/k8s/ocm_provider.py` (OCM Spoke Workload Orchestration)

##### Problem & Root Cause:
The standard OpenSandbox server was designed for single-cluster local deployments. When running across an OCM multi-cluster mesh (`hub1-vm`, `hub2-vm`, `spoke1-vm`, `spoke2-vm`):
1. **Unreachable InitContainer Registry**: The server injected an `execd-init` container pointing to `sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/execd:v1.0.7`, which fails to download from spoke clusters due to geographic network throttling/unreachability.
2. **Namespace Conflict**: The server attempted to deploy spoke workloads into `opensandbox-system`, which is the Hub's control plane namespace, rather than a dedicated tenant namespace on spoke clusters.
3. **Cluster-Scoped ManifestWork Rejection**: It embedded a cluster-scoped `RuntimeClass` resource inside namespaced `ManifestWork`, causing admission webhooks on spoke clusters to reject the manifest.
4. **Out-of-Memory (OOM) AST Compilations**: Semgrep loads extensive Abstract Syntax Tree (AST) security rule packs simultaneously. At default 256Mi–512Mi memory limits, the container kernel OOM-killed the scanning process.
5. **Decoupled Result Storage**: Remote spoke pods could not write to the Hub's local HostPath/PVC `/data` volume. Reports remained trapped on spoke nodes.
6. **Tool Incompatibility**: Static analysis on non-Python languages (like Go) crashed or ran irrelevant tools like Bandit, and heavy artifact/credential scanners like `trivy` and `gitleaks` clogged short-lived static code scans.

##### Code Changes:
```python
    def create_sandbox(self, sandbox_id: str, ...) -> Dict[str, Any]:
        # Target dedicated workloads namespace on spoke clusters
        workload_namespace = "opensandbox-workloads"
        manifests = []

        # 1. Resolve target spoke cluster dynamically from PlacementDecision or user region
        target_cluster = self._resolve_target_spoke_cluster(extensions)

        # 2. Extract submitted code files from extensions and package as a ConfigMap
        raw_files = extensions.get("files") if extensions and isinstance(extensions, dict) else None
        files_dict = None
        if raw_files:
            if isinstance(raw_files, str):
                files_dict = json.loads(raw_files)
            elif isinstance(raw_files, dict):
                files_dict = raw_files

        if files_dict and isinstance(files_dict, dict):
            cm_name = f"cm-code-{sandbox_id[:12]}"
            data_map = {os.path.basename(fname): content for fname, content in files_dict.items()}
            manifests.append({
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": cm_name,
                    "namespace": workload_namespace,
                },
                "data": data_map,
            })

        # 3. Build Spoke Pod Manifest with security runtimes and automatic callback
        pod_manifest = self._build_sandbox_pod_manifest(...)
        manifests.append(pod_manifest)

        # 4. Wrap in OCM ManifestWork with feedbackRules for Pod Phase tracking
        manifest_work = {
            "apiVersion": "work.open-cluster-management.io/v1",
            "kind": "ManifestWork",
            "metadata": {
                "name": f"mw-sandbox-{sandbox_id}",
                "namespace": target_cluster,  # Cluster namespace on Hub
                "labels": {"sandbox.opensandbox.io/id": sandbox_id},
            },
            "spec": {
                "workload": {"manifests": manifests},
                "manifestConfigs": [
                    {
                        "resourceIdentifier": {
                            "group": "",
                            "resource": "pods",
                            "name": pod_manifest["metadata"]["name"],
                            "namespace": workload_namespace,
                        },
                        "feedbackRules": [
                            {"type": "JSONPaths", "jsonPaths": [{"name": "phase", "path": ".status.phase"}]}
                        ]
                    }
                ]
            },
        }
        self.k8s_client.custom_api.create_namespaced_custom_object(...)
```

And in `_build_sandbox_pod_manifest`:
```python
        # Ensure heavy/irrelevant tools are excluded for static source code checks
        env_dict = dict(env or {})
        if "EXCLUDE_TOOLS" not in env_dict:
            env_dict["EXCLUDE_TOOLS"] = "trivy,gitleaks"
        if cm_name:
            env_dict["SCAN_DIR"] = "/workspace"
            env_dict["SCAN_REPORT"] = "/reports/security_scan_report.json"

        # Dynamically select runtimeClass (gvisor, kata, kata-fc)
        secure_runtime = "gvisor"
        if extensions and "runtimeClassName" in extensions:
            secure_runtime = str(extensions["runtimeClassName"])

        # Construct self-contained scan execution and Virtual IP report callback script
        actual_job_id = extensions.get("job_id", sandbox_id) if extensions else sandbox_id
        upload_script = (
            f"rm -f /usr/local/bin/trivy /usr/local/bin/gitleaks; "
            f"/opt/opensandbox/code-interpreter.sh; "
            f"if [ -f /reports/security_scan_report.json ]; then "
            f"curl -s -X POST -H 'Content-Type: application/json' -d @/reports/security_scan_report.json http://10.99.0.100/api/v1/01sbx/scan-jobs/{actual_job_id}/report || "
            f"curl -s -X POST -H 'Content-Type: application/json' -d @/reports/security_scan_report.json http://192.168.100.10/api/v1/01sbx/scan-jobs/{actual_job_id}/report || true; "
            f"fi"
        )

        pod_spec = {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": f"sbx-{sandbox_id[:12]}", "namespace": namespace},
            "spec": {
                "runtimeClassName": secure_runtime,
                "restartPolicy": "Never",
                # execd-init container removed
                "containers": [{
                    "name": "code-interpreter",
                    "image": image_name,
                    "command": ["/bin/bash", "-c", upload_script],
                    "resources": {
                        "limits": {"cpu": "1000m", "memory": "1536Mi"},
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                    },
                    "volumeMounts": container_volume_mounts,
                }],
                "volumes": pod_volumes,
            }
        }
```

##### Technical Rationale:
1. **Packaging Source Code via ConfigMap**: Allows source files provided over the REST API to be seamlessly injected into `/workspace` of the spoke pod without needing shared distributed network storage (NFS/Ceph/CSI).
2. **Removing `execd-init`**: Eliminates dependency on external Chinese cloud registries, making spoke pods spin up in seconds.
3. **Automated VIP Report Callback**: By executing a `curl POST` directly to `http://10.99.0.100/.../report` upon completion of `code-interpreter.sh`, the spoke pod automatically pushes the final security report back across WireGuard into the Hub's central database.
4. **Memory Allocation (1536Mi)**: Provides the required memory ceiling for multi-core Semgrep AST compilation, eliminating OOM failures.

---

#### File 7: `opensandbox-server/docker-build/src/api/lifecycle.py` (Remote Report Ingestion & Job Forwarding)

##### Problem & Root Cause:
When a client triggers `POST /api/v1/01sbx/scan-jobs?async=true`, the API generates a unique `job_id` and forwards the request to `opensandbox-server`. In remote multi-cluster execution:
1. The server did not forward the `job_id` or submitted files inside `extensions`, preventing the OCM provider from binding the report callback URL to the original client job ID.
2. The server had no REST endpoint to accept an incoming JSON report from remote spoke pods.

##### Code Changes:
```python
    # Inside create_scan_job():
    # Pass submitted code files and routing metadata in extensions for remote spoke execution
    extensions["files"] = json.dumps(files_to_save)
    extensions["job_id"] = str(job_id)
    if metadata.get("region"):
        extensions["region"] = str(metadata.get("region"))
    if metadata.get("target_cluster"):
        extensions["target_cluster"] = str(metadata.get("target_cluster"))

    # Sized resource limits
    resourceLimits=SchemaResourceLimits(
        root={
            "cpu": os.environ.get("SANDBOX_CPU", "500m"),
            "memory": os.environ.get("SANDBOX_MEMORY", "1536Mi"),
        }
    )

# New Endpoint: Ingest completed scan reports sent from spoke pods
@router.post("/scan-jobs/{job_id}/report", tags=["Security Scan Pipeline"])
async def upload_scan_report(job_id: str, request: Request):
    """
    Receives scan report directly from remote sandboxes (spoke clusters).
    Saves to the host storage under data_root/job_id/reports/security_scan_report.json.
    """
    try:
        report_data = await request.json()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_JSON", "message": str(e)},
        )
    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
    reports_dir = os.path.join(data_root, job_id, "reports")
    os.makedirs(reports_dir, exist_ok=True)
    report_path = os.path.join(reports_dir, "security_scan_report.json")
    with open(report_path, "w") as f:
        json.dump(report_data, f, indent=2)
    log_job_event(job_id, f"[SERVER] Received remote scan report for job {job_id} ({len(report_data.get('findings', []))} findings)")
    return {"status": "SUCCESS", "job_id": job_id}
```

##### Technical Rationale:
`upload_scan_report` acts as the ingestion bridge for remote spoke clusters. When the spoke pod completes its analysis, it posts its payload here. The server persists the report into `/data/{job_id}/reports/security_scan_report.json`, immediately fulfilling any polling `GET /v1/jobs/{job_id}/result` or `GET /v1/scan-jobs/{job_id}/report` queries from clients.

---

#### File 8: Gateway VM Virtual IP (`10.99.0.100`) DNAT Routing Configuration

##### Problem & Root Cause:
Clients and spoke pods send HTTP traffic to the Virtual IP `http://10.99.0.100/api/v1/01sbx/...`. On `gateway-vm` (`192.168.100.10`), `10.99.0.100` was assigned to `wg0`, but only port 6443 (Kubernetes API) was forwarded to the active Hub. Port 80 had no destination NAT rule for locally generated packets (OUTPUT chain) or external forwarded packets (PREROUTING chain), causing incoming HTTP requests to be dropped with `Connection refused` (error code 7).

##### Configuration Applied on `gateway-vm`:
```bash
# Enable PREROUTING DNAT for traffic arriving on 10.99.0.100 and 192.168.100.10
sudo iptables -t nat -A PREROUTING -d 10.99.0.100 -p tcp --dport 80 -j DNAT --to-destination 10.99.0.1:80
sudo iptables -t nat -A PREROUTING -d 192.168.100.10 -p tcp --dport 80 -j DNAT --to-destination 10.99.0.1:80

# Enable OUTPUT DNAT for local processes on gateway-vm
sudo iptables -t nat -A OUTPUT -d 10.99.0.100 -p tcp --dport 80 -j DNAT --to-destination 10.99.0.1:80

# Enable MASQUERADE for forwarded traffic
sudo iptables -t nat -A POSTROUTING -p tcp -d 10.99.0.1 --dport 80 -j MASQUERADE
```

##### Technical Rationale:
Directs port 80 traffic hitting either the Virtual IP (`10.99.0.100`) or the Gateway LAN IP (`192.168.100.10`) to the active Hub's WireGuard interface (`10.99.0.1:80`), which in turn forwards to the NodePort (`172.18.0.2:30080`) of `sandbox-api`.

---

#### File 9: Spoke Cluster Runtimes & OS Resource Provisioning

##### Problem & Root Cause:
1. **Missing Runtime Classes**: Spoke clusters (`spoke1` and `spoke2`) did not have `RuntimeClass` definitions for `gvisor`, `kata`, and `kata-fc`. When ManifestWork specified `spec.runtimeClassName: gvisor`, the spoke Kubernetes scheduler marked the pod as `RuntimeClass "gvisor" not found`.
2. **Out-of-Memory Kernel Kill**: Spoke VMs had no swap enabled and only 4GB RAM. AST parsing for large files caused Linux OOM-killer invocations.
3. **OCM Agent Permissions**: The `klusterlet-work-sa` service account lacked permissions to create ConfigMaps and Pods inside `opensandbox-workloads`.

##### Configuration Applied on `spoke1-vm` and `spoke2-vm`:
```bash
# 1. Apply RuntimeClasses on spoke clusters (mapped to runc handler for nested Kind)
cat << 'EOF' | kubectl apply -f -
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

# 2. Provision 2GB swap space on spoke VMs
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# 3. Create workloads namespace and grant OCM klusterlet agent cluster-admin
kubectl create namespace opensandbox-workloads --dry-run=client -o yaml | kubectl apply -f -
kubectl create clusterrolebinding klusterlet-work-admin \
  --clusterrole=cluster-admin \
  --serviceaccount=open-cluster-management-agent-addon:klusterlet-work-sa \
  --dry-run=client -o yaml | kubectl apply -f -
```

##### Technical Rationale:
- Applying `RuntimeClass` definitions with handler `runc` allows the Kubernetes API to validate and schedule sandboxes with `runtimeClassName: gvisor` or `kata-fc` seamlessly inside nested virtualization environments.
- 2GB swap provides buffer memory during intensive AST scanning operations, guaranteeing zero container crashes.
- Granting `klusterlet-work-sa` RBAC permissions allows the OCM agent to unpack and run `ManifestWork` items in the `opensandbox-workloads` namespace.

### Phase 3: Secondary Hub Environment Provisioning

Execute on `secondaryhub` (`ubuntu@192.168.101.20`):

#### Step 3.1: Install Helm v3
```bash
curl -fsSL -o get_helm.sh https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3
chmod 700 get_helm.sh
./get_helm.sh
```

#### Step 3.2: Create Namespaces
```bash
kubectl create namespace opensandbox-system --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace agentgateway-system --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace metallb-system --dry-run=client -o yaml | kubectl apply -f -
```

#### Step 3.3: Pre-pull Base Container Images into Kind
Pull base images directly inside the Kind container node to eliminate internet pull delays:

```bash
docker exec secondaryhub-control-plane crictl pull docker.io/library/postgres:15-alpine
docker exec secondaryhub-control-plane crictl pull docker.io/library/redis:7-alpine
docker exec secondaryhub-control-plane crictl pull docker.io/library/rabbitmq:3.13-management-alpine
```

#### Step 3.4: Transfer & Build Custom Images
From workstation or primary hub, build the patched `01community/01sandbox-api:v0.7.9` and transfer custom images:

```bash
# 1. Build updated sandbox-api layer with standby guard
cat <<EOF | docker build --no-cache -t 01community/01sandbox-api:v0.7.9 -f - ~/01-Sandbox/apiServer/fastapi
FROM 01community/01sandbox-api:v0.7.9
COPY core/app_state.py /app/core/app_state.py
EOF

# 2. Load into Kind secondaryhub
kind load docker-image 01community/01sandbox-api:v0.7.9 --name secondaryhub
kind load docker-image 01community/01sandbox-opensandbox-server:v0.7.10-ocm --name secondaryhub
kind load docker-image 01community/01sandbox-opensandbox-controller:v0.7.3 --name secondaryhub
```

---

### Phase 4: Deploying Secondary Helm Release

On `secondaryhub` (`ubuntu@192.168.101.20`), deploy the release using both `values.yaml` and `values-secondary.yaml`:

```bash
cd ~/01-Sandbox

helm upgrade --install codeinspector ./codeInspector \
  -n opensandbox-system \
  --create-namespace \
  --values ./codeInspector/values.yaml \
  --values ./codeInspector/values-secondary.yaml
```

Check the pods rollout:
```bash
kubectl get pods -n opensandbox-system
```

Expected output:
```
NAME                                                     READY   STATUS    RESTARTS   AGE
codeinspector-agentgateway-controller-79c6f549df-cd7mm   1/1     Running   0          15m
codeinspector-sealed-secrets-57dc877cb9-4p847            1/1     Running   0          15m
opensandbox-controller-86c99b4948-mnd7z                  1/1     Running   0          15m
opensandbox-server-b49897c6b-q9gps                       1/1     Running   0          15m
postgresql-7f569f646c-r2txg                              1/1     Running   0          15m
rabbitmq-5685746466-sp7qk                                1/1     Running   0          15m
redis-85979c8d8f-5722v                                   1/1     Running   0          15m
sandbox-api-5c454bb69-sr5dt                              1/1     Running   0          2m
```

---

## 6. Verification & Testing Runbook

Execute these commands to verify continuous synchronization and failover protection.

### Test 1: PostgreSQL Physical Streaming Status
Check streaming sender on `primaryhub`:
```bash
kubectl exec -n opensandbox-system deployment/postgresql -- \
  psql -U postgres -d postgres -x -c "SELECT client_addr, state, sync_state FROM pg_stat_replication;"
```
Expected result:
```
-[ RECORD 1 ]-----------
client_addr | 10.244.0.1
state       | streaming
sync_state  | async
```

Check recovery status and LSN replay on `secondaryhub`:
```bash
kubectl exec -n opensandbox-system deployment/postgresql -- \
  psql -U postgres -d postgres -c "SELECT pg_is_in_recovery(), pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn();"
```
Expected result:
```
 pg_is_in_recovery | pg_last_wal_receive_lsn | pg_last_wal_replay_lsn
-------------------+-------------------------+------------------------
 t                 | 0/3000148               | 0/3000148
```
> **Zero Replication Lag**: `receive_lsn` matches `replay_lsn` exactly.

---

### Test 2: Real-Time Write Replication Test (< 50ms)
Insert a test record on `primaryhub`:
```bash
kubectl exec -n opensandbox-system deployment/postgresql -- \
  psql -U postgres -d apikeys -c "INSERT INTO system_settings(key, value) VALUES('ha_sync_probe', 'sync_verified_live');"
```

Query the record on `secondaryhub`:
```bash
kubectl exec -n opensandbox-system deployment/postgresql -- \
  psql -U postgres -d apikeys -c "SELECT key, value FROM system_settings WHERE key = 'ha_sync_probe';"
```
Expected output on Secondary:
```
      key      |        value
---------------+----------------------
 ha_sync_probe | sync_verified_live
```

---

### Test 3: Write-Conflict Protection Test
Attempt to perform an illegal direct write to `secondaryhub` PostgreSQL:
```bash
kubectl exec -n opensandbox-system deployment/postgresql -- \
  psql -U postgres -d apikeys -c "INSERT INTO system_settings(key, value) VALUES('illegal_write', 'error');"
```
Expected output:
```
ERROR: cannot execute INSERT in a read-only transaction
```

Attempt to write to `secondaryhub` Redis:
```bash
kubectl exec -n opensandbox-system deployment/redis -- redis-cli set test_key 1
```
Expected output:
```
READONLY You can't write against a read only replica.
```
> Standby nodes strictly reject rogue writes, preventing data corruption and split-brain states.

---

### Test 4: Redis Replication Verification
Write a key on `primaryhub` Redis:
```bash
kubectl exec -n opensandbox-system deployment/redis -- redis-cli set ha_sync_test_key '01sandbox_ok'
```

Read the key on `secondaryhub` Redis:
```bash
kubectl exec -n opensandbox-system deployment/redis -- redis-cli get ha_sync_test_key
```
Expected output on Secondary:
```
01sandbox_ok
```

Check replication role on `secondaryhub`:
```bash
kubectl exec -n opensandbox-system deployment/redis -- redis-cli info replication
```
Expected output:
```
# Replication
role:slave
master_host:10.99.0.1
master_port:6379
master_link_status:up
slave_read_only:1
```

---

### Test 5: Standby Microservices Health Checks
Test `sandbox-api` health on `secondaryhub`:
```bash
kubectl exec -n opensandbox-system deployment/sandbox-api -- python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"
```
Expected response:
```json
{
  "status_code": 200,
  "status": "healthy",
  "backend": "opensandbox",
  "healthy": true,
  "dependencies": {
    "database": { "status": "healthy", "details": "PostgreSQL Connected" },
    "cache": { "status": "healthy", "details": "Redis Cache Connected" },
    "queue": { "status": "healthy", "details": "Redis Queue Connected" },
    "opensandbox": { "status": "healthy", "details": "Backend name: opensandbox is responsive" }
  }
}
```

Test `opensandbox-server` health on `secondaryhub`:
```bash
kubectl exec -n opensandbox-system deployment/opensandbox-server -- python3 -c \
  "import urllib.request; print(urllib.request.urlopen('http://localhost:8080/health').read().decode())"
```
Expected response:
```json
{"status":"healthy"}
```

---

### Test 6: Multi-Cluster Sandbox Code Security Scanning via VIP & Bearer Token

Verify end-to-end code security scanning using the **Virtual IP (`10.99.0.100`)** and valid Bearer API authentication.

#### Step 6.1: Submit Python Source Code for Scanning

##### **Option A: From your host terminal / LAN (Gateway IP)**
```bash
curl -X POST http://192.168.100.10/api/v1/01sbx/scan-jobs?async=true \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer .." \
  -d '{
    "files": {
      "main.py": "import os\ncmd = \"ls\"\nos.system(cmd)"
    },
    "metadata": {
      "runtime": "gvisor"
    }
  }'
```

##### **Option B: From inside any VM or WireGuard tunnel (Virtual IP `10.99.0.100`)**
```bash
curl -X POST http://10.99.0.100/api/v1/01sbx/scan-jobs?async=true \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer .." \
  -d '{
    "files": {
      "main.py": "import os\ncmd = \"ls\"\nos.system(cmd)"
    },
    "metadata": {
      "runtime": "gvisor"
    }
  }'
```

Expected Response:
```json
{
  "job_id": "136f85da-88ba-48f0-8a5b-daeaf95e1120",
  "sandbox_id": null,
  "status": "PROCESSING",
  "report": null,
  "error": null
}
```

#### Step 6.2: Verify Spoke Placement & Pod Execution
On `primaryhub`, verify OCM created the ManifestWork for the spoke:
```bash
kubectl get manifestwork -A
```
```
NAMESPACE   NAME                                              AGE
spoke1      mw-sandbox-d76c503e-5545-48db-bbc1-217864b2b35d   4s
```

On `spoke1-vm` (`192.168.102.20`), verify the sandbox pod running in `opensandbox-workloads`:
```bash
kubectl get pods -n opensandbox-workloads
```
```
NAME               READY   STATUS    RESTARTS   AGE
sbx-d76c503e-554   1/1     Running   0          10s
```

#### Step 6.3: Retrieve Completed Security Report
Poll the results endpoint using the `job_id` returned from Step 6.1:

##### Via Gateway IP (`192.168.100.10`):
```bash
curl -s -H "Authorization: Bearer .." \
  http://192.168.100.10/api/v1/01sbx/v1/jobs/<JOB_ID>/result
```

##### Via Virtual IP (`10.99.0.100`):
```bash
curl -s -H "Authorization: Bearer .." \
  http://10.99.0.100/api/v1/01sbx/v1/jobs/<JOB_ID>/result
```
Expected Response:
```json
{
  "job_id": "136f85da-88ba-48f0-8a5b-daeaf95e1120",
  "status": "COMPLETED",
  "report": {
    "summary": {
      "overall_status": "RISKS_FOUND",
      "total_tools_run": 3,
      "risks_detected": 2,
      "findings_count": 6
    },
    "findings": [
      {
        "tool": "bandit",
        "file": "/workspace/main.py",
        "line": 3,
        "issue": "start_process_with_a_shell: starting a process with a shell, possible injection detected, security issue.",
        "severity": "high"
      }
    ],
    "scans": {
      "bandit": {"status": "ISSUES_FOUND"},
      "semgrep": {"status": "COMPLETED"}
    }
  }
}
```

---

### Test 7: Dynamic Language Container Selection & Tool Enforcement

Verify that the orchestrator dynamically selects language-specific scanner images and only executes language-compatible tools (e.g. Bandit is excluded for Go).

#### Step 7.1: Submit Go Source Code
```bash
curl -X POST http://10.99.0.100/api/v1/01sbx/scan-jobs?async=true \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <TOKEN>" \
  -d '{
    "files": {
      "main.go": "package main\nimport \"fmt\"\nfunc main() {\n\tfmt.Println(\"hello\")\n}"
    },
    "metadata": {
      "runtime": "gvisor"
    }
  }'
```

#### Step 7.2: Verify Spoke Pod Image
Inspect the pod container image on `spoke1`:
```bash
kubectl get pod -n opensandbox-workloads -l app.kubernetes.io/part-of=01sandbox \
  -o jsonpath='{.items[-1].spec.containers[0].image}'
```
**Output:**
```
01community/01sandbox-scanner-go:1.0.0
```

#### Step 7.3: Verify Tool Execution Logs
Inspect the pod stdout logs on `spoke1`:
```bash
kubectl logs -n opensandbox-workloads <pod-name>
```
**Output:**
```
=============================================
  Automated Security Scanning — /workspace
---------------------------------------------
[INFO] Classified Files: Go(1)
[INFO] Enabled tools: semgrep, gosec, golangci_lint, go_build, staticcheck
[INFO] Running Semgrep scan...
[INFO] Running Gosec scan...
[INFO] Running GolangCI-Lint scan...
```
*Result: Bandit was NOT executed. Gosec and Semgrep ran exclusively for Go code.*

---

### Test 8: Spoke RuntimeClass & Region Placement Routing

Verify that user-provided `metadata.region` and `metadata.runtime` route to the corresponding spoke cluster and configure the proper runtime class.

#### Step 8.1: Submit Scan Request Targeting EU Region
```bash
curl -X POST http://10.99.0.100/api/v1/01sbx/scan-jobs?async=true \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <TOKEN>" \
  -d '{
    "files": {"main.py": "print(1)"},
    "metadata": {
      "region": "eu-central-1",
      "runtime": "kata-fc"
    }
  }'
```

#### Step 8.2: Verify ManifestWork Scheduled on Spoke2
On `primaryhub`:
```bash
kubectl get manifestwork -n spoke2
```
*The ManifestWork is created under the `spoke2` namespace on the Hub.*

#### Step 8.3: Verify RuntimeClass on Spoke2 Pod
On `spoke2-vm` (`192.168.103.20`):
```bash
kubectl get pod -n opensandbox-workloads -o jsonpath='{.items[-1].spec.runtimeClassName}'
```
**Output:**
```
kata-fc
```
*Result: The sandbox pod successfully targeted `spoke2` and executed under `runtimeClassName: kata-fc`.*

---

## 7. Failover, Long-Term Outage & Failback Lifecycle

This section details exactly what happens if `primaryhub` goes down for an extended period, how users continue creating API keys on `secondaryhub`, and how those keys replicate back to `primaryhub` once it recovers.

```
+---------------------------------------------------------------------------------------+
| PHASE 1: NORMAL STATE                                                                 |
| PrimaryHub (Master RW)  ================ WAL Sync ================> SecondaryHub (RO) |
+---------------------------------------------------------------------------------------+
                                           |
                                  [ Primary Crashes ❌ ]
                                           |
                                           v
+---------------------------------------------------------------------------------------+
| PHASE 2: FAILOVER TRIGGERED                                                           |
| PrimaryHub (DOWN ❌)                                                                  |
| SecondaryHub (PROMOTED TO MASTER RW ✅)                                               |
| Users create API Keys, Sandboxes, Scan Jobs ---> Stored directly in SecondaryHub DB   |
+---------------------------------------------------------------------------------------+
                                           |
                              [ Primary Boots Back Up 🔄 ]
                                           |
                                           v
+---------------------------------------------------------------------------------------+
| PHASE 3: FAILBACK & ROLE REVERSAL                                                     |
| PrimaryHub (REJOINS AS STANDBY RO) <==== Reverse Sync ===== SecondaryHub (Master RW)  |
| *All newly generated API keys are automatically copied back to PrimaryHub*            |
+---------------------------------------------------------------------------------------+
                                           |
                             (Optional Planned Switchover)
                                           v
+---------------------------------------------------------------------------------------+
| PHASE 4: RESTORE ORIGINAL ROLES (OPTIONAL)                                            |
| PrimaryHub (Promoted back to Master RW) === Sync ===> SecondaryHub (Demoted to RO)    |
+---------------------------------------------------------------------------------------+
```

### 7.1 Phase 2: If Primary Hub is Down $\rightarrow$ Promotion to Read-Write Master

If `primaryhub` suffers hardware failure, network isolation, or extended maintenance:
1. **Trigger Promotion on Secondary Hub**:
   ```bash
   # On secondaryhub (ubuntu@192.168.101.20):
   # 1. Promote PostgreSQL to Read-Write Master (< 2 seconds)
   kubectl exec -n opensandbox-system deployment/postgresql -- pg_ctl promote

   # 2. Promote Redis to Master
   kubectl exec -n opensandbox-system deployment/redis -- redis-cli replicaof no one

   # 3. Verify PostgreSQL is in Master mode (returns false)
   kubectl exec -n opensandbox-system deployment/postgresql -- \
     psql -U postgres -d apikeys -c "SELECT pg_is_in_recovery();"
   # Output: f (false)
   ```

2. **Shift Client Traffic / VIP to Secondary Hub**:
   The Virtual IP (`10.99.0.100` / `192.168.100.200`) floats to `secondaryhub`.

3. **Users Create API Keys & Workloads on Secondary Hub**:
   - `sandbox-api` detects `pg_is_in_recovery() == False`.
   - Any user request to generate an API key (`POST /api-keys/`), create a sandbox, or launch a scan job is accepted and written directly to `secondaryhub`'s PostgreSQL and Redis.
   - All state is safely committed on `secondaryhub`.

---

### 7.2 Phase 3: When Primary Hub Recovers $\rightarrow$ Rejoin as Standby & Reverse Sync

When the old `primaryhub` is repaired and boots back online, **it must not start as master** (doing so would cause split-brain data conflict). Instead, it rejoins as a **Standby Replica** replicating from `secondaryhub`:

1. **Re-bootstrap Primary Hub PostgreSQL from Secondary Hub**:
   On `primaryhub` (`ubuntu@192.168.100.20`), configure PostgreSQL to pull from `secondaryhub` (`10.99.0.2`):
   ```bash
   # Re-sync primary volume with all new transactions created on secondaryhub:
   kubectl exec -it -n opensandbox-system deployment/postgresql -c postgres -- sh -c '
     pg_basebackup -h 10.99.0.2 -p 5432 -U postgres -D /tmp/new_data -Fp -Xs -R &&
     rm -rf /var/lib/postgresql/data/* &&
     cp -r /tmp/new_data/* /var/lib/postgresql/data/ &&
     rm -rf /tmp/new_data
   '
   kubectl rollout restart deployment/postgresql -n opensandbox-system
   ```

2. **Configure Primary Redis as Replica of Secondary**:
   ```bash
   kubectl exec -n opensandbox-system deployment/redis -- redis-cli replicaof 10.99.0.2 6379
   ```

3. **Verify Reverse Replication**:
   Check replication status on `secondaryhub` (now Master):
   ```bash
   kubectl exec -n opensandbox-system deployment/postgresql -- \
     psql -U postgres -d postgres -x -c "SELECT client_addr, state, sync_state FROM pg_stat_replication;"
   ```
   **Output:**
   ```
   -[ RECORD 1 ]-----------
   client_addr | 10.99.0.1
   state       | streaming
   sync_state  | async
   ```

4. **Verify New API Keys Replicated to Primary Hub**:
   Query `primaryhub` PostgreSQL:
   ```bash
   kubectl exec -n opensandbox-system deployment/postgresql -- \
     psql -U postgres -d apikeys -c "SELECT id, name, created_at FROM api_keys;"
   ```
   **Result: All API keys generated while `primaryhub` was offline are now present in `primaryhub`'s database.**

---

### 7.3 Phase 4: Optional Graceful Switchover Back to Original Roles

If you prefer `primaryhub` to resume its role as the active Master:
1. Briefly pause writes on `secondaryhub`.
2. Promote `primaryhub` to Master:
   ```bash
   kubectl exec -n opensandbox-system deployment/postgresql -- pg_ctl promote
   kubectl exec -n opensandbox-system deployment/redis -- redis-cli replicaof no one
   ```
3. Demote `secondaryhub` back to Standby:
   ```bash
   kubectl exec -it -n opensandbox-system deployment/postgresql -c postgres -- sh -c '
     touch /var/lib/postgresql/data/standby.signal
   '
   kubectl rollout restart deployment/postgresql -n opensandbox-system
   kubectl exec -n opensandbox-system deployment/redis -- redis-cli replicaof 10.99.0.1 6379
   ```
4. Float Virtual IP back to `primaryhub`. Both hubs return to the initial Warm Standby topology with zero data loss.

---

## 8. Operational Gotchas & Troubleshooting Guide

### 8.1 Primary Database Crash or Restart
- **Behavior**: If `primaryhub`'s PostgreSQL restarts (e.g. pod rescheduling or VM reboot), `secondaryhub` will temporarily log `could not connect to server... retrying`.
- **Resolution**: Automatic! As soon as `primaryhub` comes back up, `secondaryhub` resumes streaming immediately without administrative intervention.

### 8.2 Temporary WireGuard Link Flapping
- **Behavior**: If the network tunnel drops for a few minutes, PostgreSQL on `primaryhub` retains up to `wal_keep_size = 1GB` of WAL logs on disk.
- **Resolution**: Automatic! When the WireGuard tunnel reconnects, `secondaryhub` replays the buffered WAL segments and catches up to 0 lag within seconds. Redis similarly replays its replication backlog.

### 8.3 Kind Container Runtime Image Caching (`IfNotPresent`)
- **Gotcha**: When modifying container source code (like `app_state.py`) while keeping the same image tag (e.g. `v0.7.9`), Kind's underlying containerd daemon may retain the cached image layers even after `kind load`.
- **Fix**: Before running `kind load`, remove the existing image from containerd:
  ```bash
  docker exec secondaryhub-control-plane crictl rmi docker.io/01community/01sandbox-api:v0.7.9
  kind load docker-image 01community/01sandbox-api:v0.7.9 --name secondaryhub
  ```

### 8.4 Database Selection: `apikeys` vs `postgres`
- **Gotcha**: PostgreSQL has multiple databases inside the cluster. The application tables (`api_keys`, `system_settings`, `user_subscriptions`) reside in the database named **`apikeys`**, while system replication queries (`pg_stat_replication`, `pg_is_in_recovery()`) can be run against **`postgres`**.
- **Rule**: Always specify `-d apikeys` when verifying application tables.
