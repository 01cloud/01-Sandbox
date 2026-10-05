# 🚀 Multi-Cluster Platform – Provisioning Guide

This directory contains the fully automated provisioning script for the **01-Sandbox multi-cluster platform** built on KinD (Kubernetes-in-Docker).

The script provisions the following topology:

```
┌────────────────────────────────────────────────────────────────────────┐
│  Envoy Gateway (L7) — Host-port :80  ←  Frontend VITE_API_BASE_URL   │
├────────────┬───────────────────────────────────────────────────────────┤
│ PrimaryHub │  Read-Write PostgreSQL + Valkey + sandbox-api             │
│            │  OCM Hub (Active) — manages Spoke1 & Spoke2              │
├────────────┼───────────────────────────────────────────────────────────┤
│SecondaryHub│  Hot-Standby PostgreSQL (WAL Streaming) + Valkey replica  │
│            │  OCM Hub (Standby) — failover-controller always running   │
├────────────┼───────────────────────────────────────────────────────────┤
│   Spoke1   │  Workload cluster — kata-fc (Firecracker) + gVisor runtm │
│   Spoke2   │  Workload cluster — kata-fc (Firecracker) + gVisor runtm │
└────────────┴───────────────────────────────────────────────────────────┘
```

All clusters are connected through a **WireGuard encrypted mesh** over a shared Docker transit network.

---

## Prerequisites

### 1 – SSH Key for GitHub

The script clones the **private `01cloud/01-Sandbox` repository** via SSH during provisioning. You must add your machine's SSH public key to GitHub before running the script.

**Generate an SSH key (skip if you already have one):**
```bash
ssh-keygen -t ed25519 -C "your-email@example.com"
# Accept defaults — key saved at ~/.ssh/id_ed25519
```

**Copy your public key:**
```bash
cat ~/.ssh/id_ed25519.pub
```

**Add it to GitHub:**
1. Go to **GitHub → Settings → SSH and GPG keys → New SSH key**
2. Paste the output from the command above.
3. Save.

**Verify the connection:**
```bash
ssh -T git@github.com
# Expected: "Hi <username>! You've successfully authenticated..."
```

### 2 – System Requirements

| Requirement | Minimum |
|---|---|
| OS | Ubuntu 22.04 / Debian 12 (x86-64) |
| CPU | 8 cores (16 recommended) |
| RAM | 16 GB (32 GB recommended) |
| Disk | 50 GB free |
| KVM | Enabled (`ls /dev/kvm` must succeed for Kata/Firecracker) |

> **Note:** The script automatically installs all required tools: `docker`, `kind`, `kubectl`, `helm`, `clusteradm`, `wireguard-tools`, `jq`, and more.

---

## Provisioning

### One-Shot Setup

Run from **any directory** on the target machine. The script auto-detects or clones the repository.

```bash
# Clone the provisioning script (or copy the docker-multi-script/ folder)
git clone git@github.com:01cloud/01-Sandbox.git
cd 01-Sandbox/docker-multi-script

# Make executable and run
chmod +x docker-multi-cluster.sh
./docker-multi-cluster.sh
```

The script is **fully idempotent** — re-running it skips phases that are already configured.

### Available Flags

```bash
./docker-multi-cluster.sh              # Full automated setup (default)
./docker-multi-cluster.sh --force      # Re-run ALL phases unconditionally
./docker-multi-cluster.sh --verify     # Health check an existing deployment
./docker-multi-cluster.sh --clean      # Tear down everything (clusters, nets, state)
```

### What the Script Does

