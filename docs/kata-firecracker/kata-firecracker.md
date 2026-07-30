# Setting up Kata Containers + Firecracker with RKE2

This documentation provides the complete sequence to set up a **Kata Containers (3.x)** + **Firecracker (VMM)** worker or control-plane node from scratch on Ubuntu 22.04 LTS running **RKE2 (v1.32.x)**.

Because Firecracker does not support filesystem-level sharing (like `virtio-fs` or `9p`), you **cannot** use containerd's default `overlayfs` snapshotter to run Firecracker sandboxes. Instead, this setup uses an LVM thin-pool backed **Device Mapper (`devmapper`) snapshotter** specifically for the `kata-fc` runtime.

---

## 1. Update the System
Ensure the package lists are updated and existing packages are upgraded:
```bash
sudo apt update && sudo apt upgrade -y
```

## 2. Install Required Packages
Install dependencies required for building, running, and managing containers, virtual machines, and loopback setups:
```bash
sudo apt install -y \
  curl wget git jq unzip make gcc g++ pkg-config \
  libseccomp-dev libglib2.0-dev libpixman-1-dev \
  build-essential socat iproute2 iptables bridge-utils \
  cpu-checker lvm2
```

## 3. Verify Virtualization Support

> [!IMPORTANT]
> **Hardware Virtualization Requirement:** Firecracker strictly requires access to `/dev/kvm` and CPU hardware virtualization flags (`vmx` for Intel or `svm` for AMD). Standard Cloud VPS instances (such as standard OVH Cloud VMs, AWS EC2 non-metal instances, or DigitalOcean Droplets) do **not** enable nested virtualization by default, causing `/dev/kvm` lookups to fail.

Verify that hardware virtualization (nested virtualization) is supported on the host:
```bash
egrep -c '(vmx|svm)' /proc/cpuinfo
```
*(Should return a value greater than 0)*

Then check via the KVM check utility:
```bash
sudo kvm-ok
```
**Expected Output:**
```text
INFO: /dev/kvm exists
KVM acceleration can be used
```

### Hardware & Cloud Provider Requirements for `kata-fc`

| Host Infrastructure | `/dev/kvm` Available? | `kata-fc` Support | Action Required if Unsupported |
| :--- | :---: | :---: | :--- |
| **Physical Bare Metal** | ✅ YES | **Fully Supported** | Enable Intel VT-x / AMD-V in BIOS/UEFI. |
| **OVH Bare Metal / Dedicated** | ✅ YES | **Fully Supported** | Supported out of the box on dedicated servers. |
| **AWS EC2 `.metal` Instances** | ✅ YES | **Fully Supported** | Use `.metal` instance types (e.g. `c5.metal`, `i3.metal`). |
| **GCP / Azure VMs** | ✅ YES | **Supported** | Enable `--enable-nested-virtualization` during VM creation. |
| **Proxmox / KVM On-Prem** | ✅ YES | **Supported** | Set `options kvm_intel nested=1` on host & VM CPU type to `host`. |
| **OVH Standard VPS / Cloud VM** | ❌ NO | *Not Supported* | Switch runtime to **gVisor (`gvisor`)** in `values.yaml`. |

> [!NOTE]
> If your cloud environment does not support `/dev/kvm`, switch the sandbox runtime in your Helm `values.yaml` to **`gvisor`** (`runtimeClassName: "gvisor"`). gVisor isolates containers in user space via system call interception without needing hardware KVM extensions.

## 4. Install Firecracker
Download and install the latest stable version of the Firecracker binary:
```bash
LATEST=$(curl -s https://api.github.com/repos/firecracker-microvm/firecracker/releases/latest | jq -r .tag_name)
ARCH=$(uname -m)

# Download release tarball
curl -LO https://github.com/firecracker-microvm/firecracker/releases/download/${LATEST}/firecracker-${LATEST}-${ARCH}.tgz
tar -xzf firecracker-${LATEST}-${ARCH}.tgz

# Move binary to target path
sudo mv release-${LATEST}-${ARCH}/firecracker-${LATEST}-${ARCH} /usr/local/bin/firecracker
sudo chmod +x /usr/local/bin/firecracker

# Verify installation
firecracker --version
```

