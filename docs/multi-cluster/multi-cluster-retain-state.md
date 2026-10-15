# Multi-Cluster State Retention Architecture
## Technical Deep-Dive: `user_data.sh.tftpl`

> **Document Scope:** This document provides a detailed technical explanation of every section in the `user_data.sh.tftpl` boot script that runs on every EC2 Spot instance across all 4 regions of the sandbox multi-cluster environment. The same script template is used across all regions — per-instance behaviour is driven by Terraform variables injected at render time.

---

## Table of Contents

1. [Overview & Architecture](#1-overview--architecture)
2. [Cluster-to-Region Mapping](#2-cluster-to-region-mapping)
3. [Terraform Template Variables](#3-terraform-template-variables)
4. [Script Walkthrough](#4-script-walkthrough)
5. [Persistent Storage Layout on EBS](#5-persistent-storage-layout-on-ebs)
6. [Role-Specific Behaviour](#6-role-specific-behaviour)
7. [State That Survives a Spot Replacement](#7-state-that-survives-a-spot-replacement)
8. [State That Does NOT Survive](#8-state-that-does-not-survive-must-re-run)
9. [Boot Sequence Timeline](#9-boot-sequence-timeline)

---

## 1. Overview & Architecture

When an EC2 Spot instance is **interrupted** by AWS (e.g. due to capacity reclamation), the Auto Scaling Group (ASG) automatically launches a **replacement Spot instance**. On its own, a fresh EC2 instance would know nothing about the Kubernetes cluster it is supposed to host.

The `user_data.sh.tftpl` script is the **single source of truth** for bootstrapping and restoring state. It runs automatically on every instance boot via AWS cloud-init and performs the following high-level functions:

```
New Spot Instance Boots
       │
       ▼
[IMDSv2] Get instance metadata
       │
       ▼
[EBS] Detach from terminated instance → Attach to self
       │
       ▼
[Filesystem] Mount /data (XFS) — all cluster state lives here
       │
       ▼
[Docker] Start with data-root = /data/docker (restores KinD container)
       │
       ▼
[PKI] Restore shared CA certs from /data/pki → /tmp/pki
       │
       ▼
[WireGuard] Restore overlay mesh → sync peer IPs dynamically
       │
       ▼
[KinD] Start existing control-plane container → wait for API server
       │
       ▼
[Kubeconfig] Export + rewrite to 127.0.0.1:6443 for all users
       │
       ▼
[Role Recovery] Spoke: restart kata-fc + klusterlet agents
                Hub:   resume Envoy gateway / standby election
       │
       ▼
Bootstrap Complete
```

---

## 2. Cluster-to-Region Mapping

Each region runs one EC2 instance that hosts one KinD cluster. The template is **identical** across regions; only the Terraform-injected variables change.

| Cluster Role | AWS Region | KinD Cluster Name | WireGuard Overlay IP |
|:---|:---|:---|:---|
| **PrimaryHub** (active OCM hub + Envoy gateway) | `us-east-1` | `primaryhub` | `10.99.0.1` |
| **SecondaryHub** (standby OCM hub + failover controller) | `eu-west-1` | `secondaryhub` | `10.99.0.2` |
| **Spoke1** (workload cluster — kata-fc + gVisor) | `eu-central-1` | `spoke1` | `10.99.0.3` |
| **Spoke2** (workload cluster — kata-fc + gVisor) | `ap-south-1` | `spoke2` | `10.99.0.4` |

---

## 3. Terraform Template Variables

The file is a Terraform `templatefile` (`.tftpl` extension). Terraform renders it into a plain shell script by replacing all `${variable}` and `%{ if %}` blocks **before** the instance boots. The rendered script is base64-encoded and passed as the EC2 Launch Template user-data.

| Template Variable | What It Resolves To | Source |
|:---|:---|:---|
| `${region}` | AWS region string, e.g. `us-east-1` | `var.region` in `variables.tf` |
| `${volume_id}` | EBS Volume ID, e.g. `vol-0abc1234` | `aws_ebs_volume.persistent[name].id` |
| `${cluster_name}` | The KinD cluster name, e.g. `primaryhub` | `name` (loop variable from `local.instances`) |
| `${device_name}` | Linux device hint, e.g. `/dev/sdf` | Hardcoded in `data.tf` as `/dev/sdf` |
| `${mount_point}` | Where EBS is mounted, e.g. `/data` | `var.persistent_mount_point` |
| `${enable_password_auth}` | Boolean (`true`/`false`) | `var.enable_password_auth` |
| `${secret_id}` | AWS Secrets Manager secret name for SSH password | `"${var.name}/ssh-password"` |
| `${ssh_username}` | SSH user to configure password for | `var.ssh_username` |

> **Note:** The `$${...}` double-dollar syntax (e.g., `$${REGIONS[$cluster]}`) is **not** a Terraform variable. The double `$$` is a Terraform escape sequence that renders as a single `$` in the final shell script, allowing bash associative arrays to coexist with Terraform templating.

---

## 4. Script Walkthrough

### Preamble & Shell Setup

```bash
#!/bin/bash
set -euo pipefail
exec > >(tee -a /var/log/user-data.log | logger -t user-data -s 2>/dev/console) 2>&1
```

| Line | Purpose |
|:---|:---|
| `#!/bin/bash` | Runs under bash (not sh). Required for associative arrays used later. |
| `set -euo pipefail` | `-e` = abort on error; `-u` = abort on unset vars; `-o pipefail` = abort if any piped cmd fails |
| `exec > >(tee …)` | Redirects ALL stdout/stderr to `/var/log/user-data.log` AND the system journal via `logger` |

---

### Section 1 — IMDSv2 Metadata Retrieval

```bash
TOKEN=$(curl -sS -X PUT "http://169.254.169.254/latest/api/token" \
        -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
INSTANCE_ID=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
        "http://169.254.169.254/latest/meta-data/instance-id")
AZ=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" \
        "http://169.254.169.254/latest/meta-data/placement/availability-zone")
```

**What is IMDSv2?**
IMDS (Instance Metadata Service) v2 is the secure token-based API that lets an EC2 instance query facts about itself at `169.254.169.254`. This IP is a link-local address — it only works from within the instance, never routed externally.

- The script first fetches a **session token** (valid 300 seconds) with a `PUT` request.
- It then uses that token as a header in subsequent `GET` requests.
- This is safer than IMDSv1 (unauthenticated) and is **required** by `http_tokens = "required"` in the Launch Template metadata options.

Terraform-rendered variables (`REGION`, `VOLUME_ID`, `CLUSTER_NAME`) are set here and used throughout the rest of the script.

---

### Section 2 — EBS Volume Detachment from Old Instance

```bash
for attempt in $(seq 1 30); do
  ATTACHED_INSTANCE=$(aws ec2 describe-volumes ...)
  VOLUME_STATE=$(aws ec2 describe-volumes ...)

  if   [ "$VOLUME_STATE" = "available" ];              then break
  elif [ "$ATTACHED_INSTANCE" = "$INSTANCE_ID" ];      then break
  elif [ -n "$ATTACHED_INSTANCE" ] && [ "$ATTACHED_INSTANCE" != "None" ]; then
    aws ec2 detach-volume --force ...
    sleep 5
  else
    sleep 3
  fi
done
```

**Why is this necessary?**

When a Spot instance is interrupted, AWS terminates it within seconds. The EBS volume that was attached transitions from `in-use` to `available` — but this can take a few seconds (or longer if the OS didn't flush cleanly). The **new replacement instance launches before the old volume is detached**.

This retry loop (up to 30 × ~5s = ~150 seconds max) polls the volume state and calls `detach-volume --force` if the old instance still holds it. `--force` tells AWS to detach without waiting for a clean OS unmount (safe because the old instance is already terminated).

| Volume State | Action |
|:---|:---|
| `available` | Break loop — ready to attach |
| Attached to **this** instance | Break loop — already done |
| Attached to **another** instance | Force detach and wait |
| Unknown / transitioning | Wait 3 seconds and retry |

---

### Section 3 — Attach EBS Volume to New Instance

```bash
if [ "$CURRENT_ATTACHMENT" != "$INSTANCE_ID" ]; then
  aws ec2 attach-volume \
    --volume-id "$VOLUME_ID" \
    --instance-id "$INSTANCE_ID" \
    --device "$DEVICE" \
    --region "$REGION"
  aws ec2 wait volume-in-use --volume-ids "$VOLUME_ID" --region "$REGION"
fi
```

1. Re-checks the attachment state (idempotent guard).
2. Calls `attach-volume` to instruct AWS to attach the persistent EBS volume to **this instance** as `/dev/sdf`.
3. Uses `aws ec2 wait volume-in-use` — a built-in AWS CLI waiter that polls every 15 seconds until the volume state is `in-use`. This is a **blocking call** that guarantees the volume is attached before continuing.

> **Why `/dev/sdf`?** This is the "hint" device name from Terraform. On older non-Nitro instances, the volume appears as `/dev/sdf`. On Nitro-based instances (all modern types), the kernel renames NVMe EBS volumes to `/dev/nvme[1-9]n1`, which is why Section 4 exists.

---

### Section 4 — Block Device Resolution (Nitro NVMe)

```bash
VOL_SERIAL=$(echo "$VOLUME_ID" | tr -d '-')   # vol-0abc1234 → vol0abc1234

for attempt in $(seq 1 30); do
  # Method 1: Try hint device name directly
  if [ -b "$DEVICE" ]; then TARGET_DEV="$DEVICE"; break; fi

  # Method 2: Match NVMe device by EBS volume serial number via udevadm
  for dev in /dev/nvme*n1; do
    SERIAL=$(udevadm info --query=property --name="$dev" | grep "ID_SERIAL_SHORT" | cut -d'=' -f2)
    if [[ "$SERIAL" == *"$VOL_SERIAL"* ]]; then TARGET_DEV="$dev"; break 2; fi
  done

  # Method 3: Fallback — pick any non-root disk via lsblk
  NON_ROOT_DISKS=$(lsblk -d -n -o NAME,TYPE | awk '$2=="disk" && $1!~/nvme0n1/ && $1!~/xvda/')
  if [ -n "$NON_ROOT_DISKS" ]; then TARGET_DEV=$(echo "$NON_ROOT_DISKS" | head -n 1); break; fi

  sleep 2
done
```

**The Nitro NVMe problem:**

AWS Nitro instances present EBS volumes as NVMe block devices rather than the `/dev/sdX` name you specify in the API. When you call `attach-volume --device /dev/sdf`, the kernel names it `/dev/nvme1n1` (or `nvme2n1`, etc.) depending on initialization order.

| Method | How It Works | Best For |
|:---|:---|:---|
| **Direct device check** | Checks if `/dev/sdf` exists as a block device | Non-Nitro or udev aliased instances |
| **NVMe serial matching** | Strips dashes from the EBS volume ID, queries each NVMe device's `ID_SERIAL_SHORT` via `udevadm`, matches substring | All modern Nitro instances (most reliable) |
| **Fallback lsblk scan** | Finds any disk that isn't the root volume | When only one non-root disk exists |

The 30-attempt loop with 2-second sleeps accounts for the kernel not having initialized the device by the time `attach-volume` returns.

---

### Section 5 — Filesystem Formatting (Zero Data Loss)

```bash
if ! blkid "$TARGET_DEV" >/dev/null 2>&1; then
  echo "No existing filesystem found. Formatting as XFS..."
  mkfs -t xfs "$TARGET_DEV"
else
  echo "Existing filesystem detected: $(blkid -o value -s TYPE). Preserving data."
fi
```

`blkid` probes a block device for filesystem signatures.

- **No signature found** → Volume is brand new (first Terraform deploy). Formats as **XFS**.
- **Signature found** → An existing filesystem is present. **Skips formatting entirely** — this is the zero data loss guarantee for Spot replacements.

**Why XFS?** It is a high-performance 64-bit journaling filesystem, handles large files efficiently (Docker image layers, etcd snapshots), supports online resizing, and is the default on Amazon Linux 2023.

---

### Section 6 — Mount & fstab Persistence

```bash
mkdir -p "$MOUNT_POINT"
UUID=$(blkid -s UUID -o value "$TARGET_DEV")

if ! grep -q "$UUID" /etc/fstab; then
  echo "UUID=$UUID $MOUNT_POINT xfs defaults,nofail 0 2" >> /etc/fstab
fi

if ! mountpoint -q "$MOUNT_POINT"; then
  mount -a
fi
```

1. Creates the `/data` directory if it doesn't exist.
2. Extracts the filesystem **UUID** — stable across device renames (`nvme1n1` → `nvme2n1`).
3. Adds the UUID to `/etc/fstab` if not already there (idempotent — no duplicate entries).
4. Runs `mount -a` to mount all fstab entries not yet mounted.

**Mount options:**

| Option | Purpose |
|:---|:---|
| `defaults` | Standard options: rw, suid, dev, exec, auto, nouser, async |
| `nofail` | Don't block boot if the volume fails to mount |
| `0` (dump field) | Never dump this filesystem |
| `2` (fsck field) | Check filesystem after root filesystem during boot |

---

### Section 7.1 — Kernel Tuning

```bash
cat <<'EOF_SYSCTL' > /etc/sysctl.d/99-kind.conf
fs.inotify.max_user_watches = 524288
fs.inotify.max_user_instances = 8192
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF_SYSCTL
sysctl --system >/dev/null 2>&1 || true
```

| Parameter | Default | Set To | Reason |
|:---|:---|:---|:---|
| `fs.inotify.max_user_watches` | 8,192 | 524,288 | KinD controllers set up thousands of filesystem watchers. Without this, controllers crash with `too many open files`. |
| `fs.inotify.max_user_instances` | 128 | 8,192 | Each container process using inotify creates an instance. Multi-cluster KinD easily exceeds the default. |
| `net.ipv4.ip_forward` | 0 | 1 | **Critical for WireGuard and Kubernetes networking.** Without this, the host kernel drops packets that need forwarding between the `wg0` tunnel and Docker bridge. |
| `net.ipv6.conf.all.forwarding` | 0 | 1 | Same as above for IPv6. KinD's internal CNI (kindnet) uses dual-stack. |

`sysctl --system` applies all drop-in configs live, without requiring a reboot.

---

### Section 7.2 — Toolchain Installation

```bash
dnf install -y docker wireguard-tools iptables iproute jq || true
```

| Package | Purpose |
|:---|:---|
| `docker` | Container runtime — runs the KinD control-plane container |
| `wireguard-tools` | Provides `wg` and `wg-quick` — manage WireGuard interface |
| `iptables` | Firewall/NAT rules — Docker and WireGuard both create iptables rules for routing |
| `iproute` | Provides `ip` command — used to check `ip link show wg0`, manipulate routing tables |
| `jq` | JSON processor — used by helper scripts to parse AWS CLI JSON output |

The `|| true` prevents package install failures from aborting the script (e.g., when packages are already installed).

---

### Section 7.3 — KVM Permissions

```bash
[ -e /dev/kvm ] && chmod 666 /dev/kvm 2>/dev/null || true
```

`/dev/kvm` is the Linux Kernel Virtual Machine device node, present on EC2 instances with **nested virtualization enabled** in the Launch Template (`cpu_options.nested_virtualization = "enabled"`).

**Why `666`?**
Kata Containers' Firecracker hypervisor (`kata-fc`) must open `/dev/kvm` to create microVM environments. By default, `/dev/kvm` is owned by root and the `kvm` group. The KinD container runs as root inside the container, but Docker isolation means the device must be world-accessible (`chmod 666`) on the **host** to pass through to container workloads.

Only applies if `/dev/kvm` exists — this is a no-op on hub instances without nested virtualization enabled.

---

### Section 7.4 — Docker Data-Root Persistence

```bash
cat <<EOF_DOCKER > /etc/docker/daemon.json
{
  "data-root": "$MOUNT_POINT/docker",
  "storage-driver": "overlay2",
  "log-driver": "json-file",
  "log-opts": { "max-size": "100m", "max-file": "3" }
}
EOF_DOCKER
systemctl enable --now docker
```

**This is the most critical section for state retention.**

Docker's `data-root` is where everything is stored: image layers, container filesystem overlays, volumes, and network configurations. Default is `/var/lib/docker` on the **ephemeral root disk** — which is destroyed when the instance terminates.

By setting `data-root` to `/data/docker` (persistent EBS), **all of the following survive a Spot replacement:**

- The `${cluster_name}-control-plane` KinD container
- The full Kubernetes installation inside the container
- The **etcd database** (stores all K8s resources — pods, deployments, secrets, CRDs)
- All pulled container images (no re-download on restart)
- The Kata Containers devmapper thin-pool disk image

**User permissions:**

```bash
usermod -aG docker ec2-user 2>/dev/null || true
id -u ssm-user >/dev/null 2>&1 && usermod -aG docker ssm-user 2>/dev/null || true
chmod 666 /var/run/docker.sock 2>/dev/null || true
```

- `ec2-user` and `ssm-user` are added to the `docker` group for socket access without `sudo`.
- `chmod 666 /var/run/docker.sock` makes the Docker socket world-accessible immediately — needed because the SSM agent creates `ssm-user` dynamically, so group membership may not apply until the next session.

---

### Section 7.5 — Shared PKI Certificate Restoration

```bash
mkdir -p "$MOUNT_POINT/pki"
if [ -d "$MOUNT_POINT/pki" ] && [ -f "$MOUNT_POINT/pki/ca.crt" ]; then
  rm -rf /tmp/pki && mkdir -p /tmp/pki
  cp -a "$MOUNT_POINT/pki"/* /tmp/pki/ 2>/dev/null || true
  chmod 600 /tmp/pki/ca.key /tmp/pki/sa.key 2>/dev/null || true
  chmod 644 /tmp/pki/ca.crt /tmp/pki/sa.pub 2>/dev/null || true
fi
```

**Background:**

All hub and spoke clusters share the same Kubernetes Certificate Authority (CA). This means a cert issued in one cluster is valid when presented to another — essential for OCM federation and cross-cluster API communication.

During initial deployment, shared PKI files are generated on `primaryhub` and distributed to all instances via SSM. They are injected into KinD as Docker bind-mounts:
`/tmp/pki/ca.crt` → `/etc/kubernetes/pki/ca.crt` (inside the container).

**The problem on Spot replacement:**
`/tmp` is on the ephemeral root disk — destroyed with the old instance. When a new instance boots, `/tmp/pki` is empty. If the KinD container tries to start with bind-mounts pointing to non-existent paths, it fails with:

```
runc: error mounting /tmp/pki/sa.pub: not a directory
```

**The solution:**
PKI files are persisted to `/data/pki` on EBS. On every boot, this section copies them back to `/tmp/pki` with correct permissions before Docker starts the container.

| File | Permission | Purpose |
|:---|:---|:---|
| `ca.crt` | `644` | Root CA certificate — shared identity for all clusters |
| `ca.key` | `600` | Root CA private key — signs new TLS certificates |
| `sa.key` | `600` | Service Account signing key — signs JWT tokens for pods |
| `sa.pub` | `644` | Service Account public key — verifies pod JWT tokens |

---

### Section 7.6 — WireGuard Overlay Persistence

```bash
mkdir -p "$MOUNT_POINT/wireguard"
if [ ! -L /etc/wireguard ]; then
  if [ -d /etc/wireguard ]; then
    cp -rn /etc/wireguard/* "$MOUNT_POINT/wireguard/" 2>/dev/null || true
    rm -rf /etc/wireguard
  fi
  ln -sf "$MOUNT_POINT/wireguard" /etc/wireguard
fi
```

**WireGuard `wg0.conf` contains:**
- This instance's private key (permanent — must not change)
- This instance's public key
- Peer entries for all 3 other clusters, each with their public key and `Endpoint` (VPC private IP + port 51820)
- `AllowedIPs` entries routing overlay traffic (e.g. `10.99.0.1/32` for primaryhub)

**Why persist on EBS?**

If the WireGuard private key is on the root disk (lost on Spot termination), the replacement instance would generate a new key. All other peers still have the old public key configured — authentication fails and the mesh goes down.

By symlinking `/etc/wireguard` → `/data/wireguard`, the configuration and private keys survive on EBS. The replacement instance reuses the same cryptographic identity.

**Symlink logic:**
1. Creates `/data/wireguard` on EBS.
2. If `/etc/wireguard` is already a symlink → nothing to do.
3. If `/etc/wireguard` is a real directory (first boot after installing `wireguard-tools`) → copies contents to EBS first, then replaces with symlink.

---

### Section 7.7 — Dynamic WireGuard Peer Sync Daemon

This is the most complex section. It installs a background daemon that detects when a peer cluster's Spot instance gets a new private IP and updates the WireGuard mesh live.

#### The Problem

When a Spot instance is replaced in (e.g.) `eu-west-1`, the new EC2 instance gets a **new random VPC private IP**. The WireGuard `Endpoint` entries on the other 3 instances still point to the old IP — the tunnel goes down and cross-cluster communication fails.

#### The Sync Script (`/usr/local/bin/sync-wireguard-peers.sh`)

```bash
declare -A REGIONS=(
  ["primaryhub"]="us-east-1"
  ["secondaryhub"]="eu-west-1"
  ["spoke1"]="eu-central-1"
  ["spoke2"]="ap-south-1"
)
declare -A PEER_IPS=(["primaryhub"]="10.99.0.1" ...)

for cluster in primaryhub secondaryhub spoke1 spoke2; do
  # 1. Find this cluster's pubkey in wg0.conf by its overlay IP
  pubkey=$(grep -B2 -A5 "$peer_wg_ip" "$CONF" | awk '/PublicKey/ {print $3}')

  # 2. Query AWS EC2 API for current private IP of running instance
  current_ip=$(aws ec2 describe-instances \
    --region "$region" \
    --filters "Name=tag:ClusterName,Values=$cluster" "Name=instance-state-name,Values=running" \
    --query "Reservations[0].Instances[0].PrivateIpAddress")

  # 3. Compare to configured endpoint in wg0.conf
  configured_ip=$(grep -A5 "$pubkey" "$CONF" | awk -F'[: ]+' '/Endpoint/ {print $3}')

  # 4. If different → update file AND live kernel peer table (no restart needed)
  if [ "$configured_ip" != "$current_ip" ]; then
    sed -i -E "...Endpoint = $current_ip:51820..." "$CONF"
    wg set wg0 peer "$pubkey" endpoint "$current_ip:51820" persistent-keepalive 25
  fi
done
```

**Step-by-step flow:**

1. **Lookup by overlay IP**: Uses `grep` to find the `[Peer]` block in `wg0.conf` matching the cluster's stable WireGuard IP (e.g. `10.99.0.2/32` for secondaryhub). Extracts the `PublicKey`.

2. **Query AWS EC2 API**: Calls `aws ec2 describe-instances` filtered by `tag:ClusterName=<cluster>` and `instance-state-name=running`. Gets the current `PrivateIpAddress`.

3. **Compare**: If the IP in the file matches AWS, nothing to do.

4. **Live update (no restart needed)**: If different:
   - Updates `wg0.conf` on disk (persists across reboots).
   - Calls `wg set wg0 peer <pubkey> endpoint <new_ip>:51820` to update the **live kernel WireGuard state** immediately — no `systemctl restart wg-quick@wg0`, no tunnel downtime.

#### The Systemd Timer

```ini
[Timer]
OnBootSec=10s         # Run 10 seconds after boot
OnUnitActiveSec=20s   # Then run every 20 seconds continuously
AccuracySec=1s        # Fire within 1 second of scheduled time (low jitter)
```

The script runs as a one-shot systemd service triggered by a timer every **20 seconds**. This means:

- Within 20 seconds of any Spot replacement anywhere in the fleet, all other instances update their WireGuard peer endpoints.
- The `10.99.0.0/24` overlay mesh **self-heals automatically** — no human intervention required.

> **IAM requirement:** This relies on `ec2:DescribeInstances` permission (with `Resource: "*"` for cross-region instances), already configured in the `ebs_lifecycle` IAM policy in `main.tf` across all compute stacks.

---

### Section 7.8 — KinD & kubectl Binary Bootstrap

```bash
if ! command -v kind >/dev/null 2>&1; then
  curl -Lo /usr/local/bin/kind https://kind.sigs.k8s.io/dl/v0.27.0/kind-linux-amd64
  chmod +x /usr/local/bin/kind
fi

if ! command -v kubectl >/dev/null 2>&1 \
   || file $(which kubectl) | grep -q "HTML|XML|text"; then
  # Try extracting from KinD container first (fastest, version-matched)
  docker cp "${cluster_name}-control-plane:/usr/bin/kubectl" /usr/local/bin/kubectl \
  || docker cp "${cluster_name}-control-plane:/usr/local/bin/kubectl" /usr/local/bin/kubectl \
  || curl -Lo /usr/local/bin/kubectl https://dl.k8s.io/release/v1.32.2/bin/linux/amd64/kubectl
  chmod +x /usr/local/bin/kubectl
fi
```

**KinD:** Downloaded from the official release at version `v0.27.0` if not present. Stateless binary — it manages KinD clusters by orchestrating Docker containers.

**kubectl — the "corrupt binary" problem:**
On instances where an earlier cloud-init run had a networking failure, `curl` may have written an HTML error page to `/usr/local/bin/kubectl`. The `file $(which kubectl) | grep -q "HTML|XML|text"` check detects this and triggers a re-download.

**Download priority:**
1. **`docker cp` from KinD container** — fastest, no internet needed, exactly matches the cluster's Kubernetes version (`v1.32.2`)
2. **Alternate `docker cp` path** — in case the binary is in a different location inside the container
3. **Download from Kubernetes release server** — internet fallback if the container isn't available yet

---

### Section 7.9 — KinD Cluster Auto-Resume & Role Self-Healing

#### Container Detection & Start

```bash
CLUSTER_CONTAINER="${cluster_name}-control-plane"
if docker ps -a --format '{{.Names}}' | grep -q "^$CLUSTER_CONTAINER$"; then
  docker update --restart=always "$CLUSTER_CONTAINER" 2>/dev/null || true
  docker start "$CLUSTER_CONTAINER" || true
```

`docker ps -a` lists all containers including stopped ones. If the KinD control-plane container exists in `/data/docker` (because it was persisted on EBS), the script starts it.

`docker update --restart=always` configures the container to auto-restart if Docker itself restarts in the future.

#### Local Hostname Resolution

```bash
if ! grep -q "${cluster_name}-control-plane" /etc/hosts; then
  echo "127.0.0.1 ${cluster_name}-control-plane" >> /etc/hosts
fi
```

The Kubernetes API server TLS certificate is issued for the hostname `${cluster_name}-control-plane` (e.g. `primaryhub-control-plane`). This hostname only exists inside Docker's internal network. Adding it to `/etc/hosts` as `127.0.0.1` allows host processes (SSM users running `kubectl`, monitoring scripts, etc.) to reach the API via Docker's port mapping (`0.0.0.0:6443->6443/tcp`).

#### API Server Readiness Wait

```bash
for attempt in $(seq 1 45); do
  if docker exec "$CLUSTER_CONTAINER" kubectl --kubeconfig=/etc/kubernetes/admin.conf get nodes; then
    echo "KinD cluster is ready."; break
  fi
  sleep 2
done
```

After `docker start`, the container needs ~10-30 seconds to start all Kubernetes components:
1. `containerd` (container runtime inside KinD)
2. `kubelet`
3. `etcd`
4. `kube-apiserver`
5. `kube-controller-manager`
6. `kube-scheduler`

The script polls up to 45 × 2s = **90 seconds maximum**. Running `kubectl get nodes` inside the container is the definitive API-server readiness check.

#### Kubeconfig Export & Rewrite

```bash
docker exec "$CLUSTER_CONTAINER" cat /etc/kubernetes/admin.conf > /etc/kubernetes/admin.conf

sed -i "s|https://${cluster_name}-control-plane:6443|https://127.0.0.1:6443|g" \
    /etc/kubernetes/admin.conf

cp -f /etc/kubernetes/admin.conf /root/.kube/config
cp -f /etc/kubernetes/admin.conf /home/ec2-user/.kube/config
cp -f /etc/kubernetes/admin.conf /home/ssm-user/.kube/config
```

Extracts the admin kubeconfig from inside the container and rewrites the API server URL from `https://primaryhub-control-plane:6443` to `https://127.0.0.1:6443`. This is required because:

- The hostname `primaryhub-control-plane` only resolves within Docker's internal network.
- From the host, requests go to `127.0.0.1:6443`, forwarded by Docker's port publishing to port 6443 inside the container.

The kubeconfig is distributed to all user home directories. An `/etc/profile.d/kubeconfig.sh` entry exports `KUBECONFIG=/etc/kubernetes/admin.conf` for every shell session.

#### Role-Specific Recovery

```bash
case "${cluster_name}" in
  spoke1|spoke2)  ...
  primaryhub)     ...
  secondaryhub)   ...
esac
```

After generic cluster startup, role-specific recovery actions run. See [Section 6](#6-role-specific-behaviour) for details.

---

### Section 8 — Optional SSH Password Auth

```bash
%{ if enable_password_auth ~}
PW=$(aws secretsmanager get-secret-value \
  --secret-id "${secret_id}" \
  --region "${region}" \
  --query SecretString --output text)
echo "${ssh_username}:$PW" | chpasswd
printf 'PasswordAuthentication yes\nKbdInteractiveAuthentication no\n' \
  > /etc/ssh/sshd_config.d/60-password-auth.conf
systemctl restart sshd
unset PW
%{ endif ~}
```

This block is only rendered if `var.enable_password_auth = true` (Terraform controls this). It:

1. Fetches the SSH password from **AWS Secrets Manager** (never hardcoded in the template).
2. Sets the password for `${ssh_username}` using `chpasswd`.
3. Creates a drop-in `sshd_config.d` file enabling password auth and disabling keyboard-interactive auth (to prevent prompts from interfering with automation).
4. Restarts `sshd` to apply.
5. Immediately `unset PW` to remove the password from the environment.

---

## 5. Persistent Storage Layout on EBS

Everything critical is stored under the EBS mount point (`/data`):

```
/data/                              ← EBS volume (XFS, persists Spot replacements)
├── docker/                         ← Docker data-root (ALL cluster state)
│   ├── overlay2/                   ← Container filesystem layers (images, writable layers)
│   ├── containers/<id>/            ← Runtime state for the KinD control-plane container
│   │   └── (etcd, API server, all Kubernetes data lives here)
│   ├── volumes/                    ← Docker named volumes (PVCs backed by hostPath)
│   └── containerd/                 ← Containerd snapshotter state, devmapper thin-pool
│
├── wireguard/                      ← WireGuard keys and config (← /etc/wireguard symlink)
│   └── wg0.conf                    ← Private key, public key, all peer configurations
│
└── pki/                            ← Shared Kubernetes CA certificates
    ├── ca.crt                      ← Root Certificate Authority (shared across all clusters)
    ├── ca.key                      ← Root CA private key
    ├── sa.key                      ← Service Account signing key
    └── sa.pub                      ← Service Account public key
```

---

## 6. Role-Specific Behaviour

### `primaryhub` (`us-east-1`) — Active OCM Hub

```bash
primaryhub)
  if docker ps -a | grep -q "^envoy-gateway$"; then
    docker update --restart=always envoy-gateway
    docker start envoy-gateway
  fi
  ;;
```

- Resumes the **Envoy Gateway** Docker container (the traffic ingress proxy on WireGuard VIP `10.99.0.100`). Its state is persisted in `/data/docker` and comes back automatically.
- The **OCM Hub controllers** and `cluster-manager` pods inside KinD resume automatically via kubelet reading etcd state.
- No extra intervention needed — etcd has the full cluster state.

### `secondaryhub` (`eu-west-1`) — Standby OCM Hub

```bash
secondaryhub)
  echo "SecondaryHub: standing by for multi-hub election failover..."
  ;;
```

- No special actions beyond generic cluster startup.
- The `ocm-failover-controller` pod resumes automatically via kubelet.
- If `primaryhub` becomes unreachable, the failover controller promotes `secondaryhub` to active hub automatically. The klusterlet agents on spokes have both hub bootstrap kubeconfigs (`LocalSecrets` mode) and will switch within 60 seconds.

### `spoke1` (`eu-central-1`) and `spoke2` (`ap-south-1`) — Workload Clusters

```bash
spoke1|spoke2)
  # 1. Restore KVM permissions for Kata Firecracker
  [ -e /dev/kvm ] && chmod 666 /dev/kvm

  # 2. Restart Kata Firecracker DaemonSet (re-initializes devmapper thin-pool)
  docker exec "$CLUSTER_CONTAINER" kubectl -n kube-system \
    rollout restart daemonset/kata-fc-node-reconciler

  # 3. Restart klusterlet agents (clear stale leases, re-register with hub)
  docker exec "$CLUSTER_CONTAINER" kubectl -n open-cluster-management-agent \
    rollout restart deployment/klusterlet-registration-agent
  docker exec "$CLUSTER_CONTAINER" kubectl -n open-cluster-management-agent \
    rollout restart deployment/klusterlet-work-agent
  ;;
```

**`kata-fc-node-reconciler` restart:**
The Kata Firecracker DaemonSet provisions:
- A Linux LVM thin-pool for devmapper container snapshots (required by Kata's containerd shim)
- Updated `containerd` configuration for the `kata-fc` RuntimeClass
- Binary symlinks for `firecracker`, `kata-runtime`, and `containerd-shim-kata-v2`

On instance replacement, the devmapper loop devices and LVM thin pools need to be re-initialized on the new host kernel. Restarting the DaemonSet pod triggers this reconciliation immediately.

**Klusterlet agents restart:**
The klusterlet is the OCM agent that registers the spoke with the active hub. After Spot replacement:
- Stale leader-election leases from the previous instance may block the new agent.
- A clean restart clears leases, re-reads `LocalSecrets` bootstrap credentials (for both `primaryhub` and `secondaryhub`), and re-registers with whichever hub is currently active.
- Full re-registration typically completes within **60 seconds**.

---

## 7. State That Survives a Spot Replacement

| Component | Stored In | Survives? |
|:---|:---|:---|
| All Kubernetes resources (pods, deployments, secrets, CRDs) | `/data/docker` (etcd on EBS) | ✅ Yes |
| Container images (kindest/node, application images) | `/data/docker/overlay2` | ✅ Yes |
| WireGuard mesh private key and peer config | `/data/wireguard/wg0.conf` | ✅ Yes |
| Shared PKI (CA cert, CA key, SA keys) | `/data/pki/` | ✅ Yes |
| Kata Containers devmapper thin-pool disk image | `/data/docker/containerd/...` | ✅ Yes |
| OCM klusterlet bootstrap secrets | KinD cluster etcd | ✅ Yes |
| PostgreSQL primary data (on primaryhub) | KinD cluster PVC backed by `/data` | ✅ Yes |
| PostgreSQL WAL streaming replication state | KinD cluster etcd + pod state | ✅ Yes |
| Valkey/Redis data (on primaryhub) | KinD cluster PVC backed by `/data` | ✅ Yes |
| OCM ManagedCluster registrations | KinD cluster etcd | ✅ Yes |

---

## 8. State That Does NOT Survive (Must Re-run)

| Component | Why Lost | Recovery |
|:---|:---|:---|
| SSH tunnels from dev machine | Exist only on the laptop | Reconnect SSH / SSM session |
| SSM Session Manager sessions | Tied to old instance ID | Start new SSM session via AWS Console |
| `/tmp/pki` (PKI scratch files) | `/tmp` on ephemeral root disk | Section 7.5 restores from `/data/pki` automatically |
| `ssm-user` docker group | User recreated on new OS | `chmod 666 /var/run/docker.sock` in script compensates |
| `prod.env` instance IDs | Contain old instance ID | Update `prod.env` manually after replacement with new IDs |
| WireGuard peer endpoint IPs | Old VPC IPs are stale | Section 7.7 (WireGuard sync daemon) corrects within 20s |

---

## 9. Boot Sequence Timeline

The following shows the approximate order and duration when a replacement Spot instance boots:

```
T+0s    EC2 instance launches, cloud-init starts user_data.sh
T+2s    IMDSv2 token fetched, instance metadata collected
T+5s    EBS volume polling begins (waiting for old instance to release)
T+30s   EBS detached from old instance, attached to new (or already available)
T+35s   NVMe block device resolved via serial matching
T+37s   XFS filesystem detected on block device — no format, data preserved
T+38s   /data mounted, UUID registered in /etc/fstab
T+40s   Kernel tuning applied (sysctl)
T+45s   dnf packages verified / installed (~5s if cached)
T+50s   Docker daemon.json written (data-root=/data/docker), dockerd started
T+52s   PKI files restored from /data/pki → /tmp/pki
T+53s   WireGuard symlink verified (/etc/wireguard → /data/wireguard)
T+55s   sync-wireguard-peers.sh + systemd timer installed
T+57s   wg-quick@wg0 started, first peer sync runs immediately
T+60s   Peer IPs queried from AWS EC2 API, stale endpoints corrected live
T+62s   kind and kubectl binaries verified / extracted from container
T+65s   docker start ${cluster_name}-control-plane (KinD container starts)
T+90s   API server ready (kubelet restores all pods from etcd)
T+92s   Kubeconfig exported, rewritten to 127.0.0.1:6443, distributed to users
T+95s   [Spokes] kata-fc-node-reconciler DaemonSet rollout restart triggered
T+97s   [Spokes] klusterlet registration + work agents rollout restart triggered
T+100s  [PrimaryHub] Envoy gateway container resumed
T+105s  "Bootstrap Complete at $(date)" logged to /var/log/user-data.log

~T+160s [Spokes] Klusterlet re-registers with active hub (60s election timeout)
~T+180s Managed cluster shows AVAILABLE=True on primaryhub hub cluster
```

> **Debug tip:** All activity is logged to `/var/log/user-data.log` on the instance.
> Access via: SSM Session Manager → `sudo tail -f /var/log/user-data.log`
> Or via AWS Console → EC2 → Instance → Actions → Monitor → Get System Log.