| Phase | Description |
|---|---|
| 01 | Preflight: install tools, clone repo, tune inotify limits |
| 02 | Create transit Docker network + generate WireGuard keypairs |
| 03 | Create PrimaryHub & SecondaryHub KinD clusters |
| 04 | Install all CRDs on hubs (CNPG, Gateway API, MetalLB, etc.) |
| 05 | Configure WireGuard (`wg0`) on hub clusters |
| 06 | Deploy Envoy Gateway (L7 HTTP routing with failover logic) |
| 07 | Verify root CA trust and WireGuard VIP connectivity |
| 08 | Initialize OCM (Open Cluster Management) on both hubs |
| 09 | Create `opensandbox-system` namespace on all clusters |
| 10 | Build & load `opensandbox-server` Docker image |
| 11 | Helm deploy full stack to PrimaryHub |
| 12 | Helm deploy standby stack to SecondaryHub |
| 13 | Create Spoke1 & Spoke2 KinD clusters |
| 14 | Configure WireGuard on all 4 clusters (full mesh) |
| 15 | Install CRDs on spoke clusters |
| 15b | Install Kata Containers + Firecracker (`kata-fc`) on spokes |
| 15c | Install Google gVisor (`runsc`) runtime on spokes |
| 16 | Join spoke clusters to PrimaryHub OCM |
| 17 | Sync spoke clusters to SecondaryHub OCM (for failover) |

### After Completion

The script prints a summary with the **detected machine IP** for configuring your frontend:

```
  ┌──────────────────────────────────────────────────────────────────┐
  │              🚀 FRONTEND & API INTEGRATION DETAILS               │
  ├──────────────────────────────────────────────────────────────────┤
  │ Detected Machine IP : 192.168.1.100
  │ Set this in your frontend z1sandbox-website/.env:
  │   VITE_API_BASE_URL=http://192.168.1.100
  │ Health Check URL    : http://192.168.1.100/health
  │ Swagger API Docs    : http://192.168.1.100/docs
  └──────────────────────────────────────────────────────────────────┘
```

> The script **automatically updates** `z1sandbox-website/.env` with the correct IP if the file exists.

---

## Verification Guide

Run all commands from the machine where the clusters were provisioned.

### Quick Built-in Health Check

```bash
./docker-multi-cluster.sh --verify
```

---

### 1 – Multi-Cluster Connectivity

**List all KinD clusters:**
```bash
kind get clusters
# Expected: primaryhub  secondaryhub  spoke1  spoke2
```

**Check all kubectl contexts are available:**
```bash
kubectl config get-contexts
# Expected: kind-primaryhub, kind-secondaryhub, kind-spoke1, kind-spoke2
```

**Verify nodes are Ready on all clusters:**
```bash
for ctx in kind-primaryhub kind-secondaryhub kind-spoke1 kind-spoke2; do
  echo "=== $ctx ==="
  kubectl --context $ctx get nodes
done
```

**Ping across WireGuard mesh from Primaryhub (PrimaryHub → SecondaryHub):**
```bash
ping 10.99.0.2
```

**Ping from PrimaryHub → Spoke1:**
```bash
ping 10.99.0.3
```

**Verify WireGuard interface is UP on all nodes:**
```bash
for c in primaryhub-control-plane secondaryhub-control-plane spoke1-control-plane spoke2-control-plane; do
  echo "=== $c ==="
  docker exec $c ip addr show wg0 2>/dev/null | grep -E "inet|state"
done
```

**Verify Envoy Gateway is routing traffic:**
```bash
HOST_IP=$(ip route get 1.1.1.1 | awk '{print $7; exit}')
curl -s http://${HOST_IP}/health | python3 -m json.tool
```

---

### 2 – Database Replication

**Check PostgreSQL replication sender on PrimaryHub:**
```bash
kubectl --context kind-primaryhub exec -n opensandbox-system postgresql-primary-1 \
  -c postgres -- psql -U postgres -d apikeys \
  -c "SELECT client_addr, application_name, state, sync_state, sent_lsn, write_lsn, flush_lsn, replay_lsn FROM pg_stat_replication;"
```
> Expected: One row with `state = streaming` and `sync_state = async`.

