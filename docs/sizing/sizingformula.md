# Sandbox Cluster Capacity Sizing Formula

This document provides a mathematical capacity planning guide to help cluster administrators determine the optimal number of `sandbox-api` replicas and RabbitMQ prefetch limits based on available physical hardware resources (CPU/Memory).

---

## 1. The Core Sizing Formula

The total number of concurrent repository scans processed by the cluster at any given second is:

$$\text{Max Concurrent Sandboxes} = \text{API Replicas (R)} \times \text{Prefetch Limit per Worker (P)}$$

To prevent Kubernetes scheduler failures (such as `Insufficient CPU` or `FailedScheduling`), you must ensure that your configured capacity is less than or equal to your cluster's **free hardware resource capacity**.

---

## 2. Step-by-Step Capacity Calculation

### Step 1: Account for System Overhead
Every Kubernetes cluster requires a baseline reserve of CPU and Memory resources to run core system utilities (Kubelet, CoreDNS, Ingress Controllers) alongside database and broker dependencies (PostgreSQL, Redis, RabbitMQ, Gateway).

* **Overhead Reserve Rule:**
  * **CPU Reserve:** $1.5 \text{ Cores}$
  * **Memory Reserve:** $2.5 \text{ GiB}$
* **Calculate Free Resources:**
  $$\text{Free CPU} = \text{Total CPU Cores} - 1.5$$
  $$\text{Free RAM} = \text{Total RAM (GiB)} - 2.5$$

### Step 2: Determine Sandbox Pod Resource Footprint
Each repository scan triggers a dynamic, hardened sandbox runner pod that performs security analysis (Semgrep, Trivy, Git Clone). Each sandbox pod requests a specific slice of resources:
* **Sandbox CPU Request:** $0.5 \text{ CPU}$ (equivalent to $500\text{m}$)
* **Sandbox Memory Request:** $0.5 \text{ GiB}$ (equivalent to $512\text{Mi}$)

### Step 3: Calculate Maximum Safe Concurrency
Divide your free cluster resources by the resource request of a single sandbox runner. The lower of the two counts defines your system bottleneck:

$$\text{Max Safe Concurrency (N)} = \min\left( \frac{\text{Free CPU}}{0.5}, \frac{\text{Free RAM}}{0.5} \right)$$

---

## 3. Example Node Sizing Tables

Below are example calculations for standard single-node server configurations:

| Total Node Specs | Free CPU (Cores) | Free RAM (GiB) | Bottleneck | Max Concurrent Sandboxes (N) | Recommended Config ($R \times P$) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **4 Cores / 8 GB** | 2.5 | 5.5 | CPU (5 sandboxes) | **5** | **2 Replicas** $\times$ **2 Prefetch** |
| **8 Cores / 16 GB** | 6.5 | 13.5 | CPU (13 sandboxes) | **13** | **3 Replicas** $\times$ **4 Prefetch** |
| **16 Cores / 32 GB** | 14.5 | 29.5 | CPU (29 sandboxes) | **29** | **4 Replicas** $\times$ **7 Prefetch** |

---

## 4. Applying Configurations

All scaling configurations are managed inside the **`codeInspector/values.yaml`** file:

### Setting Replicas ($R$)
Set the static replica count under the `apiServer.deployment.replicaCount` block:
```yaml
apiServer:
  deployment:
    replicaCount: 3  # <-- Match with R
```

### Setting Prefetch Limits ($P$)
Prefetch limits are configured in the environment variables under `apiServer.configMap`:
```yaml
apiServer:
  configMap:
    MAX_REPO_SCAN_WORKERS: "4"   # <-- Match with P for repository scans
    MAX_QUICK_SCAN_WORKERS: "5"  # <-- Match with P for quick scans
```

---

## 5. Scaling Decisions: Replicas vs. Prefetch

* **When to increase Replicas ($R$):**
  * When dashboard page-load latency increases.
  * When receiving high rates of public web traffic or API integrations.
  * To enable zero-downtime rolling upgrades (minimum recommended $R = 2$).
* **When to increase Prefetch ($P$):**
  * When you want to maximize scanning performance on a single-node setup with minimal web server memory overhead.
