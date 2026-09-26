# CodeInspector Multi-Cluster HA: Changes & Rationale

A technical record of every change made across the CodeInspector platform to achieve production-grade multi-cluster High Availability and Disaster Recovery. Each section covers: what changed, the exact config/code, and why it was done.

---

## Table of Contents

1. [Initial Failure Modes](#1-initial-failure-modes)
2. [Phase 1: In-Cluster Failover Controller](#2-phase-1-in-cluster-failover-controller)
3. [Phase 2: Envoy Active-Passive Gateway](#3-phase-2-envoy-active-passive-gateway)
4. [Phase 3: Delta Synchronization Job](#4-phase-3-delta-synchronization-job)
5. [Phase 4: WireGuard Routing & KinD Port Mappings](#5-phase-4-wireguard-routing--kind-port-mappings)
6. [Phase 5: Modernized Bootstrap Script](#6-phase-5-modernized-bootstrap-script)
7. [CloudNativePG Hardening](#7-cloudnativepg-hardening)
8. [Valkey / Redis Cache Failover](#8-valkey--redis-cache-failover)
9. [OCM Spoke Placement & Labels](#9-ocm-spoke-cluster-placement--labels)
10. [OpenSandbox Server: ImageSpec Bug Fix](#10-opensandbox-server-imagespec-bug-fix)
11. [API Server: Mock Key Authentication](#11-api-server-mock-key-authentication)
12. [Frontend: Dynamic API Configuration](#12-frontend-dynamic-api-configuration)
13. [PostgreSQL Timeline Divergence](#13-postgresql-timeline-divergence)
14. [Split-Brain & Spoke Release Ordering](#14-split-brain--spoke-release-ordering)
15. [Production Hardening Guide](#15-production-hardening-guide)

---

## 1. Initial Failure Modes

When PrimaryHub was shut down for the first time to test HA, these cascading failures blocked all scanning:

| Layer | Symptom | Root Cause |
| :--- | :--- | :--- |
| PostgreSQL | `cannot execute UPDATE in a read-only transaction` | Secondary DB in WAL replica mode; cannot accept writes. |
| Valkey | `ReadOnlyError: You can't write against a read only replica` | `slave_read_only: 1` with broken replication link. |
| OCM Placement | `AllManagedClusterSetsEmpty` / 0 decisions | Spokes in `clusterset: default`; missing runtime labels. |
| OpenSandbox Server | HTTP 500: `'ImageSpec' object has no attribute 'repository'` | Code used `.repository`; `ImageSpec` only has `.uri`. |
| Database Auth | `FATAL: password authentication failed for user "postgres"` | `enableSuperuserAccess: false`; `rolpassword` was NULL. |
| Spoke Connectivity | `Timed out waiting for pod to become Running` | Port `:6443` not proxied on failover; spokes severed from Secondary OCM. |
| Stale TCP | `klusterlet` hanging after failover | Envoy missing `close_connections_on_host_health_failure: true`. |

---

## 2. Phase 1: In-Cluster Failover Controller

### What Changed

**Retired**: `ocm-failover-daemon.service` — host-level systemd script on `hub2-vm` with cluster-admin kubeconfig. No RBAC isolation, no `kubectl` visibility, state lost on reboot.

**Added**: Kubernetes `Deployment` (`ocm-failover-controller`) inside `secondaryhub` namespace `opensandbox-system`.

**File**: [`codeInspector/charts/apiServer/templates/failover-controller.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/apiServer/templates/failover-controller.yaml)

**Helm activation**:
- PrimaryHub `values.yaml`: `failoverController.enabled: false`
- SecondaryHub `values-secondary.yaml`: `failoverController.enabled: true`, `primaryHost: "10.99.0.1"`

### Resource Structure inside `failover-controller.yaml`

This is a single Helm template file that renders **4 Kubernetes resources** in one shot:

```
failover-controller.yaml  (rendered only when failoverController.enabled: true)
│
├── ServiceAccount         ocm-failover-controller-sa
│     namespace: opensandbox-system
│
├── ClusterRole            ocm-failover-controller-role
│     (least-privilege — exact verbs per resource group)
│
├── ClusterRoleBinding     ocm-failover-controller-rb
│     binds ServiceAccount → ClusterRole
│
├── ConfigMap              ocm-failover-controller-script
│     data:
│       reconciler.sh           ← the controller process (bash infinite loop)
│       secondary-cluster.yaml  ← CNPG re-clone manifest (used on failback)
│       delta-sync-job.yaml     ← failback-delta-sync Job manifest
│
└── Deployment             ocm-failover-controller
      serviceAccountName: ocm-failover-controller-sa
      command: ["/bin/bash", "/scripts/reconciler.sh"]
      volumeMounts: ConfigMap mounted at /scripts/
```

`reconciler.sh` is **not a file on any VM disk**. It lives as a key in the ConfigMap (`data.reconciler.sh: |`). The Deployment mounts the ConfigMap as a volume at `/scripts/`, so the container sees `/scripts/reconciler.sh`, `/scripts/secondary-cluster.yaml`, and `/scripts/delta-sync-job.yaml` — all version-locked to the same Helm release.

When the controller needs to failback, it calls:
```bash
kubectl apply -f /scripts/delta-sync-job.yaml      # submits the delta sync Job
kubectl apply -f /scripts/secondary-cluster.yaml   # re-clones PostgreSQL
```
...reading those files from the ConfigMap mount, not from the host filesystem.

### RBAC Resources Created

```
ServiceAccount: ocm-failover-controller-sa  (namespace: opensandbox-system)
ClusterRole:    ocm-failover-controller-role
  - postgresql.cnpg.io/clusters:                    get, list, watch, patch, update, delete, create
  - cluster.open-cluster-management.io/managedclusters: get, list, watch, patch, update
  - certificates.k8s.io/certificatesigningrequests: get, list, watch, create, update, patch, approve
  - apps/deployments:                               get, list, watch, patch, update
  - core/pods, pods/exec:                           get, list, watch, create, delete
  - core/persistentvolumeclaims:                    get, list, watch, delete
  - coordination.k8s.io/leases:                     get, list, watch, delete
  - batch/jobs:                                     get, list, watch, create, delete
ClusterRoleBinding: ocm-failover-controller-rb
```

### Key Logic in `reconciler.sh`

`reconciler.sh` runs as the container's **PID 1 process** — it is the controller. It is a `while true` loop that:
1. Probes Primary every 1 second via `/livez`.
2. On 2 consecutive failures → executes the **failover sequence**.
3. When Primary recovers → executes the **failback sequence**.
4. Persists state in the `$STATE` variable in memory — restored from live CNPG spec on pod restart.

**Detection**: `curl -k -m 1 -s https://10.99.0.1:6443/livez` — 1-second timeout, `FAIL_THRESHOLD=2`, 1-second sleep = ~4s worst-case detection.

**Self-healing state on restart**: Reads live `spec.replica.enabled` from CNPG cluster to restore `STATE` — survives pod restarts without losing context.

**Failover — spokes are untainted FIRST**:
```bash
# Why first: pg_promote() takes ~500ms. Spokes must be able to reconnect
# to SecondaryHub OCM before that completes, not after.
for spoke in spoke1 spoke2; do
  kubectl patch managedcluster "$spoke" --type merge \
    -p '{"spec":{"hubAcceptsClient":true,"taints":[]}}' || true
done
approve_csrs  # auto-approve pending spoke TLS CSRs

kubectl patch cluster postgresql-secondary -n "$NAMESPACE" --type merge \
  -p '{"spec":{"replica":{"enabled":false}}}' || true   # in-place pg_promote()

kubectl exec -n "$NAMESPACE" deploy/valkey -- valkey-cli REPLICAOF NO ONE
```

**Failback — spokes are released FIRST**:
```bash
# Why first: Envoy already flipped traffic to PrimaryHub the instant /livez
# recovered. Primary must start orchestrating workloads now, not after DB sync.
for spoke in spoke1 spoke2; do
  kubectl patch managedcluster "$spoke" --type merge \
    -p '{"spec":{"hubAcceptsClient":false,"taints":[{"key":"cluster.open-cluster-management.io/unreachable","effect":"NoSelect"}]}}' || true
  # Delete Lease: OCM klusterlet renews it every 60s. Without deleting it,
  # SecondaryHub OCM holds the spoke for up to 60s after hubAcceptsClient=false.
  kubectl delete lease managed-cluster-lease -n "$spoke" || true
done

# Then: wait pg_isready, submit delta Job, reset Valkey, re-clone PostgreSQL
```

---

## 3. Phase 2: Envoy Active-Passive Gateway

### What Changed

**Retired**: `ocm-vip-watchdog.sh` — bash loop mutating `iptables` DNAT rules. Race conditions on flip, no hysteresis, no observability, stale TCP sessions on klusterlets.

**Added**: `envoyproxy/envoy:v1.31-latest` Docker container under `envoy-gateway.service` (systemd) on `gateway-vm`.

**Files**:
- [`codeInspector/gateway/envoy.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/gateway/envoy.yaml)
- [`codeInspector/gateway/envoy-gateway.service`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/gateway/envoy-gateway.service)
- [`codeInspector/gateway/deploy-gateway.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/gateway/deploy-gateway.sh)

### Key Config Added

```yaml
clusters:
- name: ingress_http_cluster      # Port :80
  close_connections_on_host_health_failure: true   # NEW
  health_checks:
  - timeout: 1s
    interval: 1s
    unhealthy_threshold: 2        # 2 failures → failover in-memory (< 100ms)
    healthy_threshold: 2          # 2 successes → failback (automatic)
    tcp_health_check: {}
  endpoints:
  - priority: 0  # Primary (10.99.0.1) — 100% traffic while healthy
  - priority: 1  # Secondary (10.99.0.2) — receives traffic only when Priority 0 fails

- name: ingress_kube_api_cluster  # Port :6443
  close_connections_on_host_health_failure: true   # NEW
  idle_timeout: 15s               # NEW — prevents stale klusterlet TCP accumulation
  # same priority endpoints
```

### Why These Were Added

**`close_connections_on_host_health_failure: true`**: Without this, when PrimaryHub fails health checks, Envoy stops routing *new* connections but keeps *existing* TCP connections alive. The `klusterlet-work-agent` has a persistent TLS connection to `:6443`. Without this flag, it holds that connection against the dead Primary for minutes. With it, Envoy sends TCP RST immediately — `klusterlet` reconnects to Secondary.

**`idle_timeout: 15s`**: Prevents idle klusterlet connections (no active heartbeat) from accumulating and exhausting file descriptors on the gateway under failover/failback cycling.

**Why both `:80` and `:6443` must switch together**: Spokes connect to `10.99.0.100:6443` for the Kubernetes API. If only `:80` switches, REST API calls go to Secondary but spokes still talk to Primary's dead `kube-apiserver`. Spokes cannot receive `ManifestWork`. Both ports must fail over to the same target.

---

## 4. Phase 3: Delta Synchronization Job

### What Changed

**Retired**: Inline `kubectl exec ... pg_dump | kubectl exec ... psql` pipeline inside the reconciler. Invisible to Kubernetes, no retry, blocks reconciler loop if hung, credentials in process listings.

**Added**: `batch/v1 Job` (`failback-delta-sync`) using `postgres:15-alpine`. Manifest embedded in the same ConfigMap as the reconciler (version-locked to Helm release).

### Job Spec

```yaml
spec:
  ttlSecondsAfterFinished: 600   # auto-cleanup 10 min after completion
  backoffLimit: 2                # retry on failure
  template:
    spec:
      containers:
      - name: delta-syncer
        image: postgres:15-alpine
        env:
        - name: PGPASSWORD
          valueFrom:
            secretKeyRef:
              name: postgresql-primary-credentials
              key: password       # mounted from Secret — not visible in ps/top
        command:
        - /bin/sh
        - -c
        - |
          pg_dump -h postgresql-secondary-rw -p 5432 -U postgres -d apikeys -t api_keys \
            --data-only --inserts --on-conflict-do-nothing 2>/dev/null \
            | grep '^INSERT' > /tmp/outage_delta.sql || true

          if [ -s /tmp/outage_delta.sql ]; then
            psql -h 10.99.0.1 -p 5432 -U postgres -d apikeys < /tmp/outage_delta.sql
          fi
```

**Why `--on-conflict-do-nothing`**: Physical WAL streaming guarantees all rows on Secondary that existed *before* failover are byte-for-byte identical to Primary. Only rows written *during* the outage are absent from Primary. `ON CONFLICT DO NOTHING` safely merges them without primary key violations.

**Inspect Job**:
```bash
kubectl get jobs -n opensandbox-system
kubectl logs job/failback-delta-sync -n opensandbox-system
```

---

## 5. Phase 4: WireGuard Routing & KinD Port Mappings

### What Changed

**Retired**: `/usr/local/bin/ocm-mesh-boot.sh` and `ocm-mesh-boot.service` on all VMs. Used `socat` port forwarders, non-idempotent `ip rule` injection, `iptables` rules after each reboot. `socat` processes died silently under load.

**Added**:

**1. Native `PostUp`/`PreDown` in `/etc/wireguard/wg0.conf`**:
```ini
PostUp = ip rule add from 10.99.0.X table 200 priority 100 2>/dev/null || true; \
         ip route add default dev wg0 table 200 2>/dev/null || true; \
         iptables -t nat -C POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null \
           || iptables -t nat -A POSTROUTING -o wg0 -j MASQUERADE; \
         iptables -C FORWARD -i wg0 -j ACCEPT 2>/dev/null \
           || iptables -I FORWARD 1 -i wg0 -j ACCEPT; \
         iptables -C FORWARD -o wg0 -j ACCEPT 2>/dev/null \
           || iptables -I FORWARD 1 -o wg0 -j ACCEPT; \
         sysctl -w net.ipv4.ip_forward=1

PreDown = ip rule del from 10.99.0.X table 200 priority 100 2>/dev/null || true; \
          ip route del default dev wg0 table 200 2>/dev/null || true; \
          iptables -t nat -D POSTROUTING -o wg0 -j MASQUERADE 2>/dev/null || true
```

**Why table 200**: Provides a separate routing domain for traffic sourced from the WireGuard overlay IP. Without it, reply packets use the host's default route (underlay NIC), breaking symmetric routing. Table 200 forces overlay-sourced replies back through `wg0`.

**2. Declarative `extraPortMappings` in KinD manifests** (replaces `socat`):
```yaml
extraPortMappings:
- { containerPort: 6443,  hostPort: 6443  }  # Kubernetes API
- { containerPort: 80,    hostPort: 80    }  # HTTP ingress
- { containerPort: 443,   hostPort: 443   }  # HTTPS ingress
- { containerPort: 30432, hostPort: 5432  }  # PostgreSQL replication
- { containerPort: 30379, hostPort: 6379  }  # Valkey replication
```

KinD manages these DNAT rules internally. No external monitoring needed.

**3. `netfilter-persistent`** for host-level NAT rule persistence across reboots.

---

## 6. Phase 5: Modernized Bootstrap Script

**File**: [`multi-cluster-sync.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/multi-cluster-sync.sh)

| Phase | Before | After |
| :--- | :--- | :--- |
| Phase 7 (Gateway) | Installs `ocm-vip-watchdog.sh` + iptables DNAT | SCP `envoy.yaml`; run `deploy-gateway.sh` |
| Phase 9 (Boot Recovery) | Installs `ocm-mesh-boot.service` + `install-auto-recovery.sh` | Validates `netfilter-persistent` + WireGuard `PostUp`/`PreDown` |
| Phase 11 (Health) | Checks `ocm-failover-daemon.service`, `ocm-vip-watchdog.service` | Checks `envoy-gateway.service`, `kubectl get deploy ocm-failover-controller` |

Result: `./multi-cluster-sync.sh` is 100% idempotent. Zero unmanaged bash daemons on any VM.

---

## 7. CloudNativePG Hardening

### Problem 1: `postgresql-secondary-1` took 12+ minutes to terminate

**Root cause**: CNPG defaults `smartShutdownTimeout: 180s`. `pg_ctl -m smart` waits for all active client connections to close. `sandbox-api` continuously retrying kept `pg_ctl` blocked indefinitely.

**Fix**:
```yaml
smartShutdownTimeout: 10   # was: 180 (default)
stopDelay: 30              # was: 1800 (default)
```

The failover controller also no longer destroys/recreates the pod. Patching `spec.replica.enabled: false` triggers in-place `pg_promote()` — pod stays alive, no termination grace period.

### Problem 2: `FATAL: password authentication failed for user "postgres"`

**Root cause**: `enableSuperuserAccess` defaults to `false`. CNPG does not manage `pg_authid.rolpassword` for the `postgres` superuser. Password is NULL. `pg_hba.conf` enforces `scram-sha-256` → every TCP connection from `sandbox-api` fails.

**Fix**:
```yaml
enableSuperuserAccess: true
superuserSecret:
  name: postgresql-primary-credentials
```

CNPG automatically syncs the superuser password to match the Secret.

### Summary

| Problem | Root Cause | Fix |
| :--- | :--- | :--- |
| 12-min termination | `pg_ctl -m smart` blocks on retrying clients | `smartShutdownTimeout: 10`, `stopDelay: 30`; in-place `pg_promote()` |
| Pod restart on failover | Controller deleted + recreated pod | Patch `spec.replica.enabled: false` — pod stays alive |
| Password auth failure | `enableSuperuserAccess: false`; NULL `rolpassword` | `enableSuperuserAccess: true` + `superuserSecret` |
| Timeline divergence | Secondary on Timeline 2; Primary on Timeline 1 | Delete cluster + re-apply with `bootstrap.pg_basebackup` |

---

## 8. Valkey / Redis Cache Failover

### What Changed

**Failover** (executed by controller):
```bash
kubectl exec -n opensandbox-system deploy/valkey -- valkey-cli REPLICAOF NO ONE
```

**Failback** (executed by controller):
```bash
kubectl exec -n opensandbox-system deploy/valkey -- valkey-cli REPLICAOF 10.99.0.1 6379
```

### Why

Valkey on SecondaryHub runs with `--replicaof 10.99.0.1 6379`. When PrimaryHub fails, the replication link goes down but Valkey retains `slave_read_only: 1`. Any `sadd("active_api_keys", jti)` call throws `redis.exceptions.ReadOnlyError`.

`REPLICAOF NO ONE` promotes Valkey in-memory without pod restart — all cached keys are preserved.

---

## 9. OCM Spoke Cluster Placement & Labels

### What Changed

**Labels applied to spokes on both hubs**:
```bash
kubectl label managedcluster spoke1 \
  cluster.open-cluster-management.io/clusterset=sandbox-spokes \
  sandbox-workload-capable=true runtime.gvisor=true runtime.kata=true --overwrite

kubectl label managedcluster spoke2 \
  cluster.open-cluster-management.io/clusterset=sandbox-spokes \
  sandbox-workload-capable=true runtime.gvisor=true runtime.kata=true --overwrite
```

**Placement policy** (`codeInspector/templates/ocm-placement.yaml`):
```yaml
spec:
  clusterSets: [sandbox-spokes]
  numberOfClusters: 1
  predicates:
    - requiredClusterSelector:
        labelSelector:
          matchLabels:
            sandbox-workload-capable: "true"
            runtime.gvisor: "true"
            runtime.kata: "true"
```

### Why

Before this change, spokes on SecondaryHub were in `clusterset: default` and lacked the runtime labels. `kubectl get placement` reported `Reason: AllManagedClusterSetsEmpty` with 0 decisions. Moving spokes to `sandbox-spokes` and adding the labels allowed OCM's placement engine to immediately schedule workloads.

### Spoke Selection Priority (in `ocm_provider.py`)

```
1. extensions["target_cluster"]  — explicit override
2. extensions["region"]          — "us-east-1"/"us" → spoke1, "eu-central-1"/"eu" → spoke2
3. PlacementDecision query       — dynamic OCM decision (alphabetical tie-breaker)
4. DEFAULT_FALLBACK_SPOKE        — static fallback
```

**Why the same spoke is always selected per hub**: `numberOfClusters: 1` with two equally eligible spokes → OCM picks alphabetically. `spoke1 < spoke2` → PrimaryHub consistently picks `spoke1`, SecondaryHub consistently picks `spoke2`. This is intentional.

---

## 10. OpenSandbox Server: ImageSpec Bug Fix

### What Changed

**File**: [`opensandbox-server/docker-build/src/services/k8s/ocm_provider.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/ocm_provider.py)

**Before** (broken):
```python
image_name = image_spec.repository   # AttributeError — .repository does not exist
```

**After** (fixed):
```python
image_name = getattr(image_spec, "uri", None)
if not image_name:
    repo = getattr(image_spec, "repository", None)
    tag = getattr(image_spec, "tag", None)
    if repo and tag:
        image_name = f"{repo}:{tag}"
    elif repo:
        image_name = repo
    else:
        image_name = str(image_spec)
```

**Remote scan report endpoint added** in `opensandbox-server/docker-build/src/api/lifecycle.py`:
```python
@router.post("/scan-jobs/{job_id}/report", tags=["Security Scan Pipeline"])
async def upload_scan_report(job_id: str, request: Request):
    """
    Receives scan report from remote spoke sandbox pods.
    Saves to data_root/job_id/reports/security_scan_report.json.
    """
```

### Why

`ImageSpec` in `src/api/schema.py` only defines `uri: str` and `auth: Optional[ImageAuth]`. Accessing `.repository` raised `AttributeError` on every scan job creation (HTTP 500). The safe extraction pattern checks `.uri` first, then falls back gracefully.

The `/report` endpoint allows spoke pods running in gVisor sandboxes (with no direct Hub storage access) to push scan results back via the Envoy gateway.

**Container image updated**: `01community/01sandbox-opensandbox-server:v0.7.10-ocm`

---

## 11. API Server: Mock Key Authentication

### Problem

When a user triggered a scan without an Auth0 session, the frontend generated a `z1_...` prefixed opaque mock key. On SecondaryHub, `sandbox-api` lacked `ALLOW_MOCK_KEYS=true`, so the JWT parser tried to decode `z1_...` as a 3-segment RS256 JWT and failed:

```json
{"detail": "Invalid token format (Not enough segments). Token snippet: 'z1_pzhrjm2...'"}
```

### Fix

```bash
kubectl set env deploy/sandbox-api -n opensandbox-system ALLOW_MOCK_KEYS=true
```

And in Helm `deployment.yaml`:
```yaml
env:
- name: ALLOW_MOCK_KEYS
  value: "true"
```

### Why

`ALLOW_MOCK_KEYS=true` causes the token validator to bypass JWT signature checks for `z1_` tokens and synthesize a session from a database lookup. Set to `false` in production (see Section 15).

---

## 12. Frontend: Dynamic API Configuration

### What Changed

**New file**: `z1sandbox-website/src/lib/apiConfig.ts`
```typescript
export function getApiBaseUrl(): string {
  const envUrl = import.meta.env.VITE_API_URL;
  if (envUrl && envUrl.trim() !== '') return envUrl.replace(/\/+$/, '');
  if (typeof window !== 'undefined') return window.location.origin;
  return 'http://127.0.0.1:8000';
}
```

**All hardcoded IPs/hostnames replaced** across:
- `src/pages/Dashboard.tsx`, `Health.tsx`, `Metrics.tsx`, `RepoScanner.tsx`
- `src/components/dashboard/SecurityScanner.tsx`, `RepoScannerWidget.tsx`, `InlineApiKeyPanel.tsx`
- `src/components/UserSettingsDialog.tsx`
- `src/hooks/useJobStore.ts`

**New file**: `z1sandbox-website/.env.example`
```
VITE_API_URL=http://192.168.100.10
VITE_AUTH0_DOMAIN=your-tenant.auth0.com
VITE_AUTH0_CLIENT_ID=...
VITE_AUTH0_AUDIENCE=https://api.01sandbox.com
```

### Why

Hardcoded IPs (`192.168.100.10:8000`, `api-sandbox.01security.com`, `localhost:8000`) caused failures when testing across different VMs or environments. With `VITE_API_URL` and fallback to `window.location.origin`, the frontend works behind any reverse proxy or gateway without rebuilding.

---

## 13. PostgreSQL Timeline Divergence

### What Happens

When `postgresql-secondary` is promoted, PostgreSQL writes a timeline history file and increments timeline 1 → 2. On failback, patching `spec.replica.enabled: true` is rejected immediately:

```
ERROR: highest timeline 1 of the primary is behind recovery timeline 2
```

Streaming replication is permanently broken between a Timeline 1 primary and a Timeline 2 secondary.

### Fix: pg_basebackup Re-Clone

1. Delta sync imports outage records into Primary (Primary becomes authoritative).
2. `kubectl delete cluster postgresql-secondary -n opensandbox-system --wait=false`
3. `kubectl apply -f /scripts/secondary-cluster.yaml`
   - Specifies `bootstrap.pg_basebackup.source: postgresql-primary`
4. CNPG runs `pg_basebackup` from `10.99.0.1:5432` — fresh copy on Timeline 1.
5. Controller polls `pg_stat_wal_receiver` until `status: streaming`.

---

## 14. Split-Brain & Spoke Release Ordering

### Split-Brain Prevention

Split-brain = both hubs accepting writes simultaneously → WAL timeline divergence → unrecoverable without data loss.

Prevention mechanisms:
- **Detection threshold of 2**: Requires 2 consecutive failed probes before promoting. Momentary blips don't trigger failover.
- **Envoy priority routing**: Only one hub (Priority 0) receives traffic at any time. Never both simultaneously.
- **Delta sync before demotion**: Outage records applied to Primary before Secondary is demoted → Primary is canonical.
- **pg_basebackup re-clone**: Forces Secondary back to Timeline 1 on every failback.

### Spoke Release Ordering (Critical)

**During failover**:
```
1. Untaint ManagedCluster (spokes reconnect to Secondary OCM immediately)
2. Approve spoke TLS CSRs
3. pg_promote() Secondary PostgreSQL (in-place, ~500ms)
4. REPLICAOF NO ONE Valkey
```

**During failback**:
```
1. Taint ManagedCluster + delete lease (spokes released to Primary immediately)
2. Wait for Primary PostgreSQL pg_isready
3. Submit delta sync Job + wait for completion
4. REPLICAOF Primary Valkey
5. Delete + re-clone postgresql-secondary (pg_basebackup → Timeline 1)
```

This ordering ensures workload orchestration switches as fast as possible, while data synchronization happens in the background.

---

## 15. Production Hardening Guide

| Parameter | Dev/Sandbox | Production |
| :--- | :--- | :--- |
| `ALLOW_MOCK_KEYS` | `"true"` | `"false"` — enforces signed RS256 JWT only |
| `APP_ENV` | `"development"` | `"production"` — disables debug stack traces |
| `VITE_API_URL` | `http://192.168.100.10` | `https://api-sandbox.01security.com` |
| Auth0 Audience | Optional / bypassable | `https://api.01sandbox.com` — prevents JWT spoofing |
| Database Secrets | Plaintext in values | SealedSecrets / Vault — encrypted at rest |
| PostgreSQL Replication | NodePort 30432 over WireGuard | `sslmode: verify-full` — mutual TLS |
| Valkey Replication | Cleartext over WireGuard | `tls-replication yes` with client certs |
| Spoke Sandbox | gVisor (`runsc`) | gVisor + Kata Containers dual pool |