**Check WAL receiver on SecondaryHub:**
```bash
kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 \
  -c postgres -- psql -U postgres -d apikeys \
  -c "SELECT status, sender_host, sender_port, latest_end_lsn, last_msg_receipt_time FROM pg_stat_wal_receiver;"
```
> Expected: `status = streaming`, `sender_host` pointing to `10.99.0.1` (PrimaryHub WireGuard IP).

**Check replication lag:**
```bash
kubectl --context kind-primaryhub exec -n opensandbox-system postgresql-primary-1 \
  -c postgres -- psql -U postgres \
  -c "SELECT pg_current_wal_lsn(), pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)) AS lag FROM pg_stat_replication;"
```
> Expected: Lag close to `0 bytes`.

**Verify SecondaryHub DB is a standby (read-only):**
```bash
kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 \
  -c postgres -- psql -U postgres -c "SELECT pg_is_in_recovery();"
# Expected: t (true)
```

**Check Valkey replication on SecondaryHub:**
```bash
kubectl --context kind-secondaryhub exec -n opensandbox-system deploy/valkey -- \
  valkey-cli info replication | grep -E "role|master_host|master_port|master_link_status|master_last_io"
```
> Expected: `role:slave`, `master_link_status:up`.

---

### 3 – When PrimaryHub is Down – What to Check on SecondaryHub

**Step 1 – Simulate PrimaryHub failure:**
```bash
docker stop primaryhub-control-plane
```

**Step 2 – Monitor the failover controller:**
```bash
kubectl --context kind-secondaryhub logs -n opensandbox-system \
  -l app=ocm-failover-controller --follow
```
> Watch for: `PrimaryHub unreachable`, `Initiating failover`, `SecondaryHub promoted to active`.

**Step 3 – Verify PostgreSQL promotion on SecondaryHub:**
```bash
kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 \
  -c postgres -- psql -U postgres -c "SELECT pg_is_in_recovery();"
# After promotion: f (false) — now acting as primary
```

**Step 4 – Check Valkey role flipped to master:**
```bash
kubectl --context kind-secondaryhub exec -n opensandbox-system deploy/valkey -- \
  valkey-cli info replication | grep role
# Expected: role:master
```

**Step 5 – Verify API keys can be created through SecondaryHub:**
```bash
HOST_IP=$(ip route get 1.1.1.1 | awk '{print $7; exit}')
curl -s -X POST http://${HOST_IP}/api/v1/apikeys \
  -H "Content-Type: application/json" \
  -d '{"name": "failover-test-key"}' | python3 -m json.tool
```

**Step 6 – Verify spoke clusters re-registered on SecondaryHub:**
```bash
kubectl --context kind-secondaryhub get managedclusters
# Expected: spoke1 and spoke2 are Available
```

**Step 7 – Bring PrimaryHub back up (failback):**
```bash
docker start primaryhub-control-plane
# The failover controller detects PrimaryHub recovery,
# runs delta sync, and restarts sandbox-api on SecondaryHub to sever stale connections.
```

---

### 4 – Klusterlet Registration in Spoke Clusters

**Check klusterlet agent pods are Running:**
```bash
for ctx in kind-spoke1 kind-spoke2; do
  echo "=== $ctx ==="
  kubectl --context $ctx -n open-cluster-management-agent get pods
done
```
> Expected: `klusterlet`, `klusterlet-registration-agent`, `klusterlet-work-agent` all `Running`.

**Check spoke clusters appear as Available on PrimaryHub:**
```bash
kubectl --context kind-primaryhub get managedclusters
```

**Check spoke cluster conditions in detail:**
```bash
kubectl --context kind-primaryhub get managedcluster spoke1 -o yaml | grep -A 10 "conditions:"
kubectl --context kind-primaryhub get managedcluster spoke2 -o yaml | grep -A 10 "conditions:"
```

**Check ManifestWork applied to a spoke:**
```bash
kubectl --context kind-primaryhub get manifestwork -n spoke1
kubectl --context kind-primaryhub get manifestwork -n spoke2
```

**Verify klusterlet also registered on SecondaryHub (for failover):**
```bash
kubectl --context kind-secondaryhub get managedclusters
```

