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

> [!NOTE]
> **Understanding the `0 extents` Error (Filesystem Free Space vs LVM Unallocated Space):**
> On default Ubuntu LVM installations, the installer creates a Volume Group (`ubuntu-vg`) and assigns **100% of its size** to a single Logical Volume (`ubuntu-lv`) mounted at root (`/`).
> - `df -h` shows **Filesystem Free Space** (unused space for files *inside* `/`).
> - `sudo vgs` shows **LVM Unallocated Space** (`VFree`), which is 0 because `ubuntu-lv` claimed the entire Volume Group.
>
> When running `sudo lvcreate --size 15G --thinpool containerd-pool ubuntu-vg`, LVM checks for *unallocated space* in `ubuntu-vg`. Because 100% was assigned to `ubuntu-lv`, LVM fails with:
> ```text
> Volume group "ubuntu-vg" has insufficient free space (0 extents): 4 required.
> ```
> Since mounted `ext4` partitions cannot be shrunk online, creating a file-backed loop device inside `/var/lib/` adds **new unallocated physical volume space** into `ubuntu-vg` safely without affecting running applications or requiring VM configuration changes.

1. **Create a 15GB backing image file**:
   ```bash
   sudo fallocate -l 15G /var/lib/containerd-pool-disk.img
   ```

2. **Attach the file as a single loop device (reusing existing loop if already attached)**:
   ```bash
   LOOP_DEV=$(sudo losetup -j /var/lib/containerd-pool-disk.img | cut -d: -f1)
   if [ -z "$LOOP_DEV" ]; then
     LOOP_DEV=$(sudo losetup -fP --show /var/lib/containerd-pool-disk.img)
   fi
   ```

3. **Force-initialize the Physical Volume and extend `ubuntu-vg`**:
   ```bash
   sudo pvcreate -ff -y $LOOP_DEV
   sudo vgextend ubuntu-vg $LOOP_DEV
   ```

4. **Create the Thin-Pool (`containerd-pool`)**:
   ```bash
   sudo lvcreate --size 14.5G --thinpool containerd-pool ubuntu-vg
   ```

5. **Persist the loopback device on boot**:
   Create a systemd service at `/etc/systemd/system/containerd-loopback.service`:
   ```ini
   [Unit]
   Description=Setup loopback device for containerd devmapper thinpool
   DefaultDependencies=no
   After=systemd-modules-load.service
   Before=rke2-server.service rke2-agent.service containerd.service

   [Service]
   Type=oneshot
   RemainAfterExit=yes
   ExecStart=/bin/sh -c 'if ! losetup -a | grep -q "/var/lib/containerd-pool-disk.img"; then \
     LOOP_DEV=$(losetup -fP --show /var/lib/containerd-pool-disk.img); \
     pvscan; \
     vgchange -ay ubuntu-vg; \
   fi'

   [Install]
   WantedBy=multi-user.target
   ```
   Enable the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable containerd-loopback.service
   ```

---

### Option C: Non-LVM Host (Plain `ext4`/`xfs` Partition Layout)
If your host partition layout is standard `ext4`/`xfs` on a plain partition (no Volume Group or LVM initialized):

1. **Create a backing file** (e.g. 20GB sparse file) to serve as physical storage:
   ```bash
   sudo truncate -s 20G /var/lib/containerd-loopback.img
   ```

2. **Associate a loopback device** with the file:
   ```bash
   LOOP_DEV=$(sudo losetup -fP --show /var/lib/containerd-loopback.img)
   ```

3. **Initialize the Physical Volume and create the Volume Group (`ubuntu-vg`)**:
   ```bash
   sudo pvcreate $LOOP_DEV
   sudo vgcreate ubuntu-vg $LOOP_DEV
   ```

4. **Create the Thin-Pool (`containerd-pool`)**:
   ```bash
   sudo lvcreate --size 15G --thinpool containerd-pool ubuntu-vg
   ```

5. **Persist the loopback device on boot**:
   Create a systemd service at `/etc/systemd/system/containerd-loopback.service`:
   ```ini
   [Unit]
   Description=Setup loopback device for containerd devmapper thinpool
   DefaultDependencies=no
   After=systemd-modules-load.service
   Before=rke2-server.service rke2-agent.service containerd.service

   [Service]
   Type=oneshot
   RemainAfterExit=yes
   ExecStart=/bin/sh -c 'if ! losetup -a | grep -q "/var/lib/containerd-loopback.img"; then \
     LOOP_DEV=$(losetup -fP --show /var/lib/containerd-loopback.img); \
     pvscan; \
     vgchange -ay ubuntu-vg; \
   fi'

   [Install]
   WantedBy=multi-user.target
   ```
   Enable the service:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable containerd-loopback.service
   ```

---

### Step 6.3 Verify Thin-Pool Device
Verify that the thin pool device was created and mapper link exists:
```bash
ls -la /dev/mapper/
# You should see: ubuntu--vg-containerd--pool
```
The device mapper name for our pool is `ubuntu--vg-containerd--pool`.

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

> **Important:** RKE2 reads `config.toml.tmpl` — **not** `config-v3.toml.tmpl`. Using the wrong filename means changes are silently ignored and the auto-generated `config.toml` retains its old values.

Create the template directory (if not exists):
```bash
sudo mkdir -p /var/lib/rancher/rke2/agent/etc/containerd
```

Create/edit the config template file:
```bash
sudo nano /var/lib/rancher/rke2/agent/etc/containerd/config.toml.tmpl
```

Paste the following configuration. The `{{ template "base" . }}` directive tells RKE2 to inject its own generated base config, and we only append the additional stanzas for `kata-fc` and `devmapper` below it:
```toml
{{ template "base" . }}

# Configure kata-fc runtime to use devmapper snapshotter
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-fc]
  runtime_type = "io.containerd.kata.v2"
  snapshotter = "devmapper"

[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.kata-fc.options]
  ConfigPath = "/etc/kata-containers/configuration.toml"

# Configure devmapper snapshotter plugin
[plugins."io.containerd.snapshotter.v1.devmapper"]
  root_path = "/var/lib/rancher/rke2/agent/containerd/io.containerd.snapshotter.v1.devmapper"
  pool_name = "ubuntu--vg-containerd--pool"
  base_image_size = "2GB"
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

## 11. Verification Steps

### Step 11.1 Verify containerd plugins
Ensure that the `devmapper` snapshotter plugin is loaded successfully and reports an `ok` status:
```bash
sudo /var/lib/rancher/rke2/bin/ctr --address /run/k3s/containerd/containerd.sock plugins ls | grep devmapper
```
**Expected Output:**
```text
io.containerd.snapshotter.v1              devmapper                linux/amd64    ok
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
