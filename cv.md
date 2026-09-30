# Project Portfolio & CV Highlights: Multi-Cluster Zero-Trust MicroVM Sandbox Platform

## Project Title Suggestions
- **Multi-Cluster Kubernetes Zero-Trust MicroVM Sandbox Platform (01-Sandbox)**
- **High-Availability Multi-Cluster Orchestrator with MicroVM Sandboxing (OCM + Firecracker)**
- **Zero-Trust Multi-Tenant Cloud Sandboxing Infrastructure**

---

## 1. Executive Summary (Short & Impactful)
> Architected and engineered an automated, zero-trust, multi-cluster Kubernetes platform designed for secure, multi-tenant sandbox execution of untrusted code. Integrated Open Cluster Management (OCM) in an active-passive dual-hub topology with automated failover, an encrypted WireGuard full-mesh overlay, and isolated hardware-virtualized runtimes combining Kata Containers + Firecracker microVMs and gVisor.

---

## 2. High-Impact CV Bullet Points (STAR / XYZ Format)

### Core Impact Bullets (Pick 3–5 for your CV Experience / Projects section)

- **Multi-Cluster Orchestration & High Availability:**
  - Designed and deployed an active-passive multi-cluster Kubernetes control plane using **Open Cluster Management (OCM)**, managing distributed spoke clusters across isolated networks with automated hub failover and mutual TLS synchronization.
- **Hardware-Isolated MicroVM Sandboxing:**
  - Engineered secure, multi-tenant container sandboxing leveraging **Kata Containers v3.18** and **AWS Firecracker v1.11**, running dedicated guest Linux microVM kernels (`vmlinux.container`) with device-mapper (`devmapper`) LVM thin-pool snapshotting for ephemeral, untrusted workload execution.
- **Zero-Trust Overlay Mesh Networking:**
  - Built an end-to-end zero-trust network topology connecting control-plane hubs and spoke clusters over an encrypted **WireGuard overlay mesh (10.99.0.0/24)**, completely abstracting underlying transport networks and securing inter-cluster control traffic.
- **L4/L7 Ingress & Reverse Proxying:**
  - Integrated **Envoy Gateway** and **MetalLB** with customized route filters, automated PKI/CA certificate distribution, and DNS discovery to safely route external API requests to isolated backend sandbox runtimes.
- **Automated Infrastructure as Code (IaC):**
  - Developed fully idempotent, modular automation scripts capable of bootstrapping the entire multi-cluster ecosystem—including kernel tuning, loopback thin-pools, OCM registration, and RuntimeClasses (`kata-fc`, `gvisor`, `runc`)—in a single-command execution.
- **Dynamic Cluster Capability Discovery:**
  - Implemented automated node-capability profiling and dynamic label synchronization on OCM ManagedClusters (`runtime.kata-fc=true`, `runtime.gvisor=true`), enabling scheduling policies that automatically route untrusted code to hardware-isolated nodes.

---

## 3. Role-Specific Tailored Variations

### Option A: For Platform / Kubernetes Infrastructure Roles
- Architected a resilient multi-cluster Kubernetes platform orchestrating primary and secondary hubs via Open Cluster Management (OCM), supporting transparent failover of managed spoke clusters.
- Configured containerd with custom snapshotters (devmapper LVM thin-pools) and container runtimes (Kata-Firecracker, gVisor, standard runc) via Kubernetes `RuntimeClass` specifications.
- Automated multi-node cluster deployment, kernel module configuration (`tun`, `kvm`), and network policy enforcement across heterogeneous virtual environments.

### Option B: For Cloud Security / DevSecOps Roles
- Implemented multi-layered defense-in-depth isolation for arbitrary code execution, mitigating container breakout risks using hardware-assisted KVM microVMs (Firecracker) and syscall-filtering sandboxes (gVisor).
- Enforced zero-trust inter-cluster network isolation via a peer-to-peer WireGuard mesh, eliminating open public exposure for control plane API endpoints.
- Designed automated PKI certificate rotation and mutual TLS (mTLS) authentication across edge proxies, control planes, and worker runtimes.

### Option C: For Systems / DevOps Automation Roles
- Engineered modular shell-based Infrastructure as Code (IaC) orchestrating Linux loopback devices, LVM thin-provisioning, systemd services, and KinD/Docker nodes in under 5 minutes.
- Automated end-to-end smoke testing and verification pipelines for guest kernel microVM execution, containerd snapshotter health, and cluster join workflows.

---

## 4. Technical Skills & Keywords (For ATS Optimization)

| Category | Technologies & Tools |
| :--- | :--- |
| **Container & MicroVM Runtimes** | Kata Containers, AWS Firecracker (`kata-fc`), gVisor (`runsc`), Containerd, CRI, Device Mapper (`devmapper`), LVM Thin-Pools, KVM virtualization |
| **Multi-Cluster & Orchestration** | Kubernetes (K8s), Open Cluster Management (OCM), KinD, ManagedClusters, Klusterlet Agent, Active-Passive Failover, `RuntimeClass` |
| **Networking & Security** | WireGuard Overlay Mesh, Zero-Trust Architecture, Envoy Gateway, MetalLB, mTLS, PKI / OpenSSL, Linux namespaces, cgroups, `tun/tap` |
| **Automation & Systems** | Bash / Shell Scripting (Modular IaC), Linux Kernel Modules (`kvm`, `dm_thin_pool`), Systemd, Docker, CI/CD Integration |

---

## 5. Architectural Talking Points for Interviews

1. **Why Firecracker over standard runc?**
   - *Answer:* Standard runc containers share the host Linux kernel, making multi-tenant untrusted code susceptible to privilege escalation and kernel CVEs. Kata with Firecracker provides hardware-enforced hypervisor isolation via KVM with near-instantaneous microVM boot times (<100ms) and low memory overhead.
2. **Why devmapper thin-pool snapshotting?**
   - *Answer:* Firecracker does not support 9pfs/virtio-fs filesystem sharing in the same manner as QEMU. Containerd devmapper creates dedicated block devices formatted with ext4 for each container layer, backed by an LVM thin pool, delivering high I/O throughput and true block-level isolation.
3. **How does OCM failover work in this setup?**
   - *Answer:* Both primary and secondary hubs maintain synchronized ManagedCluster resources and dual bootstrap secrets. Spoke klusterlet agents communicate over the WireGuard mesh and seamlessly transition cluster state to the secondary hub upon primary control plane degradation.
