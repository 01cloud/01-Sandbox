# KinD Cluster & Sandboxed Runtime Helm Chart (`kindCluster`)

## 1. Overview & Problem Statement

Standard container runtimes (such as standard Docker or `runc`) share the host Linux operating system kernel among all containers on a node. In typical enterprise microservices where all code is trusted and written by internal engineers, kernel namespaces and cgroups provide adequate isolation.

However, in an untrusted multi-tenant code execution platform like **01-Sandbox**, users submit arbitrary, unvetted Python/Bash/Node.js code snippets to be executed dynamically. Sharing the host kernel presents severe security vulnerabilities:
- **Kernel Exploits:** Any zero-day privilege escalation bug in the Linux kernel can lead to container escape and full host compromise.
- **Side-Channel Attacks:** Host-shared CPU caches can leak cryptographic secrets across tenants.
- **Resource Starvation:** Rogue user processes could spawn infinite processes (fork bombs) or flood kernel socket tables.

The `kindCluster` chart configures cluster-level runtime isolation primitives, specifically declaring the **Kubernetes RuntimeClass** for sandboxed runtimes like **gVisor (`runsc`)** and **Kata Containers**.

```
                   Standard Container (runc)            Sandboxed Sandbox Pod (runsc)
                  ┌─────────────────────────┐          ┌──────────────────────────────┐
                  │ User Code Execution     │          │ Untrusted User Code Execution│
                  └────────────┬────────────┘          └──────────────┬───────────────┘
                               │ Syscalls                             │ Syscalls
                               │ (Direct)                             ▼
                               │                       ┌──────────────────────────────┐
                               │                       │ gVisor Sentry (runsc)        │
                               │                       │ (User-space guest kernel     │
                               │                       │  intercepts & filters calls) │
                               │                       └──────────────┬───────────────┘
                               │                                      │ Limited Syscalls
                               ▼                                      ▼
                  ┌───────────────────────────────────────────────────────────────────┐
                  │ Host Operating System Kernel (Linux 6.x)                          │
                  └───────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

- **RuntimeClass (`node.k8s.io/v1`):** A cluster-scoped resource that tells the Container Runtime Interface (CRI)—such as `containerd` or `CRI-O`—which underlying runtime handler to use when instantiating a Pod.
- **gVisor (`runsc`):** An open-source, user-space virtual kernel created by Google. It implements a substantial portion of the Linux system call interface in Go, acting as a sandbox boundary between the container and the host kernel.
- **Handler Mapping:** When a Pod specification includes `runtimeClassName: gvisor`, containerd matches the string `gvisor` to the `handler: runsc` defined in `/etc/containerd/config.toml`, triggering sandboxed execution.
- **KinD (Kubernetes in Docker):** A lightweight development and CI/CD Kubernetes distribution that runs Kubernetes nodes as Docker containers. This chart allows KinD clusters to emulate production multi-tenant sandboxing environments locally.

---

## 3. Template-by-Template Manifest Breakdown

The chart contains a single, critical template in [codeInspector/charts/kindCluster/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/kindCluster/templates/):

### 3.1. [runtimeclass.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/kindCluster/templates/runtimeclass.yaml)
- **Kind:** `RuntimeClass` (`node.k8s.io/v1`)
- **Metadata:** Name dynamically set via `{{ .Values.runtimeClass.name }}` (default: `gvisor`).
- **Functionality:**
  - Binds the class name to `handler: {{ .Values.runtimeClass.handler }}` (default: `runsc`).
  - Conditional rendering: Automatically suppressed when Open Cluster Management (`.Values.global.ocm.enabled`) manages runtime classes across spoke clusters centrally.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `namespace` | string | `"kind-cluster"` | Chart namespace. |
| `runtimeClass.name` | string | `"gvisor"` | The identifier referenced in Pod `spec.runtimeClassName`. |
| `runtimeClass.handler` | string | `"runsc"` | The CRI runtime handler configured on worker nodes. |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

- **Defense-in-Depth Execution Boundary:** Every sandbox spawned by `opensandbox` or `opensandboxResourcePool` specifies `runtimeClassName: gvisor`.
- **Zero Host Impact:** Even if a user executes malicious code attempting to exploit Linux kernel vulnerabilities (like Dirty COW or io_uring exploits), the code is trapped inside gVisor's Sentry sandbox and cannot touch the underlying EC2 instance or other tenant workloads.

---

## 6. Practical Debugging & Troubleshooting

### Verify RuntimeClass Registration
```bash
# List all registered RuntimeClasses in the cluster
kubectl get runtimeclass

# Inspect the gVisor RuntimeClass definition
kubectl describe runtimeclass gvisor
```

### Test Sandboxed Execution
```bash
# Launch a test pod using the gvisor RuntimeClass
kubectl apply -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: test-gvisor
  namespace: default
spec:
  runtimeClassName: gvisor
  containers:
  - name: test
    image: alpine:latest
    command: ["dmesg"]
EOF

# Verify that dmesg output indicates gVisor
kubectl logs test-gvisor
# Expected output contains: "Starting gVisor..."
```

### Common Failure Modes
1. **Pod Fails with `CreateContainerError` / `failed to run container: runtime handler not found`:** The containerd daemon on the node has not configured the `runsc` handler in `/etc/containerd/config.toml`. Run `systemctl restart containerd` after adding the gVisor handler.
2. **Unsupported System Call inside Sandbox:** gVisor intercepts syscalls; if user code relies on esoteric or un-emulated Linux syscalls, the process may exit with `ENOSYS`. Review gVisor compatibility matrices.