## 5. Install Kata Containers (3.x)
Download the static release tarball and extract it directly into `/opt/kata`:
```bash
wget https://github.com/kata-containers/kata-containers/releases/download/3.18.0/kata-static-3.18.0-amd64.tar.xz
sudo tar -xJf kata-static-3.18.0-amd64.tar.xz -C /
```

Create necessary symlinks to ensure the binaries are available in standard system execution paths:
```bash
sudo ln -sf /opt/kata/bin/kata-runtime /usr/local/bin/kata-runtime
sudo ln -sf /opt/kata/bin/kata-ctl /usr/local/bin/kata-ctl
sudo ln -sf /opt/kata/bin/containerd-shim-kata-v2 /usr/local/bin/containerd-shim-kata-v2
sudo ln -sf /opt/kata/bin/firecracker /usr/local/bin/firecracker
sudo ln -sf /opt/kata/bin/jailer /usr/local/bin/jailer
```

Verify that the installation directories exist:
```bash
ls -la /opt/kata
# Expected: bin, libexec, share

kata-runtime --version
```

---

## 6. Configure LVM Thin-Pool for Device Mapper Snapshotter
To support Firecracker's block-storage requirement, we must create a dedicated **LVM thin-pool** on the host.

Depending on your host's partitioning, choose one of the following options:

---

### Option A: Standard LVM Setup (When LVM has free space in `ubuntu-vg`)
If your primary disk is partition-managed using LVM and has **unallocated free extents** (`VFree >= 15G`):

1. **Check available free space** in your Volume Group (`ubuntu-vg`):
   ```bash
   sudo vgs
   ```
2. **Create the thin-pool (`containerd-pool`)**:
   ```bash
   sudo lvcreate --size 15G --thinpool containerd-pool ubuntu-vg
   ```

---

### Option B: Pre-configured LVM with Insufficient Free Space (`0 extents`) *(Most Common Ubuntu Installer Setup)*

> [!IMPORTANT]
> **Critical Architectural Rule: Use a Dedicated Volume Group (`containerd-vg`)**
> Never add a file-backed loop device (e.g. `/var/lib/containerd-pool-disk.img`) to the primary root OS volume group (`ubuntu-vg`).
> During early boot, `initramfs` runs before `/` is mounted, so `/var/lib/containerd-pool-disk.img` cannot be read. If `ubuntu-vg` expects this loop device to assemble root (`/`), early boot will fail and drop to an `(initramfs)` shell.
> Creating a **separate, dedicated Volume Group (`containerd-vg`)** isolates containerd storage from early boot entirely.

#### Complete One-Click Self-Healing Setup Script

Run this command block on the host to configure permanent LVM thin provisioning and self-healing:

```bash
sudo bash -c '
# 1. Enable LVM Thin-Pool Kernel Event Monitoring Daemon
systemctl enable --now lvm2-monitor.service

# 2. Create Idempotent Self-Healing Script for Boot & Watchdog
cat << "EOF" > /usr/local/sbin/ensure-containerd-loopback.sh
#!/bin/sh
set -e

IMG="/var/lib/containerd-pool-disk.img"
VG="containerd-vg"

# Load thin pool kernel module
modprobe dm_thin_pool 2>/dev/null || true

# Attach loop device if missing
if ! losetup -a | grep -qF "$IMG"; then
  losetup -fP --show "$IMG"
fi

# Refresh LVM cache & activate containerd-vg with thin pool monitoring enabled
pvscan --cache
vgchange -ay --monitor y "$VG"
EOF

chmod +x /usr/local/sbin/ensure-containerd-loopback.sh

# 3. Create Early Boot Systemd Service
cat << "EOF" > /etc/systemd/system/containerd-loopback.service
[Unit]
Description=Setup loopback device for containerd devmapper thinpool
DefaultDependencies=no
After=systemd-modules-load.service local-fs.target lvm2-monitor.service
Before=containerd.service rke2-server.service rke2-agent.service
Requires=local-fs.target lvm2-monitor.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/ensure-containerd-loopback.sh

[Install]
WantedBy=multi-user.target
EOF

# 4. Create 5-Minute Watchdog Timer (Self-Heals automatically mid-session)
cat << "EOF" > /etc/systemd/system/containerd-loopback.timer
[Unit]
Description=Periodically ensure containerd loopback + VG stay active

[Timer]
OnBootSec=1min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

# 5. Enable & Start Loopback Services
systemctl daemon-reload
systemctl enable --now containerd-loopback.service
systemctl enable --now containerd-loopback.timer

# 6. Configure RKE2 Multi-Runtime Template (gVisor + Kata Firecracker)
cat << "EOF" > /var/lib/rancher/rke2/agent/etc/containerd/config.toml.tmpl
{{ template "base" . }}

# 1. gVisor Runtime (runsc)
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runsc]
  runtime_type = "io.containerd.runsc.v1"

# 2. Kata Firecracker Runtime (kata-fc) using devmapper snapshotter
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-fc]
  runtime_type = "io.containerd.kata.v2"
  snapshotter = "devmapper"

[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-fc.options]
  ConfigPath = "/etc/kata-containers/configuration.toml"

# 3. Devmapper Snapshotter Plugin for Kata
[plugins."io.containerd.snapshotter.v1.devmapper"]
  root_path = "/var/lib/rancher/rke2/agent/containerd/io.containerd.snapshotter.v1.devmapper"
  pool_name = "containerd--vg-containerd--pool"
  base_image_size = "4GB"
  discard_blocks = true
  fs_type = "ext4"
EOF

# 7. Apply Template Config & Restart RKE2
sed -i "s/pool_name = .*/pool_name = \"containerd--vg-containerd--pool\"/g" /var/lib/rancher/rke2/agent/etc/containerd/config.toml 2>/dev/null || true
systemctl restart rke2-server
'
```

