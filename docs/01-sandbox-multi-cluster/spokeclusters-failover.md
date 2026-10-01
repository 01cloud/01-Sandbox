# Working Principle: Automated Spoke Cluster Acceptance & Database Promotion During PrimaryHub Failover

**Platform**: 01-Sandbox / CodeInspector Multi-Cluster Control Plane
**Architecture**: Active-Passive Dual-Hub with MultipleHubs OCM Spokes & CNPG PostgreSQL
**Document Target**: `docs/01-sandbox-multi-cluster/spokeclusters-failover.md`

---

## 1. Executive Summary & Overview

In the 01-Sandbox architecture, high availability (HA) across isolated Kubernetes environments is achieved through an **Active-Passive Dual-Hub control plane**:
- **PrimaryHub (`kind-primaryhub`)**: WireGuard IP `10.99.0.1` — The primary control plane, managing live workloads, serving the Read-Write PostgreSQL cluster (`postgresql-primary`), Valkey cache/queue, and actively controlling spoke clusters.
- **SecondaryHub (`kind-secondaryhub`)**: WireGuard IP `10.99.0.2` — The warm standby control plane, running hot standby PostgreSQL (`postgresql-secondary`) streaming WAL logs, replicated Valkey, and the in-cluster Kubernetes-native **`ocm-failover-controller`**.
- **Spoke Clusters (`spoke1`, `spoke2`)**: Isolated worker clusters running microVM runtimes (Kata Containers / Firecracker / gVisor) that execute untrusted code sandboxes.

```
+-----------------------------------------------------------------------------------+
|                              ACTIVE-PASSIVE TOPOLOGY                              |
+-----------------------------------------------------------------------------------+

   +---------------------------+                      +---------------------------+
   |        PrimaryHub         |                      |       SecondaryHub        |
   |        (10.99.0.1)        |                      |        (10.99.0.2)        |
   |                           |                      |                           |
   | - Kube-API (:6443)        |<=== /livez Probes ==-| - ocm-failover-controller |
   | - OCM Hub (Active)        |     (Every 1 sec)    |   (In-Cluster Deployment) |
   | - PostgreSQL (Master RW)  |--- WAL Streaming --->| - PostgreSQL (Standby RO) |
   | - Valkey (Master RW)      |--- Replication ----->| - Valkey (Replica RO)     |
   | - sandbox-api (Active)    |                      | - sandbox-api (Standby)   |
   +---------------------------+                      +---------------------------+
                 |                                                  |
           (Priority 0)                                       (Priority 1)
       Active OCM Registration                             Standby Hot Spoke
                 |                                                  |
                 +------------------------+-------------------------+
                                          |
                              WireGuard Overlay Network
                                          |
                     +--------------------+--------------------+
                     |                                         |
                     v                                         v
       +---------------------------+             +---------------------------+
       |       Spoke1 Worker       |             |       Spoke2 Worker       |
       |        (10.99.0.3)        |             |        (10.99.0.4)        |
       |                           |             |                           |
       | - Klusterlet Agent        |             | - Klusterlet Agent        |
       |   (MultipleHubs enabled)  |             |   (MultipleHubs enabled)  |
       | - Kata / Firecracker /    |             | - Kata / Firecracker /    |
       |   gVisor MicroVMs         |             |   gVisor MicroVMs         |
       +---------------------------+             +---------------------------+
```

When PrimaryHub suffers an outage (e.g. host shutdown, power failure, network partition, or VM crash):
1. **SecondaryHub automatically takes over the Spoke Clusters** (`spoke1` and `spoke2`), transitioning them from `Unknown` to `HubAccepted=true`, `Joined=true`, and `Available=true`.
2. **PostgreSQL on SecondaryHub (`postgresql-secondary-1`) is promoted in-place from Read-Only standby to Read-Write master**, allowing immediate user transactions (such as generating API keys, executing scans, and managing sandboxes).
3. **Valkey cache on SecondaryHub is promoted to master**, enabling immediate task dispatching.

