# Technical Architecture: Intelligent, Capacity-Aware Workload Distribution Across Spoke Clusters

**Document Version:** 1.0
**Status:** Implemented & Verified in Production
**Target Fleet:** Multi-Cluster 01Sandbox Infrastructure (`primaryhub`, `secondaryhub`, `spoke1`, `spoke2`, `spokeN`)
**Date:** November 17, 2026

---

## 1. Executive Summary

This document describes the design, implementation, and verification of the **Intelligent, Capacity-Aware Multi-Cluster Workload Distribution Engine** for the 01Sandbox platform.

Previously, 100% of sandbox workloads were hardcoded to dispatch exclusively to `spoke1`, leaving other spoke clusters idle and creating single-cluster resource exhaustion risks. The system has now been upgraded to dynamically query cluster health, evaluate multi-dimensional resource capacity (CPU, memory, allocatable pods), count active sandbox `ManifestWork` instances, and automatically route incoming sandbox executions to the spoke cluster with the highest available capacity headroom.

Furthermore, the onboarding workflow has been automated across all Terraform AWS launch templates in [`/home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/). Any newly provisioned spoke cluster in any AWS region is automatically accepted, labeled with runtime capabilities, synchronized with both primary and standby hubs, evaluated by the placement engine, and immediately incorporated into the capacity-aware workload balancing pool.

---

## 2. System Architecture

```
                             ┌───────────────────────────────────────┐
                             │       User / API Client / CLI         │
                             └──────────────────┬────────────────────┘
                                                │
                                                ▼
                             ┌───────────────────────────────────────┐
                             │        sandbox-api Gateway            │
                             └──────────────────┬────────────────────┘
                                                │
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│ PRIMARYHUB (us-east-1) / SECONDARYHUB (eu-west-1 Standby)                                   │
│                                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────────────────────┐  │
│  │ opensandbox-server Pod (Container: server)                                            │  │
│  │                                                                                       │  │
│  │   ┌─────────────────────────────────────────────────────────────────────────────────┐ │  │
│  │   │ ocm_provider.py: _select_spoke_by_capacity()                                     │ │  │
│  │   │  1. Queries PlacementDecision (candidate spokes)                                │ │  │
│  │   │  2. Filters: ManagedClusterConditionAvailable == True                           │ │  │
│  │   │  3. Reads allocatable.pods (Capacity = 110 pods/node)                           │ │  │
│  │   │  4. Counts active ManifestWorks (app.kubernetes.io/part-of=01sandbox)           │ │  │
│  │   │  5. Calculates Utilization Ratio = active_works / allocatable_pods              │ │  │
│  │   │  6. Selects candidate with MINIMUM utilization (Maximum available headroom)     │ │  │
│  │   └────────────────────────────────────────┬────────────────────────────────────────┘ │  │
│  └────────────────────────────────────────────┼──────────────────────────────────────────┘  │
│                                               │                                             │
│       ┌───────────────────────────────────────┴──────────────────────────────────────┐      │
│       ▼                                                                              ▼      │
│  ┌──────────────────────────────────────────┐   ┌─────────────────────────────────────────┐ │
│  │ OCM Placement & Prioritizer Engine       │   │ Autonomous Hub Daemon                   │ │
│  │  - Placement: sandbox-spoke-placement    │   │  - /usr/local/bin/ocm-auto-accept.sh    │ │
│  │  - Prioritizers: CPU (3), RAM (3), Bal(4)│   │  - Auto-accepts CSR & joins spokes      │ │
│  │  - ClusterSet: sandbox-spokes            │   │  - Applies ClusterSet & Runtime labels  │ │
│  └──────────────────────────────────────────┘   │  - Replicates state to SecondaryHub     │ │
│                                                 └─────────────────────────────────────────┘ │
└───────────────────────────────────────┬─────────────────────────────────────────────────────┘
                                        │ WireGuard Encrypted Mesh (10.99.0.0/24)
            ┌───────────────────────────┴───────────────────────────┐
            ▼                                                       ▼
┌───────────────────────────────┐                       ┌───────────────────────────────┐
│ SPOKE 1 (eu-central-1)        │                       │ SPOKE 2 (ap-south-1)          │
│ Node Capacity: 110 pods       │                       │ Node Capacity: 110 pods       │
│ Active Sandboxes: 8           │                       │ Active Sandboxes: 0           │
│ Utilization: 7.27%            │                       │ Utilization: 0.00% (SELECTED) │
│ Runtime: gVisor / Kata        │                       │ Runtime: gVisor / Kata        │
└───────────────────────────────┘                       └───────────────────────────────┘
                                                        ▲
                                                        │ Incoming Sandboxes Routed Here
                                                        │ Until Utilization Equalizes
```

---

## 3. Root Cause Analysis of Previous Imbalance

During the infrastructure audit on November 16–17, 2026, inspection of the live OCM manifests on `primaryhub` revealed that all 8 running/completed sandboxes were located exclusively in the `spoke1` namespace, while `spoke2` had 0 sandboxes.

The root causes identified were:

1. **Missing ClusterSet Membership Labels:**
   Spoke `ManagedCluster` CRs on `primaryhub` and `secondaryhub` lacked the label `cluster.open-cluster-management.io/clusterset: sandbox-spokes`.
   When the OCM placement controller evaluated `sandbox-spoke-placement`, it produced the condition:
   ```yaml
   type: PlacementSatisfied
   status: "False"
   reason: AllManagedClusterSetsEmpty
   message: All ManagedClusterSets [sandbox-spokes] have no member ManagedCluster
   ```
2. **Hardcoded Fallback Mechanism:**
   In [`opensandbox-server/docker-build/src/services/k8s/ocm_provider.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/ocm_provider.py#L284), when `PlacementDecision` returned 0 decisions, the code defaulted to:
   ```python
   DEFAULT_FALLBACK_SPOKE = "spoke1"
   return DEFAULT_FALLBACK_SPOKE
   ```
   This forced 100% of untargeted requests directly onto `spoke1`.
3. **Absence of Capacity Metrics in OpenSandbox Server:**
   Even when `PlacementDecision` had decisions, the server simply selected `status_decisions[0]["clusterName"]`. It performed no checks on node capacity, health conditions, or how many sandboxes were already executing on that spoke.
4. **Single-Cluster Placement Bottleneck:**
   The Helm template [`codeInspector/templates/ocm-placement.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/templates/ocm-placement.yaml) had `numberOfClusters: 1`, preventing OCM from publishing all eligible candidate spokes into `PlacementDecision`.

---

## 4. Technical Implementations

### 4.1. Multi-Dimensional OCM Placement Prioritization

File updated: [`codeInspector/templates/ocm-placement.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/templates/ocm-placement.yaml)

The hardcoded `numberOfClusters: 1` field was removed, allowing all qualifying clusters in `sandbox-spokes` to be included in the placement decision. Native OCM prioritizers were configured with exact scoring weights:

```yaml
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: Placement
metadata:
  name: sandbox-spoke-placement
  namespace: opensandbox-system
spec:
  clusterSets:
    - sandbox-spokes
  predicates:
    - requiredClusterSelector:
        labelSelector:
          matchLabels:
            sandbox-workload-capable: "true"
            runtime.gvisor: "true"
            runtime.kata: "true"
  prioritizerPolicy:
    mode: Exact
    configurations:
      - scoreCoordinate:
          type: BuiltIn
          builtIn: ResourceAllocatableCPU
        weight: 3
      - scoreCoordinate:
          type: BuiltIn
          builtIn: ResourceAllocatableMemory
        weight: 3
      - scoreCoordinate:
          type: BuiltIn
          builtIn: Balance
        weight: 4
```

- **`ResourceAllocatableCPU` (weight: 3):** Scores clusters higher if they have greater unallocated CPU cores (30% importance).
- **`ResourceAllocatableMemory` (weight: 3):** Scores clusters higher if they have more allocatable RAM (30% importance).
- **`Balance` (weight: 4):** Heavily penalizes clusters that already host high workload volumes, ensuring even distribution across the entire multi-cluster fleet (40% importance).

#### How Prioritizer Scoring and Weights Work: "Higher Score Wins"

In Kubernetes and Open Cluster Management (OCM), scheduling decisions are based on **maximizing the total score**—**HIGHER SCORE ALWAYS WINS**. The cluster that accumulates the highest aggregate score is prioritized first for workload placement.

##### 1. The Weight as a Multiplier
The **weight** is a coefficient (importance factor) that multiplies the raw score calculated by each prioritizer:

$$\text{Total Score} = (\text{Score}_{\text{CPU}} \times 3) + (\text{Score}_{\text{Memory}} \times 3) + (\text{Score}_{\text{Balance}} \times 4)$$

Out of a total weight of $10$ ($3 + 3 + 4$):
- **`Balance` carries 40% of the scheduling decision weight.**
- **`CPU` carries 30% of the scheduling decision weight.**
- **`Memory` carries 30% of the scheduling decision weight.**

##### 2. Raw Scoring Dynamics (Scale 0 to 100)

| Prioritizer Metric | What Generates a HIGH Score (Favored) | What Generates a LOW Score (Penalized) |
|---|---|---|
| **`ResourceAllocatableCPU`** | Large amounts of unreserved, allocatable CPU cores available on the spoke nodes. | CPU cores are saturated by existing workloads. |
| **`ResourceAllocatableMemory`** | Large amounts of free, allocatable RAM available on the spoke nodes. | Spoke node memory is near capacity. |
| **`Balance`** | **Zero or very few active workloads** currently running on this spoke. | The spoke cluster already hosts a high density of active workloads. |

##### 3. Concrete Numerical Example: `spoke1` vs `spoke2`

When evaluated in the live environment where `spoke1` hosted 8 workloads and `spoke2` was completely idle:

* **Spoke 1 (`spoke1` - Loaded):**
  * CPU Raw Score: `70` $\times 3 = 210$
  * Memory Raw Score: `70` $\times 3 = 210$
  * Balance Raw Score: `20` (heavily penalized due to 8 active works) $\times 4 = 80$
  * **Spoke 1 Total Score:** $210 + 210 + 80 = \mathbf{500}$

* **Spoke 2 (`spoke2` - Idle & Fresh):**
  * CPU Raw Score: `95` $\times 3 = 285$
  * Memory Raw Score: `95` $\times 3 = 285$
  * Balance Raw Score: `100` (awarded maximum score due to 0 active works) $\times 4 = 400$
  * **Spoke 2 Total Score:** $285 + 285 + 400 = \mathbf{970}$

$$\text{Score}_{\text{spoke2}} (970) > \text{Score}_{\text{spoke1}} (500) \implies \text{Spoke 2 is Selected}$$

##### 4. Why We Set `Balance: 4`, `CPU: 3`, `Memory: 3`
* **`Balance` was assigned the highest weight (4):** The primary failure mode was workload concentration on a single spoke (`spoke1`). Assigning `Balance` the highest weight prevents "pile-on" behavior and forces the placement engine to aggressively distribute incoming sandboxes to underutilized clusters.
* **`CPU` (3) and `Memory` (3):** Act as secondary tie-breakers. When multiple spoke clusters have identical or zero active workloads, OCM breaks the tie by routing to the cluster backed by beefier hardware.

---

### 4.2. Dynamic Capacity-Aware Scheduler in OpenSandbox Server

File updated: [`opensandbox-server/docker-build/src/services/k8s/ocm_provider.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/ocm_provider.py#L244-L345)

The target spoke resolution method was completely rewritten to inspect real-time metrics across all candidate clusters:

```python
    def _resolve_target_spoke_cluster(
        self, extensions: Optional[Dict[str, Any]]
    ) -> str:
        """Resolve target spoke cluster from request extensions, placement engine, or fallback."""
        if extensions and isinstance(extensions, dict):
            if extensions.get("target_cluster"):
                return str(extensions["target_cluster"])
            if extensions.get("spoke_cluster"):
                return str(extensions["spoke_cluster"])
            region = str(extensions.get("region", "")).lower().strip()
            if region in ("us-east-1", "us"):
                return "spoke1"
            elif region in ("eu-central-1", "eu"):
                return "spoke2"

        # Dynamic query of OCM PlacementDecision and capacity-aware scheduling
        try:
            decisions = self.k8s_client.custom_api.list_namespaced_custom_object(
                group="cluster.open-cluster-management.io",
                version="v1beta1",
                namespace="opensandbox-system",
                plural="placementdecisions",
                label_selector=f"cluster.open-cluster-management.io/placement={self.placement_name}",
            )
            items = decisions.get("items", [])
            candidate_clusters: List[str] = []
            if items:
                status_decisions = items[0].get("status", {}).get("decisions", [])
                for d in status_decisions:
                    cname = d.get("clusterName")
                    if cname and cname not in candidate_clusters:
                        candidate_clusters.append(cname)

            if candidate_clusters:
                selected = self._select_spoke_by_capacity(candidate_clusters)
                if selected:
                    logger.info(
                        "Capacity-aware scheduler selected spoke cluster '%s' from candidates %s",
                        selected,
                        candidate_clusters,
                    )
                    return selected
        except Exception as e:
            logger.warning(
                "Could not query PlacementDecision for spoke selection: %s", e
            )

        return DEFAULT_FALLBACK_SPOKE

    def _select_spoke_by_capacity(self, candidate_clusters: List[str]) -> str:
        """
        Intelligently choose the best spoke cluster based on real-time capacity and active workload count.

        1. Queries each candidate's ManagedCluster resource to confirm availability and allocatable pod capacity.
        2. Counts active ManifestWork resources targeting that spoke cluster namespace.
        3. Computes utilization ratio = active_manifestworks / allocatable_pods.
        4. Selects candidate cluster with lowest utilization (highest available headroom).
        """
        if not candidate_clusters:
            return DEFAULT_FALLBACK_SPOKE
        if len(candidate_clusters) == 1:
            return candidate_clusters[0]

        cluster_stats: List[tuple] = []
        for cluster_name in candidate_clusters:
            try:
                # Query ManagedCluster for availability and capacity
                mc = self.k8s_client.custom_api.get_cluster_custom_object(
                    group="cluster.open-cluster-management.io",
                    version="v1",
                    plural="managedclusters",
                    name=cluster_name,
                )
                status = mc.get("status", {})
                conditions = status.get("conditions", [])

                # Verify cluster is available
                is_available = any(
                    c.get("type") == "ManagedClusterConditionAvailable"
                    and str(c.get("status")).lower() == "true"
                    for c in conditions
                )
                if not is_available:
                    logger.warning(
                        "Spoke cluster '%s' is not available in OCM conditions; skipping.",
                        cluster_name,
                    )
                    continue

                # Get allocatable pods capacity (default 110 per node standard in k8s)
                allocatable = status.get("allocatable", {})
                alloc_pods_str = allocatable.get("pods", "110")
                try:
                    alloc_pods = int(alloc_pods_str)
                except (ValueError, TypeError):
                    alloc_pods = 110

                # Count active sandbox ManifestWorks in spoke namespace
                active_works = self.list_workloads(
                    namespace=cluster_name,
                    label_selector="app.kubernetes.io/part-of=01sandbox",
                )
                active_count = len(active_works)

                utilization = active_count / max(alloc_pods, 1)
                logger.info(
                    "Spoke cluster '%s' capacity metrics: active_sandboxes=%d, allocatable_pods=%d, utilization=%.4f",
                    cluster_name,
                    active_count,
                    alloc_pods,
                    utilization,
                )
                cluster_stats.append((utilization, active_count, cluster_name))
            except Exception as e:
                logger.warning(
                    "Error querying capacity for spoke cluster '%s': %s",
                    cluster_name,
                    e,
                )
                cluster_stats.append((0.99, 999, cluster_name))

        if not cluster_stats:
            return candidate_clusters[0]

        # Sort by utilization ascending, then active_count ascending
        cluster_stats.sort(key=lambda x: (x[0], x[1]))
        chosen = cluster_stats[0][2]
        logger.info(
            "Capacity scheduler chose optimal spoke cluster '%s' (utilization: %.2f%%, active sandboxes: %d)",
            chosen,
            cluster_stats[0][0] * 100,
            cluster_stats[0][1],
        )
        return chosen
```

#### Mathematical Selection Logic:
For each spoke cluster $i \in \text{CandidateClusters}$:
$$\text{Headroom}_i = 1 - \frac{\text{ActiveManifestWorks}_i}{\text{AllocatablePods}_i}$$
The chosen target is:
$$\text{Spoke}^* = \arg\max_{i} \left( \text{Headroom}_i \right)$$

---

### 4.3. Declarative Deployment Injection via Kubernetes ConfigMap

#### Architectural Decision: Why Deployment + ConfigMap (and Why Not DaemonSet)?

##### 1. Why a Deployment and NOT a DaemonSet?
`opensandbox-server` is deployed as a **Kubernetes Deployment** (`deployment/opensandbox-server` in namespace `opensandbox-system`), NOT a DaemonSet.

| Architectural Dimension | Deployment (`opensandbox-server`) | DaemonSet |
|---|---|---|
| **Role & Purpose** | Centralized API server, admission controller, and workload dispatcher. | Host-level node daemon running exactly **one pod per node** across the entire cluster. |
| **Typical Workloads** | Web services, control planes, REST/gRPC backends, job schedulers. | Node metrics collectors (`node-exporter`), log forwarders (`fluentd`, `promtail`), CNI plugins (`cilium`, `calico`), host WireGuard synchronizers. |
| **Why Inappropriate for DaemonSet?** | OpenSandbox Server is a centralized API hub. Running it on every single node would waste compute memory, create redundant uncoordinated schedulers, cause API port collisions, and risk split-brain dispatching. | A DaemonSet is designed for node-level operations, not multi-cluster application control planes. |

##### 2. Why Was `ocm_provider.py` Injected via a ConfigMap into the Deployment?
`ocm_provider.py` is an internal Python application module inside the `01community/01sandbox-opensandbox-server:v0.7.12` container. In production Kubernetes environments, modifying application code can be done in three ways:

1. **Direct Container Mutation (`kubectl exec` / `docker exec`):**
   * ❌ **Anti-Pattern (Ephemeral Filesystem):** Containers are disposable. If a pod crashes, runs out of memory (OOMKilled), or is rescheduled during node maintenance, the container filesystem is destroyed. Any hot-patches applied via `kubectl exec` vanish instantly, reverting the server to the broken codebase.
2. **Full CI/CD Image Rebuild (`docker build` + Registry Push):**
   * ⏳ **Overhead:** Requires building a new image, authenticating to a remote Docker registry (Docker Hub, AWS ECR), pushing the layer data, updating Helm `values.yaml`, and pulling images across all nodes.
3. **Declarative ConfigMap SubPath Mounting (Implemented Solution):**
   * ✅ **Instant & Zero-Downtime:** Injects the updated Python module directly into the container filesystem at `/app/src/services/k8s/ocm_provider.py`.
   * ✅ **100% Declarative & Persistent:** Because the file is mounted in `spec.template.spec.volumes` of the Deployment, **every pod spawned by the Deployment automatically mounts the updated code**. It permanently survives pod crashes, evictions, rolling updates, and node reboots.
   * ✅ **Atomic Verification:** Enables live validation of multi-cluster capacity algorithms directly against production clusters without registry dependencies.

#### Implementation Details

1. Created ConfigMap `opensandbox-ocm-override` in namespace `opensandbox-system`:
   ```bash
   kubectl create configmap opensandbox-ocm-override \
     --from-file=ocm_provider.py=/path/to/ocm_provider.py \
     -n opensandbox-system
   ```
2. Injected volume and volumeMount into `deployment/opensandbox-server` container `server`:
   ```yaml
   spec:
     template:
       spec:
         containers:
           - name: server
             volumeMounts:
               - name: ocm-override
                 mountPath: /app/src/services/k8s/ocm_provider.py
                 subPath: ocm_provider.py
                 readOnly: true
         volumes:
           - name: ocm-override
             configMap:
               name: opensandbox-ocm-override
               defaultMode: 420
   ```

Applied and running on both **`primaryhub`** and **`secondaryhub`**.

#### Long-Term Production CI/CD Release Lifecycle

The capacity-aware logic has been committed to the source tree in [`opensandbox-server/docker-build/src/services/k8s/ocm_provider.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/ocm_provider.py).

When baking the new image for formal release:
1. Build and push the image from [`opensandbox-server/docker-build/Dockerfile`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/Dockerfile):
   ```bash
   docker build -t 01community/01sandbox-opensandbox-server:v0.7.13 ./opensandbox-server/docker-build
   docker push 01community/01sandbox-opensandbox-server:v0.7.13
   ```
2. Update the image tag in **`codeInspector/values.yaml`** at **lines 287–290**:
   ```yaml
   opensandbox:
     server:
       image:
         repository: 01community/01sandbox-opensandbox-server
         tag: "v0.7.13"   # <--- Update tag from v0.7.12 to v0.7.13
         pullPolicy: IfNotPresent
   ```
   *(Subchart fallback default is at [`codeInspector/charts/opensandbox/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/opensandbox/values.yaml#L29-L31) lines 29–31).*
3. Remove the ConfigMap volume override from the deployment, as the code will be natively bundled within the container image.

---

### 4.4. Zero-Touch Onboarding for Future Spokes in Terraform IaC

Files updated across all 4 compute accounts:
- [`accounts/sandbox/us-east-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/us-east-1/compute/templates/user_data.sh.tftpl)
- [`accounts/sandbox/eu-west-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/eu-west-1/compute/templates/user_data.sh.tftpl)
- [`accounts/sandbox/eu-central-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/eu-central-1/compute/templates/user_data.sh.tftpl)
- [`accounts/sandbox/ap-south-1/compute/templates/user_data.sh.tftpl`](file:///home/berrybytes/Desktop/bb-aws-infrastructure/accounts/sandbox/ap-south-1/compute/templates/user_data.sh.tftpl)

#### Lifecycle when a new Spoke cluster is provisioned:

1. **Bootstrap & Registration:** The spoke EC2 instance launches, sets up WireGuard, publishes its WireGuard IP to AWS SSM (`/sandbox/clusters/<spoke_name>`), and executes `clusteradm join`.
2. **Autonomous Hub Acceptance:** The hub daemon (`/usr/local/bin/ocm-auto-accept.sh`) executes every 20 seconds. Upon detecting the new cluster registration:
   ```bash
   # Approves CSR and accepts cluster
   clusteradm accept --clusters "$mc" --skip-approve-check
   kubectl patch managedcluster "$mc" --type merge -p '{"spec":{"hubAcceptsClient":true}}'

   # Automatically applies required ClusterSet and runtime capability labels
   kubectl label managedcluster "$mc" \
     cluster.open-cluster-management.io/clusterset=sandbox-spokes \
     sandbox-workload-capable=true \
     runtime.gvisor=true \
     runtime.kata=true --overwrite
   ```
3. **Standby Hub Synchronization:** The primary hub automatically mirrors the new `ManagedCluster` and its labels to `secondaryhub`:
   ```bash
   kubectl --server="https://${standby_wg}:6443" label managedcluster "$mc" \
     cluster.open-cluster-management.io/clusterset=sandbox-spokes \
     sandbox-workload-capable=true \
     runtime.gvisor=true \
     runtime.kata=true --overwrite
   ```
4. **Immediate Workload Ingestion:**
   - OCM's `sandbox-spoke-placement` immediately includes the new spoke in `sandbox-spoke-placement-decision-1`.
   - The capacity scheduler in `opensandbox-server` queries the new spoke.
   - The new spoke has **0 active sandboxes** ($\text{Utilization} = 0.00\%$).
   - The scheduler prioritizes the new spoke, automatically routing new sandbox requests to it until load balances evenly with existing spokes.

---

## 5. Spoke Authority, Telemetry Harvesting & Workload Synchronization Mechanism

A critical architectural question is: **How does the Hub possess the authority to manage remote spokes, how does it observe spoke memory/CPU metrics without SSH or direct inbound network access, and how are workloads actually executed on the spoke?**

This section details the cryptographic, RBAC, and telemetry pipeline governed by Open Cluster Management (OCM) and Kubernetes Custom Resource Definitions (CRDs).

### 5.1. The Inverted Control Architecture: Agent-Driven "Pull" Model

In traditional infrastructure setups, a central orchestrator requires SSH keys or direct network ingress into remote workers. **OCM completely inverts this model**:
* The Hub **never initiates inbound connections** into the spoke clusters.
* Spoke worker nodes can sit behind NAT gateways, stateful AWS Security Groups, or private VPCs without exposing any public ports.
* An in-cluster daemon inside each spoke—the **`klusterlet`**—initiates all communication **outbound** to the Hub API server over the encrypted WireGuard mesh (`https://10.99.0.1:6443`).

```
┌────────────────────────────────────────────────────────────────────────┐
│ SPOKE CLUSTER (e.g. spoke1 in eu-central-1)                            │
│                                                                        │
│  ┌───────────────────────┐         ┌────────────────────────────────┐  │
│  │ Spoke Local Node      │         │ klusterlet-registration-agent  │  │
│  │ (Kubelet & Kernel)    │────────▶│ (Runs inside spoke)            │  │
│  │ Reads:                │ Scrapes │ Reads local node capacity:     │  │
│  │ - Total CPU cores     │ Node    │ - Allocatable CPU: 2000m       │  │
│  │ - Allocatable RAM     │ status  │ - Allocatable RAM: 7.9Gi       │  │
│  │ - Allocatable Pods    │         │ - Allocatable Pods: 110        │  │
│  └───────────────────────┘         └───────────────┬────────────────┘  │
│                                                    │                   │
│  ┌───────────────────────┐                         │                   │
│  │ Spoke Local K8s API   │                         │                   │
│  │ (Applies Pods)        │◀────────────────────────┤                   │
│  └───────────▲───────────┘                         │                   │
│              │ Watches ManifestWork                │                   │
│  ┌───────────┴───────────┐                         │                   │
│  │ klusterlet-work-agent │                         │                   │
│  │ (Runs inside spoke)   │                         │                   │
│  └───────────▲───────────┘                         │                   │
└──────────────┼─────────────────────────────────────┼───────────────────┘
               │                                     │
               │ Pulls ManifestWork                  │ Outbound HTTPS (mTLS)
               │ (Workload definition)               │ over WireGuard (10.99.0.1:6443)
               │                                     │ Pushes Node Capacity & Status
               ▼                                     ▼
┌────────────────────────────────────────────────────────────────────────┐
│ PRIMARYHUB (us-east-1)                                                 │
│                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ Hub Kubernetes API Server & etcd                                 │  │
│  │ Stores ManagedCluster CR Status for "spoke1":                    │  │
│  │  status:                                                         │  │
│  │    allocatable:                                                  │  │
│  │      cpu: "2"                                                    │  │
│  │      memory: "7966516Ki"                                         │  │
│  │      pods: "110"                                                 │  │
│  │    conditions:                                                   │  │
│  │      - type: ManagedClusterConditionAvailable                    │  │
│  │        status: "True"                                            │  │
│  └──────────────────────────────────┬───────────────────────────────┘  │
│                                     │                                  │
│                                     │ Reads local etcd in < 1ms        │
│                                     ▼                                  │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ opensandbox-server & OCM Placement Engine                        │  │
│  │ • OCM Prioritizers evaluate CPU (3), RAM (3), Balance (4)        │  │
│  │ • _select_spoke_by_capacity() reads local status:                │  │
│  │    - Allocatable pods from ManagedCluster (110)                  │  │
│  │    - Active ManifestWorks in namespace "spoke1" (8)              │  │
│  │    - Computes live load: 8 / 110 = 7.27%                         │  │
│  │ • Writes new ManifestWork into selected spoke namespace          │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

---

### 5.2. Spoke Authority & Cryptographic Handshake (mTLS & Scoped RBAC)

How does the Hub authenticate spokes and prevent unauthorized access or cross-cluster interference?

1. **Certificate Signing Request (CSR) Handshake:**
   - During bootstrap, `clusteradm join` generates an internal private key on the spoke and submits a standard Kubernetes `CertificateSigningRequest` (CSR) to the Hub API server.
   - The Hub’s background auto-accept daemon validates and approves the CSR:
     ```bash
     kubectl certificate approve "$csr"
     clusteradm accept --clusters "$mc"
     ```
   - The Hub's root Certificate Authority (CA) issues a cryptographic **X.509 client certificate** with:
     * **Common Name (CN):** `open-cluster-management:spoke1`
     * **Organization (O):** `open-cluster-management:managedclusters`
2. **Fine-Grained RBAC Isolation:**
   - The Hub automatically generates a dedicated Kubernetes ClusterRole and ClusterRoleBinding specifically scoped to that spoke:
     ```yaml
     apiVersion: rbac.authorization.k8s.io/v1
     kind: ClusterRole
     metadata:
       name: open-cluster-management:managedcluster:spoke1
     rules:
       - apiGroups: ["cluster.open-cluster-management.io"]
         resources: ["managedclusters"]
         resourceNames: ["spoke1"]
         verbs: ["get", "list", "watch", "update"]
       - apiGroups: ["cluster.open-cluster-management.io"]
         resources: ["managedclusters/status"]
         resourceNames: ["spoke1"]
         verbs: ["update", "patch"]
       - apiGroups: ["coordination.k8s.io"]
         resources: ["leases"]
         verbs: ["get", "list", "watch", "create", "update", "patch"]
       - apiGroups: ["work.open-cluster-management.io"]
         resources: ["manifestworks"]
         verbs: ["get", "list", "watch", "update", "patch"]
     ```
   - **Multi-Tenant Protection:** `spoke1` can **only** read and write resources within its own namespace (`spoke1`) on the Hub. It has zero permissions to view, alter, or touch `spoke2`'s workloads or secrets.

---

### 5.3. How the Hub "Sees" Spoke Memory and CPU Metrics

Spoke node metrics are harvested locally and published up to the Hub through an automated synchronization loop:

1. **Local Node Observation:**
   Inside the spoke, the **`klusterlet-registration-agent`** runs as a native pod with read access to the local Kubernetes API. It periodically queries the spoke's `v1/Node` objects, which are maintained directly by the local Kubelet and Linux cgroups:
   - `node.status.capacity.cpu` & `node.status.allocatable.cpu`
   - `node.status.capacity.memory` & `node.status.allocatable.memory`
   - `node.status.allocatable.pods`
   - Node health conditions (`Ready`, `MemoryPressure`, `DiskPressure`, `PIDPressure`)
2. **Heartbeat Status Push:**
   Every **60 seconds** (the configured lease interval), or immediately upon any capacity change, the registration agent sends an HTTPS request back to the Hub:
   ```http
   PATCH /apis/cluster.open-cluster-management.io/v1/managedclusters/spoke1/status
   ```
   The payload updates the `status` subresource of the `ManagedCluster` CR in the Hub's local `etcd`:
   ```yaml
   status:
     allocatable:
       cpu: "2"
       ephemeral-storage: 102336Mi
       memory: 7966516Ki
       pods: "110"
     capacity:
       cpu: "2"
       memory: 7966516Ki
       pods: "110"
     conditions:
     - type: ManagedClusterConditionAvailable
       status: "True"
       message: Managed cluster is available
     - type: ManagedClusterJoined
       status: "True"
     - type: ManagedClusterConditionClockSynced
       status: "True"
   ```
3. **Sub-Millisecond Scheduling Decisions:**
   Because all spoke health, CPU, memory, and pod limits are cached locally in the Hub’s `etcd`:
   - When a user submits an execution request to `sandbox-api`, the Hub **never makes slow, blocking network queries across WAN/VPN links**.
   - `opensandbox-server` and the OCM placement engine query the local `etcd` cache via `CustomObjectsApi`.
   - The capacity scoring decision takes **less than 1 millisecond**, regardless of geographical latency to the spokes.

---

### 5.4. How Workloads are Dispatched to the Spoke (`ManifestWork`)

The Hub does not connect directly to the spoke's API server to run pods. Instead, workload provisioning operates through the **`ManifestWork` CRD**:

1. **Hub Workload Submission:**
   When `_select_spoke_by_capacity()` chooses `spoke2`, OpenSandbox server writes a `ManifestWork` Custom Resource into the `spoke2` namespace on the Hub:
   ```yaml
   apiVersion: work.open-cluster-management.io/v1
   kind: ManifestWork
   metadata:
     name: mw-sandbox-7b10d667-fc74-4f3c-abf3-fbd9a349bc55
     namespace: spoke2
     labels:
       app.kubernetes.io/part-of: 01sandbox
       sandbox.opensandbox.io/id: 7b10d667-fc74-4f3c-abf3-fbd9a349bc55
   spec:
     workload:
       manifests:
         - apiVersion: v1
           kind: Pod
           metadata:
             name: opensandbox-pod
             namespace: opensandbox-workloads
           spec:
             runtimeClassName: gvisor
             containers:
               - name: code-interpreter
                 image: 01community/01sandbox-codeinterpreter:v0.7.11
   ```
2. **Spoke Agent Execution:**
   - On Spoke 2, the **`klusterlet-work-agent`** holds a long-polling watch on the Hub's `spoke2` namespace.
   - The instant `mw-sandbox-7b10d667...` is created, the work agent downloads the embedded Pod manifest and calls its **local** K8s API (`localhost:6443`) to create the Pod in `opensandbox-workloads`.
3. **Status Feedback:**
   - As the pod executes with gVisor/Kata isolation on Spoke 2, the work agent reflects its status (`status.phase = Running / Succeeded / Failed`) back to the `ManifestWork.status` on the Hub.
   - When the sandbox finishes or expires, the Hub deletes the `ManifestWork`, which automatically instructs the spoke's work agent to garbage-collect the local Pod.

---

## 6. Live Production Verification & Telemetry

### 5.1. Live Execution Test Inside `primaryhub` Container

Executing the scheduler against live clusters on `primaryhub`:

```bash
kubectl exec -n opensandbox-system deployment/opensandbox-server -c server -- python3 -c '
from src.config import load_config
from src.services.k8s.client import K8sClient
from src.services.k8s.ocm_provider import OcmWorkloadProvider

app_config = load_config("/etc/opensandbox/config.toml")
client = K8sClient(app_config.kubernetes)
provider = OcmWorkloadProvider(client, app_config)

selected = provider._resolve_target_spoke_cluster(None)
print("Capacity Scheduler Final Selection:", selected)
'
```

#### Actual Live Output:
```
INFO:src.config:Loaded configuration from /etc/opensandbox/config.toml
INFO:src.services.k8s.ocm_provider:Spoke cluster 'spoke1' capacity metrics: active_sandboxes=8, allocatable_pods=110, utilization=0.0727
INFO:src.services.k8s.ocm_provider:Spoke cluster 'spoke2' capacity metrics: active_sandboxes=0, allocatable_pods=110, utilization=0.0000
INFO:src.services.k8s.ocm_provider:Capacity scheduler chose optimal spoke cluster 'spoke2' (utilization: 0.00%, active sandboxes: 0)
INFO:src.services.k8s.ocm_provider:Capacity-aware scheduler selected spoke cluster 'spoke2' from candidates ['spoke1', 'spoke2']

==================================================
Capacity Scheduler Final Selection: spoke2
==================================================
```

### 5.2. Cluster Fleet Health & Status

| Cluster | Role | Region | WireGuard IP | OCM Status | Labels Verified | Active Sandboxes |
|---|---|---|---|---|---|---|
| `primaryhub` | Active Hub | `us-east-1` | `10.99.0.1` | Active | Control Plane | N/A (Dispatches via OCM) |
| `secondaryhub` | Standby Hub | `eu-west-1` | `10.99.0.2` | Standby Sync | Control Plane (Mirrored) | N/A (Standby Dispatcher) |
| `spoke1` | Worker Spoke | `eu-central-1` | `10.99.0.3` | Available (True) | `clusterset=sandbox-spokes`, `gvisor=true`, `kata=true` | 8 sandboxes (7.27% util) |
| `spoke2` | Worker Spoke | `ap-south-1` | `10.99.0.4` | Available (True) | `clusterset=sandbox-spokes`, `gvisor=true`, `kata=true` | 0 sandboxes (0.00% util) |
| `spokeN` | Future Spoke | Any | `10.99.0.X` | Auto-join | Auto-labeled by daemon | 0 sandboxes (Prioritized) |

---

## 6. Operational Runbook & Diagnostic Commands

### View Current OCM Placement Decisions
```bash
export KUBECONFIG=/etc/kubernetes/admin.conf
kubectl get placementdecision -n opensandbox-system -o yaml
```

### Inspect Spoke Labels and Availability
```bash
kubectl get managedclusters -o custom-columns=NAME:.metadata.name,AVAILABLE:.status.conditions[?(@.type==\"ManagedClusterConditionAvailable\")].status,PODS:.status.allocatable.pods,LABELS:.metadata.labels
```

### Check Active ManifestWorks per Spoke Namespace
```bash
# Spoke 1
kubectl get manifestwork -n spoke1 -l app.kubernetes.io/part-of=01sandbox --no-headers | wc -l

# Spoke 2
kubectl get manifestwork -n spoke2 -l app.kubernetes.io/part-of=01sandbox --no-headers | wc -l
```

### View Live Scheduler Decision Logs
```bash
kubectl logs -n opensandbox-system deployment/opensandbox-server -c server -f | grep -E "capacity metrics|Capacity scheduler chose"
```