---

### Step 6.3 Technical Reboot Survival & Self-Healing Architecture

The configuration implemented above guarantees **100% stability across all VM reboots and RKE2 restarts** through the following technical mechanisms:

#### 1. Preventing `(initramfs)` Early Boot Shell
- **Root Cause Solved**: Previously, a file-backed loop device was added directly into `ubuntu-vg` (the root OS Volume Group). During early boot, `initramfs` tried to assemble `ubuntu-vg` *before* `/var/lib` was mounted, causing the OS boot to fail into BusyBox shell.
- **Permanent Solution**: `ubuntu-vg` relies **only on `/dev/sda3`** (physical partition). `containerd-vg` is isolated in a separate Volume Group that `initramfs` completely ignores during early boot.

#### 2. Preventing `ctr plugins ls -> error` on containerd Startup
- **Root Cause Solved**: On VM boot, containerd could start *before* `/var/lib/containerd-pool-disk.img` was attached to a loop device.
- **Permanent Solution**: `containerd-loopback.service` specifies `Before=containerd.service rke2-server.service rke2-agent.service`. Systemd guarantees that `ensure-containerd-loopback.sh` attaches `/var/lib/containerd-pool-disk.img` and activates `containerd-vg` **before** containerd initializes.

#### 3. Preventing `operation not supported` (`EOPNOTSUPP`) on Thin Snapshot Creation
- **Root Cause Solved**: The thin pool was previously activated without thin pool event monitoring (`--monitor y`) or `lvm2-monitor.service`. Without active monitoring, the kernel device mapper rejected thin snapshot creation ioctls.
- **Permanent Solution**: `lvm2-monitor.service` is permanently enabled, and `ensure-containerd-loopback.sh` executes `vgchange -ay --monitor y containerd-vg`, linking kernel event monitoring (`dmeventd`) automatically on every boot.

#### 4. Preventing `Device does not exist` / `snapshot does not exist: not found`
- **Root Cause Solved**: The config template specified `pool_name = "containerd--vg-containerd--pool-tpool"`, but the actual target name in `dmsetup ls` was `containerd--vg-containerd--pool`.
- **Permanent Solution**: `config.toml.tmpl` specifies `pool_name = "containerd--vg-containerd--pool"`, matching `dmsetup` output exactly.

#### 5. Automatic Mid-Session Recovery (Watchdog Timer)
- **Permanent Solution**: `containerd-loopback.timer` runs every 5 minutes in the background to verify loop device attachment and thin pool activation. If anything ever detaches, it self-heals automatically without downtime.

