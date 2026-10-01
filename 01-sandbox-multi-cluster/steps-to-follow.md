# Step-by-Step Installation & Operations Guide: Pure Docker Multi-Cluster Platform

This guide provides step-by-step instructions for any developer, DevOps engineer, or teammate to set up, configure, and operate the **01-Sandbox Pure Docker Multi-Cluster Platform** on a clean machine.

---

## 1. High-Level Overview: What is this Platform?

The **01-Sandbox** multi-cluster platform is an enterprise-grade, high-availability sandbox execution environment built entirely inside **Docker containers** using **KinD (Kubernetes in Docker)**.

### Why Pure Docker?
- **Zero VMs & Hypervisors**: You do **not** need VirtualBox, Vagrant, VMware, KVM, or cloud provider instances (AWS/GCP/Azure).
- **Lightweight & High Density**: Runs 4 complete Kubernetes clusters + an Envoy gateway on a standard laptop or workstation consuming only **~3.5 GB to 4.5 GB of RAM**.
- **100% Self-Contained**: Automatically provisions networking, generates cryptographic identities (PKI/Root CAs), builds custom images on-the-fly, joins clusters, and orchestrates database WAL replication without external dependencies.
- **Reboot & Crash Resilient**: Designed to survive host computer restarts, power cuts, and container reboots with zero split-brain and zero manual reconfiguration.

---

## 2. High-Level Architecture: What Gets Created?

```
┌────────────────────────────────────────────────────────────────────────┐
│                        HOST MACHINE / BROWSER                          │
│               Direct API Gateway: http://172.30.0.100                  │
│               MetalLB Ingress:    http://172.18.255.200                │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
    ┌───────────────────────────────┴─────────────────────────────────┐
    │           Docker Transit Network (01sandbox-transit)            │
    │                        172.30.0.0/24                            │
    └───────┬──────────────┬──────────────┬──────────────┬────────────┘
            │              │              │              │
      ┌─────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐
      │   Envoy    │ │ PrimaryHub │ │SecondaryHub│ │  Spokes    │
      │  Gateway   │ │  Cluster   │ │  Cluster   │ │(spoke1/2)  │
      │ 172.30.0.10│ │172.30.0.20 │ │172.30.0.21 │ │172.30.0.30/│
      └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘
            │              │              │              │
════════════╪══════════════╪══════════════╪══════════════╪════════════════
WireGuard   │ VIP          │ Active Hub   │ Standby Hub  │ Managed
Overlay     ▼              ▼              ▼              ▼ Workloads
Mesh    [10.99.0.100] [10.99.0.1]   [10.99.0.2]   [10.99.0.3 / .4]
══════════════════════════════════════════════════════════════════════════
```

### Key Components:
1. **Four Autonomous KinD Kubernetes Clusters**:
   - `primaryhub` (`10.99.0.1`): Active management control plane. Runs Open Cluster Management (OCM), read-write CloudNativePG PostgreSQL master, and primary Valkey cache.
   - `secondaryhub` (`10.99.0.2`): Warm-standby management control plane. Continuously streams database changes via physical PostgreSQL WAL streaming and replicates Valkey memory.
   - `spoke1` (`10.99.0.3`) & `spoke2` (`10.99.0.4`): Managed execution clusters running sandboxes and code analysis workloads.
2. **Envoy High-Availability Gateway (`envoy-gateway`)**:
   - Runs at `172.30.0.10` / WireGuard VIP `10.99.0.100`.
   - Exposes HTTP port `80` (FastAPI Swagger Docs / Health API) and HTTPS port `6443` (Kubernetes API server routing).
   - Directs all client and spoke traffic to `primaryhub`, automatically failing over to `secondaryhub` if primary fails.
3. **Encrypted WireGuard Mesh (`10.99.0.0/24`)**:
   - All 5 containers establish kernel-level encrypted peer-to-peer tunnels across an isolated Docker network (`172.30.0.0/24`).
4. **Automated Failover & Failback Controller**:
   - `ocm-failover-controller` on `secondaryhub` monitors primary health every 2 seconds. In an outage, it un-taints spokes and promotes the secondary database. When primary recovers, it syncs delta data and re-establishes replication with zero data corruption.

---

## 3. System Requirements & What to Expect