---

### 5 – Failover Controller

**Check the failover controller pod is running:**
```bash
kubectl --context kind-secondaryhub get pods -n opensandbox-system \
  -l app=ocm-failover-controller
```

**Tail the failover controller logs:**
```bash
kubectl --context kind-secondaryhub logs -n opensandbox-system \
  -l app=ocm-failover-controller --tail=50
```
> In steady state: periodic `PrimaryHub reachable` health checks.

**Describe the controller pod:**
```bash
POD=$(kubectl --context kind-secondaryhub get pod -n opensandbox-system \
  -l app=ocm-failover-controller -o jsonpath='{.items[0].metadata.name}')
kubectl --context kind-secondaryhub describe pod -n opensandbox-system $POD
```

---

### 6 – Spoke Cluster Runtimes

**Verify both RuntimeClasses exist on spokes:**
```bash
for ctx in kind-spoke1 kind-spoke2; do
  echo "=== $ctx ==="
  kubectl --context $ctx get runtimeclass
done
# Expected: kata-fc and gvisor both listed
```

**Verify Kata Containers binaries are present:**
```bash
docker exec spoke1-control-plane ls /usr/local/bin/ | grep -E "kata|firecracker"
```

**Verify gVisor (runsc) is installed and functional:**
```bash
docker exec spoke1-control-plane runsc --version
docker exec spoke2-control-plane runsc --version
```

**Verify containerd is configured with both runtimes:**
```bash
docker exec spoke1-control-plane cat /etc/containerd/config.toml | grep -A 3 "kata\|runsc"
```

**Run a test pod with gVisor:**
```bash
kubectl --context kind-spoke1 apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: gvisor-test
  namespace: default
spec:
  runtimeClassName: gvisor
  containers:
  - name: hello
    image: busybox:latest
    command: ["sh", "-c", "uname -r && echo gVisor OK"]
  restartPolicy: Never
EOF
kubectl --context kind-spoke1 wait --for=condition=Succeeded pod/gvisor-test --timeout=60s
kubectl --context kind-spoke1 logs pod/gvisor-test
kubectl --context kind-spoke1 delete pod gvisor-test
```

**Run a test pod with kata-fc:**
```bash
kubectl --context kind-spoke1 apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: kata-test
  namespace: default
spec:
  runtimeClassName: kata-fc
  containers:
  - name: hello
    image: busybox:latest
    command: ["sh", "-c", "uname -r && echo Kata-FC OK"]
  restartPolicy: Never
EOF
kubectl --context kind-spoke1 wait --for=condition=Succeeded pod/kata-test --timeout=90s
kubectl --context kind-spoke1 logs pod/kata-test
kubectl --context kind-spoke1 delete pod kata-test
```

---

### 7 – Full Stack Status Snapshot

Run this single block to get a complete picture of all components:

```bash
echo "=== KinD Clusters ===" && kind get clusters

echo -e "\n=== OCM ManagedClusters (PrimaryHub) ==="
kubectl --context kind-primaryhub get managedclusters

echo -e "\n=== OCM ManagedClusters (SecondaryHub) ==="
kubectl --context kind-secondaryhub get managedclusters

echo -e "\n=== All Pods (PrimaryHub) ==="
kubectl --context kind-primaryhub get pods -n opensandbox-system

echo -e "\n=== All Pods (SecondaryHub) ==="
kubectl --context kind-secondaryhub get pods -n opensandbox-system

echo -e "\n=== RuntimeClasses (Spoke1) ==="
kubectl --context kind-spoke1 get runtimeclass

echo -e "\n=== PostgreSQL Replication ==="
kubectl --context kind-primaryhub exec -n opensandbox-system postgresql-primary-1 \
  -c postgres -- psql -U postgres \
  -c "SELECT client_addr, state, sync_state FROM pg_stat_replication;" 2>/dev/null

echo -e "\n=== Valkey Replication (SecondaryHub) ==="
kubectl --context kind-secondaryhub exec -n opensandbox-system deploy/valkey -- \
  valkey-cli info replication 2>/dev/null | grep -E "role|master_link_status"

HOST_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')
echo -e "\n=== API Health Check ==="
curl -sf "http://${HOST_IP}/health" | python3 -m json.tool 2>/dev/null || echo "Gateway not reachable"
```