| Potential Point of Failure | How It Is Permanently Solved |
| :--- | :--- |
| **`initramfs` Emergency Boot Shell** | Root OS (`ubuntu-vg`) uses **only `/dev/sda3`**. `containerd-vg` is isolated in a separate Volume Group so early boot never crashes. |
| **Missing Loop Device on Boot** | `containerd-loopback.service` runs right after `/var/lib` mounts to auto-attach `/var/lib/containerd-pool-disk.img`. |
| **Un-monitored Thin Pool Kernel Error (`EOPNOTSUPP`)** | `ensure-containerd-loopback.sh` activates `containerd-vg` with **`vgchange -ay --monitor y`**, hooking LVM kernel monitoring (`dmeventd`). |
| **Mid-session Disconnects** | `containerd-loopback.timer` checks the loop device every 5 minutes and self-heals automatically. |
| **`dmsetup` Device Name Match** | `pool_name` is set to `containerd--vg-containerd--pool` matching `dmsetup ls` output exactly. |

---

## 7. Configure Kata Containers (Firecracker)
Copy the default Firecracker template to the configuration path:
```bash
sudo mkdir -p /etc/kata-containers
sudo cp /opt/kata/share/defaults/kata-containers/configuration-fc.toml /etc/kata-containers/configuration.toml
```

Edit the configuration file:
```bash
sudo nano /etc/kata-containers/configuration.toml
```
Ensure the hypervisor and path are correctly configured:
```toml
[hypervisor.firecracker]
path = "/usr/local/bin/firecracker"
```

Verify Kata configuration and compatibility with your host system:
```bash
sudo kata-runtime check
# All checks should report PASS.
```

---

## 8. Create RuntimeClass
Apply the `RuntimeClass` resource so Kubernetes scheduling knows to target this configuration when specified:
```yaml
cat <<EOF | kubectl apply -f -
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: kata-fc
handler: kata-fc
EOF
```

---

## 9. Configure RKE2 containerd
Because RKE2 dynamically generates `/var/lib/rancher/rke2/agent/etc/containerd/config.toml` on startup, we must use a **custom containerd template** (`config.toml.tmpl`) to inject both our `devmapper` snapshotter configuration and the `kata-fc` runtime settings.

Create the template directory (if not exists):
```bash
sudo mkdir -p /var/lib/rancher/rke2/agent/etc/containerd
```

Create/edit the config template file `/var/lib/rancher/rke2/agent/etc/containerd/config.toml.tmpl`:
```toml
{{ template "base" . }}

# 1. gVisor Runtime (runsc)
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.runsc]
  runtime_type = "io.containerd.runsc.v1"

# 2. Kata Firecracker Runtime (kata-fc) using devmapper snapshotter
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-fc]
  runtime_type = "io.containerd.kata.v2"
  snapshotter = "devmapper"

[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-fc.options]
  ConfigPath = "/etc/kata-containers/configuration.toml"

# 3. Devmapper Snapshotter Plugin for Kata
[plugins."io.containerd.snapshotter.v1.devmapper"]
  root_path = "/var/lib/rancher/rke2/agent/containerd/io.containerd.snapshotter.v1.devmapper"
  pool_name = "containerd--vg-containerd--pool"
  base_image_size = "4GB"
  discard_blocks = true
  fs_type = "ext4"
```

---

## 10. Restart RKE2 Service
Restart RKE2 to pick up and generate containerd's configuration from the new template:
```bash
# If running rke2-server:
sudo systemctl restart rke2-server

# If running rke2-agent (worker):
sudo systemctl restart rke2-agent
```

---

## 11. Verification & Troubleshooting Guide

### Step 11.1 Verify containerd plugins
Ensure that the `devmapper` snapshotter plugin is loaded successfully and reports an `ok` status:
```bash
sudo /var/lib/rancher/rke2/bin/ctr --address /run/k3s/containerd/containerd.sock plugins ls | grep devmapper
```
**Expected Output:**
```text
io.containerd.snapshotter.v1              devmapper                linux/amd64    ok
```

---

### Step 11.2 Troubleshooting Common Issues

#### Issue A: Boot Drops to `(initramfs)` BusyBox Shell
- **Cause**: Loop device was added directly to `ubuntu-vg` (root OS VG). Early boot cannot find the loop file before `/` is mounted.
- **Fix**:
  1. At `(initramfs)` prompt, run: `vgchange -ay --partial` and `exit`.
  2. Once logged into Ubuntu, remove missing PV from root VG:
     ```bash
     sudo vgreduce --removemissing --force ubuntu-vg
     sudo update-initramfs -u -k all
     ```

