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

- **`ResourceAllocatableCPU` (weight: 3):** Scores clusters higher if they have greater unallocated CPU cores.
- **`ResourceAllocatableMemory` (weight: 3):** Scores clusters higher if they have more allocatable RAM.
- **`Balance` (weight: 4):** Penalizes clusters that already host high workload volumes, ensuring even distribution across the entire multi-cluster fleet.

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

To guarantee that the modified `ocm_provider.py` remains persistently loaded across pod evictions, deployments, node restarts, and crashes, the code was deployed declaratively via a Kubernetes `ConfigMap` subPath mount:

1. Created ConfigMap `opensandbox-ocm-override` in namespace `opensandbox-system`:
   ```bash
   kubectl create configmap opensandbox-ocm-override \
     --from-file=ocm_provider.py=/path/to/ocm_provider.py \
     -n opensandbox-system
   ```
2. Injected volume and volumeMount into `deployment/opensandbox-server`:
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

## 5. Live Production Verification & Telemetry

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