---

## 8 – Frontend Dashboard, Auth0 Login & API Key Replication

The platform includes a modern Vite + React frontend dashboard located in `z1sandbox-website/` that integrates with Auth0 for user authentication and communicates with the multi-cluster backend via Envoy Gateway (port 80).

### 1. Configure and Launch the Frontend

1. Navigate to the frontend directory:
   ```bash
   cd ../z1sandbox-website # or /path/to/01-Sandbox/z1sandbox-website
   ```

2. Configure environment variables (`.env`):
   ```bash
   cp .env.example .env
   ```
   Ensure `.env` contains your Auth0 client configuration and points to the Envoy Gateway host IP:
   ```env
   VITE_AUTH0_DOMAIN=
   VITE_AUTH0_CLIENT_ID=
   VITE_AUTH0_AUDIENCE=https://code-inspector-api
   VITE_API_BASE_URL=http://<HOST_IP_OR_VM_IP>
   VITE_DASHBOARD_BACKENDS_JSON='[{"id":"Z1_SANDBOX","name":"01 Sandbox","description":"Production-grade hardened cluster for secure code execution.","icon":"terminal","color":"indigo","baseUrl":"/api/v1/01sbx","documentationUrl":"/api/v1/01sbx/docs"}]'
   VITE_ENABLE_DEV_MODE=false
   ```
   > **Note**: Replace `<HOST_IP_OR_VM_IP>` with your host machine or VM IP (e.g. `http://10.0.0.132` or `http://localhost`). Envoy forwards HTTP traffic on port 80 to the active hub's `sandbox-api`.

3. Install dependencies and start the Vite dev server:
   ```bash
   npm install
   npm run dev
   ```
   The frontend will be available at `http://localhost:8080`.

---

### 2. Login via Auth0

1. Open your browser and navigate to `http://localhost:8080`.
2. Click **Log In** / **Sign In**.
3. You will be redirected to the secure Auth0 Universal Login page.
4. Enter your credentials or complete sign-up.
5. Upon successful authentication, Auth0 issues a signed JWT token and redirects back to the dashboard.

---

### 3. Create API Keys in the Dashboard

1. In the dashboard sidebar, navigate to **API Keys** (or **Settings → API Keys**).
2. Click **Create New Key** / **Generate Key**.
3. Provide a descriptive key name (e.g., `production-agent-key`) and select required permissions.
4. Click **Generate** and securely copy the generated secret key token.

*(Alternative)* **Create an API key via cURL directly through Envoy Gateway:**
```bash
HOST_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}')

curl -X POST "http://${HOST_IP}/api/v1/api-keys" \
  -H "Authorization: Bearer <YOUR_AUTH0_ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "test-key-01"}'
```

---

### 4. Verify Real-Time Data Replication on SecondaryHub

Every key created on PrimaryHub is instantly replicated to SecondaryHub via continuous physical PostgreSQL WAL streaming and Valkey memory synchronization.

**1. Inspect the key in PrimaryHub PostgreSQL:**
```bash
kubectl --context kind-primaryhub exec -n opensandbox-system postgresql-primary-1 \
  -c postgres -- psql -U postgres -d apikeys \
  -c "SELECT id, name, user_email, created_at FROM api_keys;"
```

**2. Verify the key exists in SecondaryHub PostgreSQL (Replication in action):**
```bash
kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 \
  -c postgres -- psql -U postgres -d apikeys \
  -c "SELECT id, name, user_email, created_at FROM api_keys;"
```
> The exact same key record will be present on SecondaryHub with zero lag.