### Hardware & OS Requirements:
- **Operating System**: Linux (Ubuntu 20.04+, Debian 11+, Fedora 38+, RHEL 9+, Arch Linux) or Windows 11 with WSL2 (Ubuntu).
- **CPU**: 4 cores minimum (8 cores recommended).
- **RAM**: Minimum 8 GB total system RAM (the multi-cluster platform consumes **~3.5 GB to 4.5 GB** when fully active).
- **Disk Space**: At least 15 GB free disk space for Docker base images, KinD node images, and persistent volumes.
- **User Permissions**: Sudo/root privileges or user in the `docker` group.

### What to Expect During Installation:
- **First-time Run Duration**: **~3 to 5 minutes** total (depends on internet speed to pull standard base images).
- **Subsequent Runs**: **~1 to 2 minutes** (all container images and KinD node images are cached locally).
- **Zero Prompts**: The script runs 100% non-interactively from start to finish.
- **No Conflict with Host Services**: Uses isolated Docker subnets (`172.30.0.0/24`) and WireGuard VIP (`10.99.0.100`), keeping your local host network completely clean.

---

## 4. Step 1: Install Host Prerequisites

Open a bash terminal on the target machine and install the required tools:

### For Ubuntu / Debian / WSL2:
```bash
# 1. Update package manager and install core utilities
sudo apt-get update && sudo apt-get install -y \
  curl wget git jq openssl python3 python3-pip python3-cryptography

# 2. Install Docker (skip if Docker is already installed and running)
if ! command -v docker &>/dev/null; then
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER
  echo "Please log out and log back in, or run 'newgrp docker' to enable docker permissions."
fi

# 3. Install kubectl (Kubernetes CLI)
if ! command -v kubectl &>/dev/null; then
  curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
  sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
  rm kubectl
fi

# 4. Install KinD (Kubernetes in Docker)
if ! command -v kind &>/dev/null; then
  curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.27.0/kind-linux-amd64
  chmod +x ./kind
  sudo mv ./kind /usr/local/bin/kind
fi

# 5. Install Helm v3
if ! command -v helm &>/dev/null; then
  curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
fi

# 6. Install clusteradm (Open Cluster Management CLI)
if ! command -v clusteradm &>/dev/null; then
  curl -L https://raw.githubusercontent.com/open-cluster-management-io/clusteradm/main/install.sh | bash
fi
```

### For RHEL / Fedora / CentOS:
```bash
sudo dnf install -y curl git jq openssl python3 python3-pip python3-cryptography
# Install docker-ce, kubectl, kind, helm, and clusteradm using official package guides.
```

---

## 5. Step 2: Configure System File Watcher Limits

KinD runs multiple Kubernetes nodes as containers. Each node runs `kubelet` and `containerd`, which watch container logs and volume mounts. Increase the Linux inotify limits to prevent "too many open files" errors:

```bash
# Apply immediately
sudo sysctl fs.inotify.max_user_watches=524288
sudo sysctl fs.inotify.max_user_instances=512

# Persist across computer reboots
echo "fs.inotify.max_user_watches=524288" | sudo tee -a /etc/sysctl.d/99-kind.conf
echo "fs.inotify.max_user_instances=512" | sudo tee -a /etc/sysctl.d/99-kind.conf
sudo sysctl --system
```

---

## 6. Step 3: Clone Repository & Prepare Directory

Clone or navigate to the repository on your machine:

```bash
cd /path/to/01-Sandbox

# Verify execution permissions on the management scripts
chmod +x docker-multi-cluster.sh docker-multi-cluster-destroy.sh
```

---

## 7. Step 4: Run the One-Shot Provisioning Script

Run the automated provisioning script:

```bash
./docker-multi-cluster.sh
```

### Execution Flow: What Happens During the Run

The script executes 11 automated phases sequentially:

```
[Phase 1] Checking host prerequisites (docker, kind, kubectl, helm, clusteradm, jq)
[Phase 2] Setting up 01sandbox-transit Docker network (172.30.0.0/24) & generating WireGuard keys
[Phase 3] Creating KinD clusters (primaryhub, secondaryhub, spoke1, spoke2)
[Phase 4] Configuring WireGuard overlay mesh (10.99.0.0/24) inside each node
[Phase 5] Deploying Envoy HA Gateway container (10.99.0.100 & 172.30.0.100)
[Phase 6] Synchronizing Kubernetes Root CA between Primary and Secondary Hubs
[Phase 7] Initializing Open Cluster Management (OCM) control plane on both Hubs
[Phase 8] Joining spoke clusters through Envoy VIP & approving CSRs
[Phase 9] Sideloading images and deploying CloudNativePG PostgreSQL & Valkey with replication
[Phase 10] Deploying automated failover controller on SecondaryHub
[Phase 11] Running comprehensive end-to-end health verification
```

### Completion Banner:
When the script successfully finishes, you will see:
```text
======================================================================
🎉 PURE DOCKER MULTI-CLUSTER SETUP COMPLETE (ZERO VMS REQUIRED)!
======================================================================

Web UI / API Endpoints:
  • Swagger Docs (Direct Envoy):  http://172.30.0.100/docs
  • Health Endpoint:              http://172.30.0.100/health
  • MetalLB VIP Ingress:          http://172.18.255.200
  • Kubernetes API VIP:           https://10.99.0.100:6443

Kubectl Contexts:
  • Primary Hub:    kubectl config use-context kind-primaryhub
  • Secondary Hub:  kubectl config use-context kind-secondaryhub
  • Spoke 1:        kubectl config use-context kind-spoke1
  • Spoke 2:        kubectl config use-context kind-spoke2
```

---

## 8. Step 5: Verify System Health & Status

### Built-in System Verification
You can run the health check suite at any time:
```bash
./docker-multi-cluster.sh --verify
```

### What You Should Expect to See:
1. **OCM Hub Status**:
   - `primaryhub`: `spoke1` and `spoke2` are `Accepted: true` and `Available: True`.
   - `secondaryhub`: `spoke1` and `spoke2` are pre-synchronized in standby mode.
2. **CloudNativePG PostgreSQL Streaming**:
   - Primary: shows `postgresql-secondary` with `state: streaming`, `sync_state: async`.
   - Secondary: shows `status: streaming`, `sender_host: 10.99.0.1`, `sender_port: 30432`.
3. **Valkey Replication**:
   - Secondary shows `role:slave`, `master_host:10.99.0.1`, `master_link_status:up`.
4. **Pod Status**:
   All pods in `opensandbox-system` on both hubs show `1/1 Running`.

### Manual Inspection via `kubectl`:
```bash
# Check Primary Hub Pods
kubectl --context kind-primaryhub -n opensandbox-system get pods

# Check Secondary Hub Pods
kubectl --context kind-secondaryhub -n opensandbox-system get pods

# Check Spoke Clusters
kubectl --context kind-spoke1 get nodes
kubectl --context kind-spoke2 get nodes
```

---

## 9. How to Access the APIs and Interfaces

### 1. Browser & HTTP Clients
Because Envoy is attached to the Docker transit network (`172.30.0.0/24`), it is accessible directly from your host browser or via curl:

| Endpoint | Purpose | Link / Command |
| :--- | :--- | :--- |
| **Interactive API Documentation** | FastAPI Swagger UI | [http://172.30.0.100/docs](http://172.30.0.100/docs) |
| **System Health Check** | Comprehensive subsystem status | [http://172.30.0.100/health](http://172.30.0.100/health) |
| **OpenAPI Schema** | Raw OpenAPI JSON specification | [http://172.30.0.100/openapi.json](http://172.30.0.100/openapi.json) |
| **MetalLB LoadBalancer VIP** | Direct cluster service VIP | `http://172.18.255.200` |

Test the API right from your terminal:
```bash
curl -s http://172.30.0.100/health | jq .
```

### 2. Switching Kubernetes Clusters
The script automatically configures your `~/.kube/config` with descriptive contexts:
```bash
# Target Primary Hub (Active)
kubectl config use-context kind-primaryhub

# Target Secondary Hub (Standby)
kubectl config use-context kind-secondaryhub

# Target Execution Cluster 1
kubectl config use-context kind-spoke1

# Target Execution Cluster 2
kubectl config use-context kind-spoke2
```

---

## 10. Testing Reboot Resilience & Automated Failover

### A. Testing Machine Reboot Survival
All state (WireGuard keys, Root CA certificates, Envoy routing configs, and standby manifests) is stored on physical disk in `.sandbox-state/` (persisting outside container lifecycles). Container restart policies are set to `unless-stopped` and `always`.

To simulate a complete computer reboot without restarting your physical machine:
```bash
# Restart all 5 multi-cluster containers simultaneously
docker restart primaryhub-control-plane secondaryhub-control-plane spoke1-control-plane spoke2-control-plane envoy-gateway

# Wait ~15 seconds for Kubernetes control planes to initialize, then verify:
./docker-multi-cluster.sh --verify
```
*Result: WireGuard reconnects immediately, Envoy re-establishes routes, PostgreSQL and Valkey resume replication automatically.*

---

### B. Testing Active-Passive Failover & Zero Split-Brain Failback

#### 1. Simulate Primary Hub Failure:
Stop the active primary hub container:
```bash
docker stop primaryhub-control-plane
```

#### 2. Observe Automatic Secondary Hub Promotion:
Stream the failover controller logs on SecondaryHub:
```bash
kubectl --context kind-secondaryhub -n opensandbox-system logs -l app.kubernetes.io/name=ocm-failover-controller -f
```
*Within ~10 seconds:*
- The controller detects 3 consecutive health probe failures on `10.99.0.1`.
- Promotes Secondary PostgreSQL from standby replica to read-write Master.
- Un-taints spoke clusters so execution workloads continue without disruption.
- Envoy Gateway shifts ingress API traffic to `10.99.0.2` (`secondaryhub`).

#### 3. Recover Primary Hub (Failback):
Start the primary hub container back up:
```bash
docker start primaryhub-control-plane
```
*What happens:*
- The controller detects PrimaryHub recovery.
- Runs the `failback-delta-sync` Job to sync any delta rows written to SecondaryHub during the outage.
- Re-clones Secondary PostgreSQL as a standby replica, preventing split-brain.
- Restores PrimaryHub as active Master with zero downtime.

---

## 11. Complete Teardown & Clean Reset

If you want to completely remove the platform and return your machine to a pristine state:

```bash
./docker-multi-cluster-destroy.sh
```

### What gets removed:
- All 4 KinD clusters (`primaryhub`, `secondaryhub`, `spoke1`, `spoke2`).
- The `envoy-gateway` container.
- Docker networks `01sandbox-transit` and `kind`.
- Kubernetes contexts from `~/.kube/config`.
- Local configuration directory `.sandbox-state/`.
- Prunes all dangling Docker volumes.

To redeploy everything from scratch, simply run `./docker-multi-cluster.sh` again.

---

## 12. Frequently Asked Questions (FAQ) & Troubleshooting

### Q1: `docker-multi-cluster.sh` reports missing dependencies?
Ensure all required binaries are in your `$PATH`:
```bash
which docker kind kubectl helm clusteradm jq openssl python3
```
If Python cryptography is missing:
```bash
pip3 install cryptography # or sudo apt-get install python3-cryptography
```

### Q2: KinD fails to start containers with "too many open files" or exit code 127?
This happens when Linux default file watch handles are exhausted. Run:
```bash
sudo sysctl -w fs.inotify.max_user_watches=524288
sudo sysctl -w fs.inotify.max_user_instances=512
```

### Q3: How do I view logs of a specific component?
```bash
# Failover controller
kubectl --context kind-secondaryhub -n opensandbox-system logs -l app.kubernetes.io/name=ocm-failover-controller -f

# PostgreSQL cluster status (CloudNativePG)
kubectl --context kind-primaryhub -n opensandbox-system get cluster.postgresql.cnpg.io

# Envoy Gateway traffic logs
docker logs envoy-gateway --tail=50 -f

# API Server logs
kubectl --context kind-primaryhub -n opensandbox-system logs -l app.kubernetes.io/name=sandbox-api -f
```

### Q4: Can I use this setup in CI/CD (GitHub Actions / GitLab CI)?
**Yes!** Because it runs purely in Docker, it runs directly inside standard GitHub Actions runners (`ubuntu-latest`) or Docker-in-Docker CI agents without nested virtualization support. Simply run the steps in Section 4 and execute `./docker-multi-cluster.sh`.
