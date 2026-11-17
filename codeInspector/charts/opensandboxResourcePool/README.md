# OpenSandbox Pre-Warmed Resource Pool Helm Chart (`opensandboxResourcePool`)

## 1. Overview & Problem Statement

In interactive developer tooling, automated code analysis, and AI agent execution (such as LLM code execution tools), latency is paramount. When an end-user runs a Python snippet or asks an AI agent to compute data, waiting 3 to 10 seconds for Kubernetes to schedule a pod, pull container layers, and boot the gVisor sandbox kernel creates an unacceptable user experience.

This latency penalty is called the **Cold-Start Problem**.

The `opensandboxResourcePool` chart solves this cold-start dilemma by deploying declarative **Pre-Warmed Sandbox Pools**. Instead of launching pods on demand, the pool maintains a warm standby cache of ready, running sandbox containers. When a client requests execution, OpenSandbox immediately assigns an idle, pre-warmed sandbox in **under 50 milliseconds**.

```
                Incoming Code Execution Request
                              │
                              ▼ (Latency < 50ms)
    ┌────────────────────────────────────────────────────────┐
    │ Ready / Pre-Warmed Sandbox (Assigned Instantly)        │
    └────────────────────────────────────────────────────────┘

    Meanwhile, in the background:
    ┌────────────────────────────────────────────────────────┐
    │ OpenSandbox Controller detects buffer dropped below    │
    │ bufferMin -> Asynchronously spawns replacement pod    │
    └────────────────────────────────────────────────────────┘
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

- **Custom Resource Definition (`Pool`):** Unlike standard Kubernetes `Deployments` (which manage uniform stateless web servers), the `Pool` CRD (`sandbox.opensandbox.io/v1alpha1`) manages a dynamic lifecycle of stateful, disposable execution pods with distinct states (`Idle`, `Assigned`, `Terminating`).
- **Buffer vs. Pool Capacity Management:**
  - `bufferMin`: The minimum number of hot, idle, ready-to-claim pods kept running in memory at all times.
  - `bufferMax`: The maximum number of idle standby pods allowed before throttling scale-up.
  - `poolMin`: The baseline minimum total capacity (active + idle).
  - `poolMax`: The hard upper limit on total concurrent pods, preventing runaway resource consumption or cluster Out-Of-Memory (OOM) conditions.
- **Warm Standby Lifecycle:**
  1. A pod boots with `opensandbox/code-interpreter:v1.0.2` and sits idle in the `Available` state.
  2. A user API request arrives: the server transitions the pod to `Claimed` / `InUse`.
  3. The controller automatically detects `bufferCount < bufferMin` and spins up a new pod to replenish the standby pool.
  4. Once user execution completes or times out, the pod is destroyed (never recycled) to prevent cross-tenant contamination.

---

## 3. Template-by-Template Manifest Breakdown

The chart contains a focused, high-impact template in [codeInspector/charts/opensandboxResourcePool/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/opensandboxResourcePool/templates/):

### 3.1. [pool.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/opensandboxResourcePool/templates/pool.yaml)
- **Kind:** `Pool` (`sandbox.opensandbox.io/v1alpha1`)
- **Metadata:** Name set via `.Values.pool.name` (default: `my-sandbox-pool`) in `.Values.namespace` (`opensandbox-system`).
- **Functionality:**
  - **Container Template:** Configures the sandboxed worker container (`opensandbox/code-interpreter:v1.0.2`).
  - **Port Mappings:** Exposes internal communication and debugging ports (e.g., `44772` and `54321`) used by the execution agent.
  - **Capacity Specification:** Configures `bufferMin`, `bufferMax`, `poolMin`, and `poolMax`.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `namespace` | string | `"opensandbox-system"` | Namespace where the resource pool operates. |
| `pool.name` | string | `"my-sandbox-pool"` | Identifier for the Pool custom resource. |
| `pool.container.image` | string | `"opensandbox/code-interpreter:v1.0.2"` | Base runtime container image for sandboxes. |
| `pool.container.ports` | list | `[{"containerPort": 44772}, {"containerPort": 54321}]` | Listening ports inside the sandbox pod. |
| `pool.capacitySpec.bufferMin` | integer | `3` | Minimum warm idle pods ready for instant allocation. |
| `pool.capacitySpec.bufferMax` | integer | `10` | Maximum warm idle pods allowed. |
| `pool.capacitySpec.poolMin` | integer | `1` | Absolute minimum pods in the pool. |
| `pool.capacitySpec.poolMax` | integer | `50` | Absolute maximum concurrent pods allowed. |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

- **Zero-Latency Execution:** Powers 01-Sandbox's instantaneous code execution capabilities, guaranteeing sub-second response times for developers and AI agents.
- **Resource Protection:** Enforces strict upper bounds (`poolMax: 50`) per pool, preventing malicious or runaway scripts from starving other critical cluster microservices (like `apiServer` or `cortex`).

---

## 6. Practical Debugging & Troubleshooting

### Inspect Pool State & Metrics
```bash
# Query active pool status
kubectl get pools.sandbox.opensandbox.io -n opensandbox-system
kubectl describe pool my-sandbox-pool -n opensandbox-system

# Check the pods managed by this pool
kubectl get pods -n opensandbox-system -l pool.sandbox.opensandbox.io/name=my-sandbox-pool
```
*Look for fields: `Status.ReadyReplicas`, `Status.AllocatedReplicas`, and `Status.BufferReplicas`.*

### Common Failure Modes
1. **Buffer Cannot Reach `bufferMin`:** If ready pods stay below `bufferMin`, the cluster may be out of CPU/memory capacity, or nodes cannot pull `pool.container.image`. Check `kubectl describe nodes` and inspect events.
2. **Pod Allocation Stuck:** If API calls timeout claiming a sandbox, check if `poolMax` has been reached. Inspect `kubectl get pool my-sandbox-pool -o yaml` to check if `AllocatedReplicas == poolMax`.
3. **Pods Terminating Abruptly:** Check if the host kernel or OOM-killer is terminating sandboxes due to memory limits.