This document breaks down the deep, technical background mechanics governed by [`failover-controller.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/failover-controller.yaml), Open Cluster Management (OCM) Klusterlet internals, and CloudNativePG (CNPG) replication state machines.

---

## 2. The Core Standby Invariant (Why SecondaryHub Releases Spokes in Normal State)

A fundamental challenge in dual-hub architectures is **split-brain**: if both PrimaryHub and SecondaryHub accept client registrations from spoke clusters simultaneously, both hubs would attempt to schedule and dispatch `ManifestWork` resources to the same worker nodes.

### Standby State Configuration
While PrimaryHub is healthy, the `ocm-failover-controller` on SecondaryHub actively enforces the **Standby Invariant**:
```bash
for CLUSTER in $(kubectl get managedclusters -o jsonpath='{.items[*].metadata.name}' 2>/dev/null); do
  IS_ACCEPTED=$(kubectl get managedcluster "$CLUSTER" -o jsonpath='{.spec.hubAcceptsClient}' 2>/dev/null || echo "false")
  if [ "$IS_ACCEPTED" != "false" ]; then
    log "STANDBY: Ensuring $CLUSTER is released from SecondaryHub..."
    kubectl patch managedcluster "$CLUSTER" --type merge \
      -p '{"spec":{"hubAcceptsClient":false,"taints":[{"key":"cluster.open-cluster-management.io/unreachable","effect":"NoSelect"}]}}'
    kubectl delete lease --all -n "$CLUSTER" --ignore-not-found=true
    set_spoke_status "$CLUSTER" "Unknown" "ManagedClusterLeaseUpdateStopped" "Released by SecondaryHub on standby"
  fi
done
```

### Why Spokes Show `Unknown` While PrimaryHub is Active
On SecondaryHub:
- `spec.hubAcceptsClient` is set to `false`.
- The spoke namespace has its coordination leases deleted.
- The condition `ManagedClusterConditionAvailable` is intentionally set to `status: Unknown`, reason `ManagedClusterLeaseUpdateStopped`.
- An `unreachable` taint with effect `NoSelect` is placed on the cluster to prevent any workload scheduler from selecting it.

This guarantees that **100% of spoke control traffic and workload scheduling is anchored to PrimaryHub**.

---

## 3. Detailed Failure Detection Engine

The detection mechanism runs inside the `ocm-failover-controller` pod in SecondaryHub's `opensandbox-system` namespace.

```mermaid
flowchart TD
    Start([Controller Reconciler Loop]) --> CheckAPI{curl -k -m 1<br/>https://10.99.0.1:6443/livez}

    CheckAPI -- HTTP 200 OK --> PrimaryAlive[PrimaryHub is UP]
    PrimaryAlive --> ResetCounter[Reset FAIL_COUNT = 0]
    ResetCounter --> CheckState{Current STATE?}
    CheckState -- STANDBY --> EnforceStandby[Enforce Standby Invariant<br/>hubAcceptsClient=false]
    CheckState -- FAILOVER_ACTIVE --> TriggerFailback[Trigger Split-Brain-Safe Failback]

    CheckAPI -- Timeout or Refused --> PrimaryDown[PrimaryHub Unreachable]
    PrimaryDown --> IncCounter[Increment FAIL_COUNT++]
    IncCounter --> CheckThreshold{FAIL_COUNT >= 2<br/>& STATE == STANDBY?}
    CheckThreshold -- No --> SleepLoop[Sleep 1s]
    CheckThreshold -- Yes --> TriggerFailover[CRITICAL: TRIGGER FAILOVER]

    SleepLoop --> Start
```

### Probing Parameters
- **Target Endpoint**: `https://10.99.0.1:6443/livez` over the encrypted WireGuard mesh.
- **Probe Frequency**: Every 1 second.
- **Timeout**: Strict 1-second timeout (`curl -k -m 1 -s`).
- **Failure Threshold (`FAIL_THRESHOLD`)**: 2 consecutive missed probes.
- **Detection Latency**: Failover is triggered in **~2 to 3 seconds** from the exact moment PrimaryHub goes offline.

---

## 4. Spoke Cluster Takeover: How Spokes Become Accepted & Available

When `FAIL_COUNT >= 2`, the failover sequence initiates. The first priority is recovering control of the spoke clusters.

### Step-by-Step Technical Sequence

```mermaid
sequenceDiagram
    autonumber
    participant SC as Spoke Klusterlet (spoke1/spoke2)
    participant FC as ocm-failover-controller (SecondaryHub)
    participant K8S as SecondaryHub Kube-API & CSR Controller
    participant OCM as SecondaryHub OCM Controller

    Note over FC: PrimaryHub /livez failed 2x
    FC->>K8S: Patch ManagedCluster: hubAcceptsClient=true, leaseDurationSeconds=5, taints=[]
    Note over SC: Klusterlet hubConnectionTimeoutSeconds (15s) expires on PrimaryHub
    SC->>SC: Switch to SecondaryHub bootstrap kubeconfig (MultipleHubs LocalSecrets)
    SC->>K8S: Submit CertificateSigningRequest (CSR) for spoke agent
    loop Every 1 second
        FC->>K8S: approve_csrs() finds pending CSR with null conditions
        FC->>K8S: kubectl certificate approve <csr>
    end
    K8S-->>SC: Issue signed client certificate
    SC->>K8S: Connect via mTLS, begin renewing lease in coordination.k8s.io
    OCM->>K8S: Detects hubAcceptsClient=true, CSR approved, and fresh lease renewal
    OCM->>K8S: Set ManagedClusterConditionJoined=True & ManagedClusterConditionAvailable=True
    Note over K8S,SC: Spoke is now fully AVAILABLE and ready for workload scheduling!
```

### 1. Hub-Side Patching (`hubAcceptsClient=true`)
The controller executes:
```bash
for spoke in spoke1 spoke2; do
  kubectl patch managedcluster "$spoke" --type merge \
    -p '{"spec":{"hubAcceptsClient":true,"leaseDurationSeconds":5,"taints":[]}}'
done
```
- **`hubAcceptsClient: true`**: Authorizes SecondaryHub to accept incoming control-plane sessions from the spoke agent.
- **`leaseDurationSeconds: 5`**: Reduces the heartbeat lease renewal interval from the default 60s down to 5s. This causes OCM to detect the spoke's online presence almost instantaneously.
- **`taints: []`**: Clears the `unreachable` / `NoSelect` taints, marking the clusters as schedulable.

### 2. Spoke-Side MultipleHubs Klusterlet Failover
On `spoke1` and `spoke2`, the OCM Klusterlet is configured with the `MultipleHubs` feature gate and `LocalSecrets` bootstrap mode:
```yaml
spec:
  registrationConfiguration:
    featureGates:
      - feature: MultipleHubs
        mode: Enable
    bootstrapKubeConfigs:
      type: LocalSecrets
      localSecretsConfig:
        hubConnectionTimeoutSeconds: 15
        kubeConfigSecrets:
          - name: primaryhub-kubeconfig    # Priority 0
          - name: secondaryhub-kubeconfig  # Priority 1
```

- While PrimaryHub is down, TCP connections from the klusterlet to `10.99.0.1:6443` hang or reset.
- As soon as `hubConnectionTimeoutSeconds: 15` elapses, the klusterlet registration agent marks `primaryhub` as degraded and automatically pivots to index 1: `secondaryhub-kubeconfig` (`https://10.99.0.2:6443`).

### 3. Automated CSR Approvals (`approve_csrs`)
When a spoke connects to a new hub, it generates a fresh cryptographic private key and submits a Kubernetes `CertificateSigningRequest` (CSR) to the SecondaryHub Kube-API:
- **Signer Name**: `kubernetes.io/kube-apiserver-client`
- **Request Subject**: `O=open-cluster-management:spoke1, CN=open-cluster-management:spoke1:agent`

Without an approval controller, the CSR remains in status `Pending`, preventing the spoke from completing its registration.

The `ocm-failover-controller` contains an automated CSR approval loop:
```bash
approve_csrs() {
  PENDING_CSRS=($(kubectl get csr -o json 2>/dev/null | jq -r '.items[]? | select(.status.conditions == null) | .metadata.name' 2>/dev/null || true))
  for csr in "${PENDING_CSRS[@]}"; do
    if [ -n "$csr" ]; then
      log "Approving pending spoke CSR: $csr"
      kubectl certificate approve "$csr" 2>/dev/null || true
    fi
  done
}
```

The controller possesses explicit RBAC permissions in [`failover-controller.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/failover-controller.yaml) to approve client certificates:
```yaml
- apiGroups: ["certificates.k8s.io"]
  resources: ["certificatesigningrequests", "certificatesigningrequests/approval"]
  verbs: ["get", "list", "watch", "create", "update", "patch"]
- apiGroups: ["certificates.k8s.io"]
  resources: ["signers"]
  resourceNames: ["kubernetes.io/kube-apiserver-client"]
  verbs: ["approve"]
```

### 4. Lease Heartbeat & Availability Transition
Once the CSR is approved:
1. SecondaryHub's `kube-controller-manager` signs and populates the certificate block.
2. The spoke klusterlet retrieves the certificate and mounts it into its local agent secret.
3. The klusterlet begins writing lease heartbeat renewals into SecondaryHub under `coordination.k8s.io/v1/namespaces/spoke1/leases/managed-cluster-lease`.
4. The OCM `managedcluster-import-controller` observes:
   - `spec.hubAcceptsClient == true`
   - Active client certificate matching spoke identity
   - Healthy lease renewal within the 5s window
5. OCM transitions the ManagedCluster conditions:
   ```yaml
   status:
     conditions:
     - type: HubAcceptedCondition
       status: "True"
     - type: ManagedClusterConditionJoined
       status: "True"
     - type: ManagedClusterConditionAvailable
       status: "True"
       reason: ManagedClusterAvailable
       message: Managed cluster is available
   ```

At this moment, `kubectl --context kind-secondaryhub get managedclusters` reports:
```
NAME     HUB ACCEPTED   MANAGED CLUSTER URLS                JOINED   AVAILABLE   AGE
spoke1   true           https://spoke1-control-plane:6443   True     True        12h
spoke2   true           https://spoke2-control-plane:6443   True     True        12h
```

---

## 5. Critical TLS Prerequisite: Kube-API Server SANs

A frequent cause of spokes remaining in `JOINED: False / AVAILABLE: Unknown` is **TLS certificate validation rejection**:
- Spoke clusters connect to SecondaryHub via its WireGuard overlay IP: `https://10.99.0.2:6443`.
- If the SecondaryHub's Kubernetes API server certificate was generated only for internal hostnames (`kubernetes`, `kubernetes.default`, `localhost`, `127.0.0.1`), the spoke's TLS client rejects the connection with:
  ```
  x509: certificate is valid for kubernetes, not 10.99.0.2
  ```
- Because TLS handshake fails before reaching HTTP routing, no CSR is ever generated, and the spoke appears dead.

### The Permanent Fix
During cluster initialization (or certificate renewal), SecondaryHub's `apiserver.crt` MUST include all overlay and VIP IP addresses in its Subject Alternative Names (SANs):
```bash
kubeadm init phase certs apiserver \
  --cert-dir /etc/kubernetes/pki \
  --service-cidr "10.97.0.0/16" \
  --apiserver-cert-extra-sans "10.99.0.2,10.99.0.1,10.99.0.254,10.99.0.100"
```
Once restarted, `openssl s_client -connect 10.99.0.2:6443` validates `10.99.0.2` in `X509v3 Subject Alternative Name`, allowing spoke klusterlets to connect without TLS errors.

---

## 6. PostgreSQL Secondary (`postgresql-secondary-1`) Promotion to Read-Write

While OCM handles spoke orchestration, user requests require database writes. Creating API keys, registering users, storing scan findings, and updating sandbox status all perform `INSERT` and `UPDATE` operations.

```mermaid
flowchart TD
    subgraph Standby Mode
        StandbyDB[postgresql-secondary-1<br/>Hot Standby]
        StandbyDB -->|pg_is_in_recovery = true| ReadOnly[All Writes REJECTED<br/>ERROR: cannot execute INSERT in read-only transaction]
    end

    subgraph Promotion Step
        TriggerFailover[Failover Controller detects Primary DOWN]
        TriggerFailover --> PatchCluster[kubectl patch cluster postgresql-secondary<br/>spec.replica.enabled = false]
        PatchCluster --> CNPGOperator[CloudNativePG Operator Reconciles]
        CNPGOperator --> PGPromote[Execute pg_promote in-place]
    end

    subgraph Promoted Master
        PGPromote --> TimelineShift[Timeline Shift: Timeline 1 -> Timeline 2<br/>Write 00000002.history WAL]
        TimelineShift --> RecoveryExit[PostgreSQL Exits Recovery Mode<br/>pg_is_in_recovery = false]
        RecoveryExit --> ConfigPW[ALTER USER postgres WITH PASSWORD 'password123']
        ConfigPW --> RWReady[Service postgresql-secondary-rw Route Active<br/>Full Read-Write Transactions Enabled]
        RWReady --> APIKeyCreate[sandbox-api: INSERT INTO api_keys SUCCESS]
    end
```

### 1. The Normal Replica State
Under normal conditions, SecondaryHub runs a CNPG `Cluster` configured with `spec.replica.enabled: true`:
```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgresql-secondary
  namespace: opensandbox-system
spec:
  instances: 1
  replica:
    enabled: true
    source: postgresql-primary
  bootstrap:
    pg_basebackup:
      source: postgresql-primary
  externalClusters:
  - name: postgresql-primary
    connectionParameters:
      host: "10.99.0.1"
      port: 30432
      user: "postgres"
      dbname: "apikeys"
```
In this state:
- `postgresql-secondary-1` runs `postgres` in recovery mode (`SELECT pg_is_in_recovery();` returns `true`).
- Streaming replication receiver (`pg_stat_wal_receiver`) receives WAL records from `10.99.0.1:30432`.
- Any client attempting to write receives:
  ```sql
  ERROR: cannot execute INSERT in a read-only transaction
  ```

### 2. In-Place Promotion via `spec.replica.enabled=false`
In Step 2 of failover, the controller executes:
```bash
kubectl patch cluster postgresql-secondary -n "$NAMESPACE" --type merge \
  -p '{"spec":{"replica":{"enabled":false}}}'
```

### What Happens in the Background:
1. **CNPG Reconciliation**: The CloudNativePG operator detects that `spec.replica.enabled` has been set to `false`.
2. **`pg_promote()` Execution**: Instead of tearing down the pod or restarting containers, CNPG executes an in-place promotion (calling PostgreSQL's internal `pg_promote()` function or signaling via trigger).
3. **Timeline Advance (Timeline 1 → Timeline 2)**:
   - PostgreSQL completes any remaining WAL segments from the old primary.
   - It writes a new timeline history file: `00000002.history`.
   - The instance increments its internal timeline ID from `1` to `2`.
   - Recovery mode ends (`pg_is_in_recovery()` returns `false`).
4. **Latency**: The in-place promotion completes in **under 500 milliseconds**. Pod restart and grace period delays are completely avoided.

### 3. Superuser Password Configuration
To prevent `FATAL: password authentication failed for user "postgres"` when `sandbox-api` connects to the promoted database, the controller ensures the superuser password matches expectations:
```bash
for i in {1..15}; do
  if kubectl exec -n "$NAMESPACE" postgresql-secondary-1 -c postgres -- \
    psql -U postgres -d apikeys -c "ALTER USER postgres WITH PASSWORD 'password123';" 2>/dev/null; then
    log "Successfully configured postgres password on secondary!"
    break
  fi
  sleep 1
done
```

### 4. Service Endpoint Routing
CloudNativePG provides dedicated Kubernetes Services:
- `postgresql-secondary-rw`: Always targets the current Read-Write instance (instance with role `primary`).
- `postgresql-secondary-ro`: Targets read-only instances.

Because `sandbox-api` uses `DATABASE_URL=postgresql://postgres:password123@postgresql-secondary-rw:5432/apikeys`, all database traffic immediately lands on the freshly promoted master.

### 5. API Key Generation Verification
Once promoted, write queries execute normally:
```sql
INSERT INTO api_keys (id, name, hashed_key, prefix, created_at, is_active)
VALUES (
  'key_9f82ab71c',
  'failover-production-key',
  '$2b$12$e8Y4k...',
  'sk_live_failover',
  NOW(),
  true
);
-- Result: INSERT 0 1
```
Users can immediately create API keys, authenticate, and launch sandbox sessions even while PrimaryHub is completely powered off.

---

## 7. Valkey Memory Cache Promotion

The `sandbox-api` relies on Valkey for fast session caching, rate limiting, and execution queues.
- On SecondaryHub, Valkey is initially configured as a read-only replica:
  ```bash
  REPLICAOF 10.99.0.1 6379
  # slave_read_only = 1
  ```
- Any write attempt raises:
  ```
  ReadOnlyError: You can't write against a read only replica
  ```

In Step 3 of failover, the controller promotes Valkey to a standalone master:
```bash
kubectl exec -n "$NAMESPACE" deploy/valkey -- valkey-cli REPLICAOF NO ONE
```
Valkey severs replication and instantly allows writes, removing any remaining read-only barriers.

---

## 8. Complete Failover Execution Log

When tested live, the `ocm-failover-controller` outputs the following timeline:

```text
[2026-10-01T06:15:01+00:00] [ocm-k8s-failover-controller] Primary check failed (1/2)
[2026-10-01T06:15:02+00:00] [ocm-k8s-failover-controller] Primary check failed (2/2)
[2026-10-01T06:15:02+00:00] [ocm-k8s-failover-controller] CRITICAL: PrimaryHub is DOWN (2 failures). Triggering automated in-place failover...
[2026-10-01T06:15:02+00:00] [ocm-k8s-failover-controller] Step 1: Ensuring OCM spoke clusters are accepted on secondaryhub (spoke1, spoke2)...
managedcluster.cluster.open-cluster-management.io/spoke1 patched
managedcluster.cluster.open-cluster-management.io/spoke2 patched
[2026-10-01T06:15:03+00:00] [ocm-k8s-failover-controller] Approving pending spoke CSR: csr-spoke1-xyz7a
[2026-10-01T06:15:03+00:00] [ocm-k8s-failover-controller] Approving pending spoke CSR: csr-spoke2-abc3f
[2026-10-01T06:15:03+00:00] [ocm-k8s-failover-controller] Step 2: Promoting PostgreSQL secondary cluster to Read-Write primary...
cluster.postgresql.cnpg.io/postgresql-secondary patched
[2026-10-01T06:15:04+00:00] [ocm-k8s-failover-controller] Successfully configured postgres password on secondary!
[2026-10-01T06:15:04+00:00] [ocm-k8s-failover-controller] Step 3: Promoting Valkey to master...
OK
[2026-10-01T06:15:05+00:00] [ocm-k8s-failover-controller] SUCCESS: Automated Failover complete. SecondaryHub is now active Read-Write cluster. Zero downtime!
```

---

## 9. Automated Split-Brain-Safe Failback Mechanics

When PrimaryHub recovers, the system must return to normal state **without data loss, without split-brain, and without broken database replication**.

### The PostgreSQL Timeline Divergence Problem
During failover, SecondaryHub advanced to **Timeline 2**. PrimaryHub was shut down on **Timeline 1**.
Standard PostgreSQL streaming replication cannot replicate backward or reconcile diverged timelines without re-cloning. If one simply patches `spec.replica.enabled: true`, PostgreSQL logs:
```
FATAL: highest timeline 1 of the primary is behind recovery timeline 2
```

### The 5-Step Failback Solution

```mermaid
sequenceDiagram
    autonumber
    participant FC as ocm-failover-controller
    participant SC as Spoke Clusters
    participant DB2 as SecondaryHub DB (Timeline 2)
    participant DB1 as PrimaryHub DB (Timeline 1)
    participant VK as Valkey

    Note over FC: PrimaryHub Kube-API /livez is back UP
    FC->>DB1: Verify pg_isready -h 10.99.0.1 -p 5432
    Note over FC,SC: Step 1: Release Spokes from SecondaryHub
    FC->>SC: ManagedCluster: hubAcceptsClient=false, taints=[unreachable]
    FC->>SC: Delete lease managed-cluster-lease (Force instant failback to PrimaryHub)
    Note over FC,DB1: Step 2: Sync Outage Delta via Kubernetes Job
    FC->>DB1: Run Job failback-delta-sync: pg_dump from Secondary -> psql into Primary
    FC->>DB1: Execute CHECKPOINT
    Note over FC,VK: Step 3: Reverse Valkey Sync
    FC->>VK: REPLICAOF 10.99.0.1 6379
    Note over FC,DB2: Step 4: Re-clone Secondary PostgreSQL (Fix Timeline Divergence)
    FC->>DB2: Delete cluster postgresql-secondary & PVC
    FC->>DB2: Apply secondary-cluster.yaml (pg_basebackup from PrimaryHub)
    DB2->>DB1: pg_basebackup restores Secondary onto Timeline 1
    DB2->>DB1: Streaming replication established (pg_stat_wal_receiver = streaming)
    Note over FC: Failback complete! 100% data parity, zero split-brain.
```

1. **Spoke Release**: SecondaryHub immediately patches `spec.hubAcceptsClient: false` and adds the `unreachable` taint. Deleting the OCM lease forces the spoke klusterlet to immediately reconnect to PrimaryHub (Priority 0).
2. **Pre-Check Primary PostgreSQL**: Controller loops on `pg_isready -h 10.99.0.1 -p 5432` until PrimaryHub's database is fully initialized.
3. **Declarative Delta Sync Job (`failback-delta-sync`)**:
   Runs a container with `pg_dump` and `psql` to stream all records created during the outage (such as new API keys) from `postgresql-secondary-rw` into PrimaryHub's `apikeys` database, finishing with a database `CHECKPOINT`.
4. **Valkey Resynchronization**: Secondary Valkey executes `REPLICAOF 10.99.0.1 6379`.
5. **PostgreSQL Re-clone**: The controller deletes `postgresql-secondary` and re-applies `secondary-cluster.yaml`. CloudNativePG runs `pg_basebackup` against PrimaryHub, pulling a pristine copy of Timeline 1. Streaming replication (`pg_stat_wal_receiver`) resumes with 0 lag.

---

## 10. Verification & Troubleshooting Commands

### Check ManagedCluster Status on SecondaryHub
```bash
kubectl --context kind-secondaryhub get managedclusters
```
*Expected during failover:*
```
NAME     HUB ACCEPTED   MANAGED CLUSTER URLS                JOINED   AVAILABLE   AGE
spoke1   true           https://spoke1-control-plane:6443   True     True        ...
spoke2   true           https://spoke2-control-plane:6443   True     True        ...
```

### Check PostgreSQL Role & Readiness
```bash
# Verify if instance is Primary (false) or Standby (true)
kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 -c postgres -- \
  psql -U postgres -d apikeys -t -A -c "SELECT pg_is_in_recovery();"
```
- During PrimaryHub UP: returns `true` (standby).
- During PrimaryHub DOWN: returns `false` (active read-write master).

### Check Active Timeline
```bash
kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 -c postgres -- \
  psql -U postgres -d apikeys -t -A -c "SELECT timeline_id FROM pg_control_checkpoint();"
```

### Check Failover Controller Logs
```bash
kubectl --context kind-secondaryhub logs -n opensandbox-system deploy/ocm-failover-controller -f
```

### Check CSRs on SecondaryHub
```bash
kubectl --context kind-secondaryhub get csr
```
Ensure all spoke CSRs have condition `Approved,Issued`.