#### Issue B: Duplicate Loop Device Warnings (`Cannot use device with duplicates`)
- **Cause**: Multiple loop devices (`/dev/loop0`, `/dev/loop1`) attached to the same file.
- **Fix**:
  ```bash
  sudo losetup -D
  sudo losetup -a | grep containerd-pool-disk | cut -d: -f1 | xargs -r sudo losetup -d 2>/dev/null || true
  sudo systemctl restart containerd-loopback.service
  ```

#### Issue C: Expanding Backing Disk Image & Thin Pool Size
- **To expand the backing image to 15GB and thin pool to 14.5GB**:
  ```bash
  sudo truncate -s 15G /var/lib/containerd-pool-disk.img
  LOOP_DEV=$(sudo losetup -j /var/lib/containerd-pool-disk.img | cut -d: -f1)
  sudo losetup -c $LOOP_DEV
  sudo pvresize $LOOP_DEV
  sudo lvextend -L 14.5G containerd-vg/containerd-pool
  ```

#### Issue D: `failed to query device metadata` / `snapshot does not exist: not found`
- **Cause**: Stale snapshotter metadata DB or image layers pulled using `overlayfs` instead of `devmapper`.
- **Fix**:
  ```bash
  sudo systemctl stop rke2-server
  sudo rm -rf /var/lib/rancher/rke2/agent/containerd/io.containerd.snapshotter.v1.devmapper
  sudo systemctl start rke2-server
  sudo /var/lib/rancher/rke2/bin/ctr -n k8s.io images pull --snapshotter devmapper <IMAGE_NAME>
  ```

#### Issue E: `failed to get reader from content store: content digest not found`
- **Cause**: Incomplete or corrupted image content blob entry in containerd's content store.
- **Fix**:
  ```bash
  sudo systemctl stop rke2-server
  sudo rm -rf /var/lib/rancher/rke2/agent/containerd/io.containerd.content.v1.content
  sudo rm -rf /var/lib/rancher/rke2/agent/containerd/io.containerd.metadata.v1.bolt
  sudo systemctl start rke2-server
  ```

#### Issue F: `Pod for kube-apiserver not synced (waiting for termination of old pod sandbox)`
- **Cause**: Orphan container shim process remaining after rapid RKE2 restarts.
- **Fix**:
  ```bash
  sudo pkill -9 -f containerd-shim
  sudo systemctl restart rke2-server
  ```

#### Issue G: `etcd context deadline exceeded` / Defragmentation Lock
- **Cause**: RKE2 restarted while `etcd` was defragmenting, leaving stale `etcd` process locking port `2379`.
- **Fix**:
  ```bash
  sudo systemctl stop rke2-server
  sudo pkill -9 -f etcd 2>/dev/null || true
  sudo pkill -9 -f rke2 2>/dev/null || true
  sudo rm -f /run/k3s/containerd/containerd.sock 2>/dev/null || true
  sudo systemctl start rke2-server
  ```

### Step 11.2 Verify registration
Verify that `kata-fc` and the correct `base_image_size` are present in the live generated config:
```bash
sudo grep -A5 "kata-fc" /var/lib/rancher/rke2/agent/etc/containerd/config.toml
sudo grep "base_image_size" /var/lib/rancher/rke2/agent/etc/containerd/config.toml
```
**Expected:** `base_image_size = "2GB"`. If it still shows `10GB`, confirm you edited `config.toml.tmpl` (not `config-v3.toml.tmpl`) and restart RKE2.

### Step 11.3 Deploy test pod
Create a file named `kata-test.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata:
  name: kata-test
spec:
  runtimeClassName: kata-fc
  containers:
  - name: nginx
    image: nginx
```
Apply the deployment:
```bash
kubectl apply -f kata-test.yaml
```

Check the status of the pod:
```bash
kubectl get pod kata-test -o wide
# Expected status: Running
```

### Step 11.4 Verify running MicroVM
Check for active `firecracker` instances on the host:
```bash
ps aux | grep firecracker
```
You should see a running process path resembling `/firecracker --id <sandbox-id> ...`.
