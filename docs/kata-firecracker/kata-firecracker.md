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

### Step 6.1 Check Volume Group Space
Check for available free space in your Volume Group (e.g. `ubuntu-vg`):
```bash
sudo vgs
```
Ensure you have sufficient free space (e.g., `15G` or more).

### Step 6.2 Create the Thin-Pool
Run the following command to create a thin-pool named `containerd-pool` inside your volume group (e.g., `ubuntu-vg`):
```bash
sudo lvcreate --size 15G --thinpool containerd-pool ubuntu-vg
```

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
Because RKE2 dynamically generates `/var/lib/rancher/rke2/agent/etc/containerd/config.toml` on startup, we must use a **custom containerd template** (`config-v3.toml.tmpl`) to inject both our `devmapper` snapshotter configuration and the `kata-fc` runtime settings.

Create the template directory (if not exists):
```bash
sudo mkdir -p /var/lib/rancher/rke2/agent/etc/containerd
```

Create/edit the config template file:
```bash
sudo nano /var/lib/rancher/rke2/agent/etc/containerd/config-v3.toml.tmpl
```

Paste the following full configuration. Note that we define the `devmapper` snapshotter plugin block at the bottom, and explicitly override the `snapshotter` for the `kata-fc` runtime:
```toml
# File generated by rke2. DO NOT EDIT. Use config.toml.tmpl instead.
version = 3
root = "/var/lib/rancher/rke2/agent/containerd"
state = "/run/k3s/containerd"

[grpc]
  address = "/run/k3s/containerd/containerd.sock"

[plugins.'io.containerd.internal.v1.opt']
  path = "/var/lib/rancher/rke2/agent/containerd"

[plugins.'io.containerd.grpc.v1.cri']
  stream_server_address = "127.0.0.1"
  stream_server_port = "10010"

[plugins.'io.containerd.cri.v1.runtime']
  enable_selinux = false
  enable_unprivileged_ports = true
  enable_unprivileged_icmp = true
  device_ownership_from_security_context = false

[plugins.'io.containerd.cri.v1.images']
  snapshotter = "overlayfs"
  disable_snapshot_annotations = true

[plugins.'io.containerd.cri.v1.images'.pinned_images]
  sandbox = "index.docker.io/rancher/mirrored-pause:3.6"

[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc]
  runtime_type = "io.containerd.runc.v2"

[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runc.options]
  SystemdCgroup = true

[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.runhcs-wcow-process]
  runtime_type = "io.containerd.runhcs.v1"

[plugins.'io.containerd.cri.v1.images'.registry]
  config_path = "/var/lib/rancher/rke2/agent/etc/containerd/certs.d"

# Configure kata-fc runtime to use devmapper snapshotter
[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.kata-fc]
  runtime_type = "io.containerd.kata.v2"
  snapshotter = "devmapper"

[plugins.'io.containerd.cri.v1.runtime'.containerd.runtimes.kata-fc.options]
  ConfigPath = "/etc/kata-containers/configuration.toml"

# Configure devmapper snapshotter plugin
[plugins."io.containerd.snapshotter.v1.devmapper"]
  root_path = "/var/lib/rancher/rke2/agent/containerd/io.containerd.snapshotter.v1.devmapper"
  pool_name = "ubuntu--vg-containerd--pool"
  base_image_size = "10GB"
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
Verify that `kata-fc` is registered in containerd config:
```bash
sudo grep -A5 "kata-fc" /var/lib/rancher/rke2/agent/etc/containerd/config.toml
```

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
