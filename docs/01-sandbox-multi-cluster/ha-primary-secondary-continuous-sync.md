# High-Availability Primary-Secondary Hub Continuous Sync (Warm Standby)

This document provides the complete architectural blueprint, configuration specifications, Helm values profiles, and step-by-step implementation plan to establish continuous data synchronization and warm standby failover between **`primaryhub`** and **`secondaryhub`** with zero data loss and zero write conflicts.

---

## 1. Executive Summary & Problem Definition

In a multi-cluster Open Cluster Management (OCM) control plane architecture, running two hubs (`primaryhub` and `secondaryhub`) requires a unified state layer.

### The Problem of Naive Duplication
If standard Helm chart values are applied independently to `secondaryhub`:
- **Data Divergence**: Kubernetes will create isolated, independent PostgreSQL and Redis instances on each hub.
- **Split-Brain**: Both hubs would accept write operations independently, resulting in divergent primary keys, mismatched user API keys, and conflicting sandbox lifecycle records.
- **Broken Failover**: If `primaryhub` fails, `secondaryhub` would lack all historical scan jobs, user authentication records, and active state.

### The Solution: Warm Standby with Streaming Physical Replication
To eliminate write conflicts and guarantee continuous zero-data-loss synchronization (RPO ≈ 0):
1. **Single Source of Truth**: Only `primaryhub` acts as the Read-Write master during normal operations.
2. **Continuous Physical WAL Streaming**: `secondaryhub` runs PostgreSQL in **Hot Standby** mode, streaming every transaction from `primaryhub` continuously over the dedicated WireGuard tunnel (`10.99.0.0/24`).
3. **Read-Only Protection**: Standby PostgreSQL automatically rejects direct writes, preventing split-brain corruption.
4. **Pre-Warmed Microservices (Warm Standby)**: `sandbox-api` and `opensandbox-server` run with `replicaCount: 1` on `secondaryhub`, ready to serve live traffic the instant failover is triggered (RTO < 30s).

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

| Node | Physical LAN IP | WireGuard IP (`wg0`) | Role |
| :--- | :--- | :--- | :--- |
| **`primaryhub`** | `192.168.100.20` | `10.99.0.1` | Active Master |
| **`secondaryhub`** | `192.168.101.20` | `10.99.0.2` | Warm Standby |
| **Virtual IP (VIP)** | Floating | `10.99.0.100` / `192.168.100.200` | Client Endpoint |

---

## 3. Component Synchronization Architecture

### 3.1 PostgreSQL Continuous Physical Replication
1. **Primary Configuration (`primaryhub`)**:
   - `wal_level = replica`
   - `max_wal_senders = 10`
   - `wal_keep_size = 1GB`
   - `hot_standby = on`
   - Dedicated replication user: `replicator` with password authentication.
   - `pg_hba.conf` allows replication connections strictly from `10.99.0.0/24`.
2. **Standby Configuration (`secondaryhub`)**:
   - Bootstrapped via `pg_basebackup` with `-R` (auto-generates `standby.signal` and `primary_conninfo`).
   - `primary_conninfo = 'host=10.99.0.1 port=30432 user=replicator password=...'`
   - Rejects any direct write transactions with: `cannot execute INSERT in a read-only transaction`.
3. **Failover & Promotion**:
   - Executing `pg_ctl promote` or creating `/var/lib/postgresql/data/promote.trigger` instantly converts the standby database to Read-Write in < 2 seconds.

### 3.2 Redis In-Memory State Mirroring
- **Primary**: Standard Redis server bound to port `6379`.
- **Secondary**: Configured with `replicaof 10.99.0.1 30379`.
- Asynchronously replicates key-value entries, JWT session cache, and pub/sub events.

### 3.3 Open Cluster Management (Dual Klusterlet)
- `spoke1` and `spoke2` maintain simultaneous connections to both hubs using dual Klusterlet instances:
  - `klusterlet`: Active registration to `primaryhub`.
  - `klusterlet-secondaryhub`: Active registration to `secondaryhub`.
- When failover occurs, `secondaryhub` already holds valid certificates and placement decisions for all spokes with zero reconnection lag.

---

## 4. Implementation Steps

### Phase 1: Primary Hub Exposure Configuration
Expose the PostgreSQL and Redis services on `primaryhub` so they can be accessed over the secure WireGuard tunnel.

1. **Expose PostgreSQL Service via NodePort on `primaryhub`**:
   ```yaml
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
   ```

2. **Configure Replication User & Permissions**:
   ```sql
   -- Run inside primary PostgreSQL container
   CREATE ROLE replicator WITH REPLICATION LOGIN ENCRYPTED PASSWORD 'repl_secure_pass_01sbx';
   ```