**3. Verify Valkey cache replication on both hubs:**
```bash
echo "=== PrimaryHub Valkey ==="
kubectl --context kind-primaryhub exec -n opensandbox-system deploy/valkey -- valkey-cli KEYS '*'

echo "=== SecondaryHub Valkey (Replica) ==="
kubectl --context kind-secondaryhub exec -n opensandbox-system deploy/valkey -- valkey-cli KEYS '*'
```

**4. Check real-time WAL replication health:**
```bash
# Check primary WAL sender:
kubectl --context kind-primaryhub exec -n opensandbox-system postgresql-primary-1 \
  -c postgres -- psql -U postgres \
  -c "SELECT client_addr, state, sync_state FROM pg_stat_replication;"

# Check secondary WAL receiver:
kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 \
  -c postgres -- psql -U postgres -d apikeys \
  -c "SELECT status, sender_host, written_lsn FROM pg_stat_wal_receiver;"
```

---

## 9 – High-Availability Failover & Split-Brain Prevention Testing

The platform employs a single-master Active-Passive model with an automated in-cluster Watchdog (`ocm-failover-controller`) on `secondaryhub`.

- **Normal State**: `primaryhub` actively accepts spokes (`spoke1`, `spoke2`) and serves API requests. `secondaryhub` keeps spokes in standby mode (`HUB ACCEPTED: false`, `AVAILABLE: Unknown`) to guarantee zero split-brain.
- **Failover State**: When `primaryhub` fails, `secondaryhub` promotes its database to Read-Write, un-taints spokes, accepts spokes (`HUB ACCEPTED: true`, `AVAILABLE: True`), and Envoy Gateway redirects all traffic to `secondaryhub`.
- **Failback State**: When `primaryhub` recovers, `ocm-failover-controller` syncs any delta mutations made during the outage back to PrimaryHub, re-clones Secondary as a replica, releases the spokes on SecondaryHub, and PrimaryHub resumes control.

---

### Step 1: Verify Initial Cluster State

Confirm PrimaryHub is the active manager and SecondaryHub is in standby:

```bash
echo "=== PrimaryHub ManagedClusters ==="
kubectl --context kind-primaryhub get managedclusters

echo -e "\n=== SecondaryHub ManagedClusters (Standby) ==="
kubectl --context kind-secondaryhub get managedclusters
```

**Expected Output:**
- `primaryhub`: `spoke1` and `spoke2` have `HUB ACCEPTED: true` and `AVAILABLE: True`.
- `secondaryhub`: `spoke1` and `spoke2` have `HUB ACCEPTED: false` and `AVAILABLE: Unknown`.

---

### Step 2: Simulate PrimaryHub Outage

Simulate an unexpected crash or maintenance shutdown of the PrimaryHub cluster:

```bash
docker stop primaryhub-control-plane
```

**Stream the failover controller logs on SecondaryHub:**
```bash
kubectl --context kind-secondaryhub -n opensandbox-system logs -l app.kubernetes.io/name=ocm-failover-controller -f
```

**What occurs automatically within ~4-10 seconds:**
1. **Heartbeat Failure Detected**: Probes to `10.99.0.1:6443/livez` fail consecutively.
2. **Database Promotion**: `postgresql-secondary` is promoted from read-only replica to read-write master (`pg_ctl promote`).
3. **Spoke Takeover**: SecondaryHub accepts the spoke clusters (`HUB ACCEPTED: true`) and clears `unreachable` taints.
4. **Traffic Redirection**: Envoy Gateway detects the PrimaryHub failure and routes API traffic to `10.99.0.2` (`secondaryhub`).

**Verify SecondaryHub is now serving the spokes:**
```bash
kubectl --context kind-secondaryhub get managedclusters
```
*Both `spoke1` and `spoke2` now show `HUB ACCEPTED: true` and `AVAILABLE: True` on `secondaryhub`!*

---

### Step 3: Mutate Data on SecondaryHub during Outage (Simulating Split-Brain Risk)

