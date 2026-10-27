# 🛠️ Multi-Cluster Platform (AWS EC2 Production) – Troubleshooting & Root Cause Analysis

This document provides a comprehensive post-mortem and reference guide for the operational issues encountered during the automated deployment of the **01-Sandbox multi-region, multi-cluster Kubernetes platform** across AWS EC2 instances, along with the architectural solutions and code enhancements implemented.

---

## Table of Contents

1. [Issue 1: SSH "Too many authentication failures"](#issue-1-ssh-too-many-authentication-failures)
2. [Issue 2: Remote Dependency Detection & Package Manager Mismatch ("kind: command not found")](#issue-2-remote-dependency-detection--package-manager-mismatch-kind-command-not-found)
3. [Issue 3: Remote Node `kubectl` Failing with `localhost:8080 Connection Refused`](#issue-3-remote-node-kubectl-failing-with-localhost8080-connection-refused)
4. [Issue 4: Local vs Remote Script Environment Directory Ambiguity](#issue-4-local-vs-remote-script-environment-directory-ambiguity)
5. [Issue 5: Kubeconfig Port Stale Pollution & AWS Security Group Restrictions (`127.0.0.1:35085 Connection Refused`)](#issue-5-kubeconfig-port-stale-pollution--aws-security-group-restrictions-12700135085-connection-refused)
6. [Issue 6: Docker Image Streaming Authentication Failures](#issue-6-docker-image-streaming-authentication-failures)
7. [Issue 7: Pods in ErrImagePull Due to 8 GiB EBS Volume Exhaustion ("no space left on device")](#issue-7-pods-in-errimagepull-due-to-8-gib-ebs-volume-exhaustion-no-space-left-on-device)
8. [Architecture Reference: SSH Tunnel & Port Mapping Matrix](#architecture-reference-ssh-tunnel--port-mapping-matrix)

---

## Issue 1: SSH "Too many authentication failures"

### Symptom
During Phase 02/03 SSH execution and remote SCP file transfers:
```text
Received disconnect from 52.213.158.85 port 22:2: Too many authentication failures
scp: Connection closed
Received disconnect from 13.234.240.239 port 22:2: Too many authentication failures
```

### Root Cause
When connecting via SSH, the SSH client automatically offers all keys loaded into the local SSH agent (`ssh-agent`) or default key files (`~/.ssh/id_rsa`, `~/.ssh/id_ed25519`, etc.) before offering the key specified by `-i ~/.ssh/kamalaws.pem`. When the remote AWS SSH daemon's `MaxAuthTries` threshold (typically 6) was reached, the server disconnected immediately.

### Solution Adapted
1. Added `-o IdentitiesOnly=yes` to all `ssh` and `scp` invocation wrappers in [`lib/globals.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/prod-docker-multi-script/lib/globals.sh):
   ```bash
   ssh -p "$SSH_PORT" -i "$key" \
     -o IdentitiesOnly=yes \
     -o StrictHostKeyChecking=no \
     -o UserKnownHostsFile=/dev/null \
     -o ConnectTimeout=10 -o BatchMode=yes -o LogLevel=ERROR \
     "${user}@${host}" "$@"
   ```
2. Sanitized host definitions in `prod.env` to strictly contain IP addresses (stripping any accidental `ec2-user@` prefix) to prevent malformed hostnames like `ec2-user@ec2-user@<IP>`.

---

## Issue 2: Remote Dependency Detection & Package Manager Mismatch ("kind: command not found")

### Symptom
In Phase 03 when creating the first remote KinD cluster:
```text
[INFO] Creating remote KinD cluster 'primaryhub' on 52.213.158.85 (Pod:10.244.0.0/16 Svc:10.96.0.0/16)...
bash: line 1: kind: command not found
[INFO] Syncing remote kubeconfig for 'primaryhub' from 52.213.158.85...
scp: /tmp/kubeconfig-primaryhub.export: No such file or directory
```

### Root Cause
1. **Flawed Preflight Predicate**: The original `_check_phase_01()` in [`lib/preflight.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/prod-docker-multi-script/lib/preflight.sh) only inspected the local orchestrator machine (`command -v kind`). Since the local developer laptop already had `kind` installed, Phase 01 declared `[OK] Phase 01 – already configured. Skipping.` without inspecting the remote EC2 instances.
2. **Package Manager Mismatch**: The instances were running **Amazon Linux 2023 (`amzn2023`)** where the package manager is `dnf`. The original bootstrap script hardcoded Ubuntu/Debian commands (`apt-get install docker.io`), which failed silently on Amazon Linux.

### Solution Adapted
1. **Remote Probing in `_check_phase_01()`**: Added a verification loop across all configured remote instances (`PRIMARYHUB_HOST`, `SECONDARYHUB_HOST`, `SPOKE1_HOST`, `SPOKE2_HOST`) checking `docker`, `kind`, `kubectl`, `git`, and `wireguard-tools`. If any tool is missing on any instance, Phase 01 does not skip.
2. **Dynamic Multi-OS Bootstrap Engine**:
   - Auto-detects `dnf` / `yum` (Amazon Linux, RHEL, Fedora) or `apt-get` (Ubuntu, Debian).
   - Tests each dependency individually (`command -v <tool>`). If already installed, logs `[OK] <tool> already installed. Skipping.`
   - Installs missing dependencies (`docker`, `git`, `wireguard-tools`, `iptables-nft`, `iproute`).
   - Downloads static official binaries for `kind` (`v0.27.0`) and `kubectl` (`v1.31.0`) directly into `/usr/local/bin`.
   - Starts and enables `docker.service` and configures user socket permissions.

---

## Issue 3: Remote Node `kubectl` Failing with `localhost:8080 Connection Refused`

### Symptom
When SSHing into the `primaryhub` EC2 instance (`[ec2-user@ip-10-22-0-21 ~]$`) and running `kubectl get pods -A`:
```text
Client Version: v1.31.0
The connection to the server localhost:8080 was refused - did you specify the right host or port?
```

### Root Cause
1. KinD had not finished creating the cluster due to the earlier `kind: command not found` error.
2. Even when KinD creates a cluster, `kubectl` expects a configuration file at `~/.kube/config`. In the initial script, kubeconfig was only exported to `/tmp/kubeconfig-${name}.export` for SCP retrieval, leaving the default user home directory without a valid config.

### Solution Adapted
Enhanced `_create_kind_cluster` in [`lib/clusters.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/prod-docker-multi-script/lib/clusters.sh#L157) so that immediately upon cluster creation, the remote EC2 user's local `~/.kube/config` is configured:
```bash
remote_exec "$target_host" "mkdir -p ~/.kube && kind export kubeconfig --name '$name' --kubeconfig ~/.kube/config 2>/dev/null || true"
```
Now, whenever an administrator SSHs into any EC2 instance, running `kubectl` immediately connects to that node's KinD cluster without any manual exports.

---

## Issue 4: Local vs Remote Script Environment Directory Ambiguity

### Symptom
The deployment script started running deployments on the local developer laptop instead of the AWS EC2 instances, populating the local Docker daemon with control plane containers.

### Root Cause
The repository has two distinct directories:
- `docker-multi-script`: Configured with `127.0.0.1` / `localhost` for single-machine local sandboxing and development.
- `prod-docker-multi-script`: Configured for multi-region AWS EC2 deployment via SSH keypair authentication.

Running `./docker-multi-cluster.sh` from the wrong working directory targeted the local machine.

### Solution Adapted
- Documented clear separation in documentation.
- Cleaned up local containers using `./docker-multi-cluster.sh --clean` in `docker-multi-script`.
- Ensured all production operations strictly execute within `prod-docker-multi-script`.

---

## Issue 5: Kubeconfig Port Stale Pollution & AWS Security Group Restrictions (`127.0.0.1:35085 Connection Refused`)

### Symptom
During Phase 08 (OCM Initialization) and Phase 09 (Namespaces):
```text
Preflight check: cluster-info check Failed with 0 warnings and 1 errors
Error: [preflight] Some fatal errors occurred:
        [ERROR cluster-info check]: Get "https://127.0.0.1:35085/api/v1/namespaces/kube-public/configmaps/cluster-info": dial tcp 127.0.0.1:35085: connect: connection refused

error validating ... failed to download openapi: Get "https://127.0.0.1:35085/openapi/v2?timeout=32s": dial tcp 127.0.0.1:35085: connect: connection refused
error validating ... failed to download openapi: Get "https://127.0.0.1:34507/openapi/v2?timeout=32s": dial tcp 127.0.0.1:34507: connect: connection refused
```

### Root Cause
1. **Stale Port Pollution**: Ports `35085` and `34507` were ephemeral ports assigned by KinD on the local laptop during earlier local runs. When `_sync_remote_kubeconfig` ran:
   ```bash
   KUBECONFIG="${KUBECONFIG}:${tmp_remote_kube}" kubectl config view --flatten > /tmp/kubeconfig-merged
   ```
   `kubectl config view --flatten` gives precedence to the **first** file in the merge list. Because `~/.kube/config` already contained `kind-primaryhub` pointing to `127.0.0.1:35085`, the new remote definition was ignored and dropped.
2. **AWS Security Group Firewall**: AWS Security Groups only permit inbound port `22` (SSH) from the administrator IP. External connections to Kubernetes API port `6443` directly over public IPs (`https://52.213.158.85:6443`) timed out.

### Solution Adapted
1. **Automated Secure SSH Port Forwarding**:
   Rather than exposing Kubernetes API ports (`6443`) insecurely to the public internet, each remote cluster is assigned a dedicated local SSH tunnel port:
   - `kind-primaryhub`: `127.0.0.1:16443` $\rightarrow$ `52.213.158.85:6443`
   - `kind-secondaryhub`: `127.0.0.1:26443` $\rightarrow$ `13.234.240.239:6443`
   - `kind-spoke1`: `127.0.0.1:36443` $\rightarrow$ `18.197.200.4:6443`
   - `kind-spoke2`: `127.0.0.1:46443` $\rightarrow$ `34.235.149.38:6443`
2. **Tunnel Engine in `lib/globals.sh`**:
   Implemented `ensure_cluster_ssh_tunnel` and `ensure_all_cluster_tunnels`, hooked directly into `run_phase`. Before any phase runs `kubectl`, `helm`, or `clusteradm`, the respective background SSH tunnels are checked and activated.
3. **Deterministic Kubeconfig Synchronization**:
   Updated `_sync_remote_kubeconfig` in [`lib/clusters.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/prod-docker-multi-script/lib/clusters.sh#L32):
   - Deletes any existing stale context/cluster/user for `kind-${name}` from local `~/.kube/config`.
   - Rewrites the server URL in `/tmp/kubeconfig-${name}` to `https://127.0.0.1:<tunnel_port>`.
   - Merges with `KUBECONFIG="${tmp_remote_kube}:${KUBECONFIG}"`, ensuring the fresh remote configuration always takes precedence.

---

## Issue 6: Docker Image Streaming Authentication Failures

### Symptom
Streaming custom images (`01sandbox-opensandbox-server` and scanner images) to remote clusters via `docker save | ssh ... ctr images import` occasionally broke with authentication or connection drops.

### Root Cause
Raw `ssh` calls in Phase 10 and Phase 15d omitted `-o IdentitiesOnly=yes` and `-p "$SSH_PORT"`, triggering the same `Too many authentication failures` issue observed in Issue 1 under heavy pipe I/O.

### Solution Adapted
Hardened lines 741 and 945 in [`lib/clusters.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/prod-docker-multi-script/lib/clusters.sh):
```bash
docker save "$img" | ssh -p "$SSH_PORT" -i "$SSH_KEY_PATH" \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=no \
  "${SSH_USER}@${target_host}" \
  "docker exec -i ${hub}-control-plane ctr -n k8s.io images import --local -"
```

---

## Issue 7: Pods in ErrImagePull Due to 8 GiB EBS Volume Exhaustion ("no space left on device")

### Symptom
In Phase 11b on `primaryhub`, several pods entered `ErrImagePull` / `ImagePullBackOff`:
```text
opensandbox-controller-5fd46b78b7-v8njq   0/1     ErrImagePull   0          2m22s
sandbox-api-dc64f5d69-z4lhz               0/1     ErrImagePull   0          2m27s
```
Inspecting the pod events with `kubectl describe pod opensandbox-controller-...` revealed:
```text
Warning  Failed   kubelet   Failed to pull image "01community/01sandbox-opensandbox-controller:v0.7.11":
failed to extract layer ... no space left on device
```

### Root Cause
1. **8 GiB Default Root Volume**: The AWS EC2 instances were launched with AWS's default 8 GiB EBS root volume (`/dev/nvme0n1 8G`).
2. **Disk Exhaustion**:
   - Host OS + Docker base packages: ~1.7 GiB
   - KinD node image (`kindest/node:v1.32.2`): ~1.2 GiB
   - Workload images downloaded into containerd (Postgres, RabbitMQ, Valkey, Sealed Secrets, AgentGateway, Opensandbox Server): ~4.9 GiB
   - Total space consumed: **8.0 GiB out of 8.0 GiB (100% full, 0 bytes free)**.
   When containerd attempted to unpack subsequent layers for `opensandbox-controller` and `sandbox-api`, the filesystem ran out of space.
3. **Repository Not Cloned on EC2**: The user noticed the Git repo is not present on the EC2 instance. This is by design: EC2 instances are Kubernetes cluster compute nodes. All applications run as containerized Docker images deployed by the orchestrator. Storing the Git source code and commit history on the EC2 instances would consume even more disk space unnecessarily.

### Solution Adapted
1. **AWS EBS Volume Expansion (Online, Zero Downtime)**:
   In the AWS Management Console:
   - Navigate to **EC2 $\rightarrow$ Elastic Block Store $\rightarrow$ Volumes**.
   - Select the root volume for each instance (`primaryhub`, `secondaryhub`, `spoke1`, `spoke2`).
   - Click **Actions $\rightarrow$ Modify Volume**.
   - Increase the size from **8 GiB** to **30 GiB** (or **40 GiB** as configured in `terraform/main.tf`).
   - Click **Modify**.
2. **Grow Partition and Filesystem Live**:
   Once AWS finishes modifying the volume (takes ~15 seconds), run the following on each EC2 instance:
   ```bash
   sudo growpart /dev/nvme0n1 1
   sudo xfs_growfs -d /
   ```
   This immediately expands the XFS root filesystem live without restarting the instance.
3. **Recovery**:
   Once disk space is available, Kubernetes automatically retries pulling the container images and all pods transition to `1/1 Running`.

---

## Architecture Reference: SSH Tunnel & Port Mapping Matrix

| Cluster Name | Role | Region | Host Public IP | Tunnel Port (Local) | Target Node Port | Overlay IP (WireGuard) |
|---|---|---|---|---|---|---|
| **`primaryhub`** | Active OCM Hub & DB Primary | `us-east-1` / `eu-west-1` | `52.213.158.85` | **`16443`** | `6443` | `10.99.0.1` |
| **`secondaryhub`** | Standby OCM Hub & DB Standby | `ap-south-1` | `13.234.240.239` | **`26443`** | `6443` | `10.99.0.2` |
| **`spoke1`** | Managed Workload Cluster | `eu-central-1` | `18.197.200.4` | **`36443`** | `6443` | `10.99.0.3` |
| **`spoke2`** | Managed Workload Cluster | `us-east-1` | `34.235.149.38` | **`46443`** | `6443` | `10.99.0.4` |
| **`Envoy Gateway`** | Reverse Proxy / VIP Entry | Co-located on PrimaryHub | `52.213.158.85` | N/A | `80`, `443` | `10.99.0.100` |

---

## Verification Summary

All fixes have been validated live:
- **Phase 01**: Preflight skips automatically when all 4 EC2 instances are verified, or selectively installs missing dependencies.
- **Phase 03**: Clusters created on remote nodes; remote `~/.kube/config` and local SSH tunnels configured.
- **Phase 08**: `clusteradm init` passed preflight on both `primaryhub` and `secondaryhub`; OCM cluster managers and registration webhooks active.
- **Phase 09**: All application namespaces created and confirmed active on both hubs.