3. **Expose Redis Service via NodePort on `primaryhub`**:
   ```yaml
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

---

### Phase 2: Secondary Helm Profile (`codeInspector/values-secondary.yaml`)

Create `codeInspector/values-secondary.yaml` to specify the standby configuration:

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
    replicaCount: 1 # Warm Standby
    image:
      repository: 01community/01sandbox-api
      tag: "v0.7.9"
      pullPolicy: IfNotPresent
  postgresql:
    storageClassName: "standard"
  sealedSecrets:
    enabled: false

opensandbox:
  server:
    replicaCount: 1 # Warm Standby
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

---

### Phase 3: Secondary Hub Initial Sync & Image Pre-loading

Execute on `ubuntu@192.168.101.20`:

1. **Synchronize Repository**:
   ```bash
   rsync -avz --exclude='.git' ubuntu@192.168.100.20:~/01-Sandbox/ ~/01-Sandbox/
   ```

2. **Transfer & Load Built Docker Images**:
   ```bash
   # Transfer images from primary
   docker save 01community/01sandbox-opensandbox-server:v0.7.10-ocm | ssh ubuntu@192.168.101.20 "docker load"
   docker save 01community/01sandbox-opensandbox-controller:v0.7.3 | ssh ubuntu@192.168.101.20 "docker load"
   docker save 01community/01sandbox-api:v0.7.9 | ssh ubuntu@192.168.101.20 "docker load"

   # Load into Kind secondaryhub
   kind load docker-image 01community/01sandbox-opensandbox-server:v0.7.10-ocm --name secondaryhub
   kind load docker-image 01community/01sandbox-opensandbox-controller:v0.7.3 --name secondaryhub
   kind load docker-image 01community/01sandbox-api:v0.7.9 --name secondaryhub
   ```

---

### Phase 4: PostgreSQL Standby Bootstrapping & Deployment

1. **Bootstrap Volume using `pg_basebackup`**:
   ```bash
   # Execute inside secondaryhub node or init container
   pg_basebackup -h 10.99.0.1 -p 30432 -U replicator -D /var/lib/postgresql/data -Fp -Xs -R
   ```
   *The `-R` parameter creates `standby.signal` and configures `primary_conninfo` automatically.*

2. **Deploy Standby Helm Release**:
   ```bash
   helm upgrade --install codeinspector ./codeInspector \
     -n opensandbox-system \
     --create-namespace \
     --values ./codeInspector/values.yaml \
     --values ./codeInspector/values-secondary.yaml
   ```

---

### Phase 5: Automated Failover Watchdog (Quorum Mechanism)

Deploy the Failover Controller on `secondaryhub` as defined in `docs/multi-cluster/ha-multicluster-failover.md`:
- Every 5 seconds, tests connectivity to `primaryhub`.
- If 3 consecutive failures occur, queries witness on `spoke1` (`http://192.168.122.52:9999`).
- **2-of-2 Quorum Confirmation**: If both `secondaryhub` AND `spoke1` confirm `primaryhub` is down, failover triggers:
  1. Promotes secondary PostgreSQL (`touch /var/lib/postgresql/data/promote.trigger`).
  2. Promotes secondary Redis (`redis-cli replicaof no one`).
  3. Floats the Virtual IP to `secondaryhub`.

---

## 5. End-to-End Verification Plan

### Test 1: Real-Time Data Replication Test (< 100ms)
1. Insert test entry on `primaryhub`:
   ```bash
   kubectl exec -n opensandbox-system deploy/postgresql -- psql -U postgres -d apikeys \
     -c "INSERT INTO system_settings (key, value) VALUES ('ha_sync_probe', 'sync_ok_val');"
   ```
2. Query entry on `secondaryhub`:
   ```bash
   kubectl exec -n opensandbox-system deploy/postgresql -- psql -U postgres -d apikeys \
     -c "SELECT key, value FROM system_settings WHERE key = 'ha_sync_probe';"
   ```
3. **Success Criteria**: Row appears on `secondaryhub` immediately.

### Test 2: Write-Conflict Protection Test
1. Attempt direct insert on `secondaryhub` PostgreSQL:
   ```bash
   kubectl exec -n opensandbox-system deploy/postgresql -- psql -U postgres -d apikeys \
     -c "INSERT INTO system_settings (key, value) VALUES ('illegal_write', 'error');"
   ```
2. **Success Criteria**: Transaction is rejected with:
   `ERROR: cannot execute INSERT in a read-only transaction`.

### Test 3: Streaming Replication Status Check
1. On `primaryhub`:
   ```sql
   SELECT client_addr, state, sync_state FROM pg_stat_replication;
   ```
2. **Success Criteria**: Shows `client_addr = 10.99.0.2` and `state = streaming`.

### Test 4: Warm Standby Microservices Health Test
1. Query health endpoint on `secondaryhub`:
   ```bash
   kubectl exec -n opensandbox-system deploy/sandbox-api -- python3 -c \
     "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').read().decode())"
   ```
2. **Success Criteria**: Returns `{"status":"healthy","status_code":200}`.