While PrimaryHub is completely down, create new data on SecondaryHub to simulate real user transactions during an outage:

```bash
kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 \
  -c postgres -- psql -U postgres -d apikeys \
  -c "INSERT INTO api_keys (name, key_hash) VALUES ('outage-key-01', 'hash_outage_presentation');"

# Confirm key is stored in SecondaryHub:
kubectl --context kind-secondaryhub exec -n opensandbox-system postgresql-secondary-1 \
  -c postgres -- psql -U postgres -d apikeys \
  -c "SELECT id, name, created_at FROM api_keys;"
```

---

### Step 4: Restore PrimaryHub & Observe Automated Failback

Bring PrimaryHub back online:

```bash
docker start primaryhub-control-plane
```

**Follow the reconciliation logs on SecondaryHub:**
```bash
kubectl --context kind-secondaryhub -n opensandbox-system logs -l app.kubernetes.io/name=ocm-failover-controller -f
```

**The 4-Step Failback Process:**
1. **Health Gate**: The controller waits until PrimaryHub API server and PostgreSQL become fully healthy.
2. **Authoritative Delta Sync**: Delta records created during the outage (e.g., `outage-key-01`) are synced from SecondaryHub to PrimaryHub.
3. **Valkey Resynchronization**: Cache memory is flushed and synced with PrimaryHub.
4. **Spoke Release & Demotion (Zero Split-Brain)**:
   - SecondaryHub automatically releases the spoke clusters (`HUB ACCEPTED: false`).
   - PrimaryHub re-accepts the spoke clusters (`HUB ACCEPTED: true`, `AVAILABLE: True`).
   - Secondary PostgreSQL re-clones and attaches as a standby replica of Primary.
   - Envoy Gateway shifts client traffic back to PrimaryHub.

---

### Step 5: Verify Complete Recovery and Parity

**1. Verify OCM ManagedClusters on both hubs:**
```bash
echo "=== PrimaryHub (Back in Control) ==="
kubectl --context kind-primaryhub get managedclusters

echo -e "\n=== SecondaryHub (Back in Standby) ==="
kubectl --context kind-secondaryhub get managedclusters
```
- `primaryhub`: `spoke1` and `spoke2` are `HUB ACCEPTED: true` and `AVAILABLE: True`.
- `secondaryhub`: `spoke1` and `spoke2` are released (`HUB ACCEPTED: false`, `AVAILABLE: Unknown`).

**2. Verify the outage key exists on PrimaryHub without data loss:**
```bash
kubectl --context kind-primaryhub exec -n opensandbox-system postgresql-primary-1 \
  -c postgres -- psql -U postgres -d apikeys \
  -c "SELECT id, name, created_at FROM api_keys;"
```

**3. Verify WAL replication is restored:**
```bash
kubectl --context kind-primaryhub exec -n opensandbox-system postgresql-primary-1 \
  -c postgres -- psql -U postgres \
  -c "SELECT client_addr, state, sync_state FROM pg_stat_replication;"
```

---

## Teardown

```bash
./docker-multi-cluster.sh --clean
```

This removes all KinD clusters, the transit Docker network, the Envoy Gateway container, WireGuard keys, PKI certs, and all phase state.

---

## Script Structure

```
docker-multi-script/
├── docker-multi-cluster.sh   ← Entry point (run this)
└── lib/
    ├── globals.sh             Configuration, logging, phase runner
    ├── preflight.sh           Tool install, repo clone, system tuning
    ├── network.sh             Transit net, WireGuard mesh, PKI, Envoy
    ├── clusters.sh            KinD cluster lifecycle, CRDs, OCM init
    ├── deploy.sh              Helm deployments (PrimaryHub + SecondaryHub)
    ├── kata.sh                Kata Containers + Firecracker runtime setup
    ├── gvisor.sh              Google gVisor (runsc) runtime setup
    ├── ocm.sh                 OCM spoke join + SecondaryHub sync
    └── main.sh                Teardown, verification, main() orchestrator
```
