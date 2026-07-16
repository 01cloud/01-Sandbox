# Technical Deep Dive: Kata Containers + Firecracker on Kubernetes (RKE2)

This document provides a comprehensive technical guide on the architecture, mechanics, configuration, and verification of **Kata Containers** using the **Firecracker** hypervisor.

---

## 1. Architecture of Kata Containers + Firecracker

Unlike standard container runtimes (like `runc`) that rely on shared host kernel namespaces and cgroups, Kata Containers boots a dedicated lightweight Virtual Machine (MicroVM) for each Kubernetes Pod.

### Component Diagram

```mermaid
graph TD
    subgraph Host System (Ubuntu)
        K8s[Kubernetes API / Control Plane] -->|Schedules Pod| Kubelet[Kubelet]
        Kubelet -->|CRI gRPC| Containerd[Containerd Daemon]

        subgraph Containerd Plugins
            Containerd -->|Invokes| Shim[containerd-shim-kata-v2]
            Containerd -->|Allocates Blocks| DevMapper[devmapper Snapshotter]
        end

        DevMapper -->|Exposes Thin Device| LVM[LVM Thin Pool]
        Shim -->|Launches| FC[Firecracker Process]

        subgraph Firecracker MicroVM (Hardware-Isolated via KVM)
            FC -->|Loads Guest Kernel| Kernel[Guest Kernel: vmlinux]
            Kernel -->|Starts Init| Agent[Kata Agent]
            Agent -->|Mounts Block Device| Rootfs[Container Rootfs]
            Agent -->|Spawns Container| Application[App Process (e.g. Nginx)]
        end

        LVM -->|Hotplugged via virtio-block| Rootfs
    end
```

### Detailed Flow of Execution

1. **Pod Scheduling:** Kubelet receives a request to run a Pod with `runtimeClassName: kata-fc`.
2. **CRI Request:** Kubelet calls Containerd over the CRI socket, requesting the creation of a Sandbox and Container using the `kata-fc` handler.
3. **Shim Spawning:** Containerd spawns `containerd-shim-kata-v2`. This shim is responsible for managing the lifecycle of the MicroVM.
4. **Storage Allocation:** The `devmapper` snapshotter takes the container image layers, creates a block device (snapshot) from the LVM thin-pool, and maps it to a block device node on the host.
5. **MicroVM Bootstrapping:**
   - The shim launches `firecracker` via `jailer` (for security sandboxing) and sends configuration parameters (kernel path, rootfs image, vCPUs, RAM).
   - Firecracker starts the guest kernel (`vmlinux`) using KVM.
6. **Agent Initialization:** The guest kernel boots and starts the `kata-agent` inside the VM.
7. **Storage & Network Hotplugging:**
   - The container rootfs block device is attached to the VM as a `virtio-block` device and mounted inside the VM.
   - The network tap device (`tapX`) is hot-plugged, and `tc` rules redirect traffic from the host network namespace into the MicroVM.
8. **App Execution:** The `kata-agent` spawns the container processes (e.g., `nginx`) inside the guest namespaces inside the MicroVM.

---

## 2. Installation: Under the Hood

When you install Firecracker and Kata Containers, the binaries and libraries occupy specific roles:

### A. Firecracker Binaries
*   `/usr/local/bin/firecracker`: The main Virtual Machine Monitor (VMM). It uses the Linux Kernel-based Virtual Machine (KVM) API to create and manage vCPUs, memory, and simple virtual devices (block, network, serial).
*   `/usr/local/bin/jailer`: A wrapper that runs Firecracker inside a secure chroot jail. It drops privileges, uses `cgroups` to restrict resources, and applies namespaces to protect the host if Firecracker itself is compromised.

### B. Kata Containers (opt/kata)
*   `kata-runtime`: The CLI tool used by administrators to query, check, and manage the Kata environment.
*   `containerd-shim-kata-v2`: The runtime engine. Instead of calling `runc` to create namespaces, Containerd calls this shim. The shim remains running on the host as a daemon, translating container management commands (start, stop, exec) into commands the `kata-agent` inside the VM understands.
*   `share/kata-containers/vmlinux.container`: A highly optimized guest Linux kernel stripped of unnecessary drivers to minimize boot times (~10–30ms).
*   `share/kata-containers/kata-containers.img`: The initial guest root filesystem image. It contains a minimal system environment (Busybox/systemd) and the `kata-agent` binary.

---

## 3. Configuring Kata with Firecracker

The configuration file `/etc/kata-containers/configuration.toml` defines how the host interacts with the guest. Key directives include:

```toml
[hypervisor.firecracker]
path = "/usr/local/bin/firecracker"
kernel = "/opt/kata/share/kata-containers/vmlinux.container"
image = "/opt/kata/share/kata-containers/kata-containers.img"
jailer_path = "/opt/kata/bin/jailer"
```

*   **`path`**: Directs the shim to the Firecracker binary.
*   **`kernel` & `image`**: Specifies the guest operating system kernel and agent system image that Firecracker boots.
*   **`jailer_path`**: Tells Kata to wrap the Firecracker process in a jailer sandbox.
*   **Resource settings** (defined further down in the file):
    ```toml
    default_vcpus = 1
    default_memory = 2048 # Allocates 2GB of host RAM to the microVM
    block_device_driver = "virtio-mmio"
    ```
    Since Firecracker does not support standard PCI buses, it uses **`virtio-mmio`** (memory-mapped IO) to attach network and block devices, drastically reducing emulation complexity.

---

## 4. Key Commands & Diagnostics

### A. `kata-runtime check`
Validates that the host meets the requirements for running Kata:
```bash
sudo kata-runtime check
```
*It verifies `/dev/kvm` permissions, checks if the CPU has virtualization extensions (`intel_has_vx` or `amd_svm`), and checks kernel module availability.*

### B. `kata-ctl env`
Prints the active environment configuration:
```bash
sudo kata-ctl env
```
*Shows the loaded config paths, paths to binaries, guest kernel command line parameters, and hypervisor details.*

### C. `dmsetup` and `lvdisplay`
Exposes the physical backing of the devmapper snapshotter:
```bash
sudo dmsetup ls
# Shows active device mapper devices on the host

sudo lvs
# Confirms the data/meta consumption of the LVM thin-pool
```

### D. `crictl` and `ctr`
Lower-level container inspection tools:
```bash
# Check the container details inside containerd's Kubernetes namespace
sudo crictl --runtime-endpoint unix:///run/k3s/containerd/containerd.sock ps

# Check the running tasks from containerd's perspective
sudo ctr -n k8s.io tasks ls
```

---

## 5. Verifying Security, Performance, and Orchestration

Here is how you can verify the value proposition of Kata Containers + Firecracker:

### A. Verify Hardware-Level Security (Kernel Isolation)
Run a standard pod (`runc`) and a Kata pod (`kata-fc`), and compare their kernels.

1. **Standard Pod Kernel Check:**
   ```bash
   kubectl run test-runc --image=nginx --restart=Never
   kubectl exec test-runc -- uname -r
   # Returns the host machine's exact kernel version (e.g., 5.15.0-xxx-generic)
   ```
2. **Kata Pod Kernel Check:**
   ```bash
   kubectl exec kata-test -- uname -r
   # Returns the Kata Guest Kernel version (e.g., 6.1.x-kata)
   ```
3. **Process Space Check:**
   Inside `kata-test`, look at the running processes:
   ```bash
   kubectl exec kata-test -- ps aux
   # You will only see the Nginx processes and the kata-agent.
   # You cannot see any processes running on the host or in other pods.
   ```

### B. Verify Memory and Startup Overhead (Performance)
1. **Startup Time:**
   Check the event logs of your `kata-test` pod:
   ```bash
   kubectl describe pod kata-test
   ```
   Under `Events`, examine the time difference between `Scheduled`, `Pulling image`, and `Started container`. You will find that VM creation and guest boot take **less than 1 second**!
2. **Memory Footprint:**
   Find the active `firecracker` process on the host:
   ```bash
   ps -o pid,rss,command -C firecracker
   ```
   The `RSS` (Resident Set Size) shows the physical memory used by the hypervisor itself. It is typically **under 15-20 MB**, which is incredibly lightweight compared to traditional hypervisors (QEMU/KVM processes usually consume 100+ MB just to start).

### C. Verify Native Kubernetes Orchestration
Verify that networking works identically to normal pods:
1. **Expose the Pod:**
   ```bash
   kubectl expose pod kata-test --port=80 --target-port=80 --type=ClusterIP
   ```
2. **Test Access:**
   Launch a temporary shell pod and try to curl the service:
   ```bash
   kubectl run test-curl --rm -i --tty --image=curlimages/curl -- sh
   # Inside the pod:
   curl http://kata-test
   # Returns the default Nginx welcome page
   ```
   *This proves the `tcfilter` network bridging works perfectly, routing traffic through the Kubernetes SDN (Cilium) straight into the Firecracker MicroVM.*
