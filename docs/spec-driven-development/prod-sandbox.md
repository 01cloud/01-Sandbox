# SPEC-MC-2026-002: OCM Multi-Cluster Production Sandbox Architecture & Migration Specification

| Metadata | Value |
|---|---|
| **Spec ID** | `SPEC-MC-2026-002` |
| **Title** | Open Cluster Management (OCM) Multi-Cluster Control-Spoke Sandbox Architecture |
| **Version** | `1.0.0` |
| **Status** | `PROPOSED / SDD` |
| **Author** | Antigravity AI & 01Sandbox Infrastructure Team |
| **Target Scope** | `01-Sandbox` (`apiServer`, `opensandbox-server`, `opensandbox-controller`, `OCM`) |
| **Last Updated** | 2026-09-09 |

---

## 1. Problem Statement & Architecture Goals

### Current Bottlenecks (Single-Cluster)
Currently, all components—including Control Plane APIs (`apiServer`), databases (`PostgreSQL`, `Redis`), message queue (`RabbitMQ`), proxy (`AgentGateway`), control servers (`opensandbox-server`, `opensandbox-controller`), storage (`PVC`), and untrusted sandbox execution pods (`code-interpreter` under `gVisor`/`Kata`) run inside a **single Kubernetes cluster**.

Under high concurrent loads, heavy AST parsing and untrusted code execution exhaust node CPU/RAM, causing API gateway timeouts, database connection pool exhaustion, and cluster-wide pod crashes.

### Target Multi-Cluster Topology (Hub-and-Spoke via OCM)
To prevent server overload and isolate control plane operations from execution workloads:
1. **Hub Clusters (`primaryhub` & `secondaryhub`):** Host all persistent control plane services (`apiServer`, `PostgreSQL`, `Redis`, `RabbitMQ`, `AgentGateway`, `opensandbox-server`, `opensandbox-controller`, persistent storage PVCs).
2. **Spoke Clusters (`spoke-us-east-1`, `spoke-eu-central-1`, etc.):** Dedicated worker clusters configured with runtime security (`gVisor` and `Kata Containers`) that **only** run ephemeral sandbox workload pods.
3. **OCM (Open Cluster Management) Orchestration:** The Hub uses OCM `Placement` and `ManifestWork` CRDs to dynamically schedule and deliver sandbox pod execution specs to Spoke worker clusters.

---

## 2. End-to-End Multi-Cluster System Architecture

```
                                  [ User / External API Clients ]
                                                │
                                                ▼ (HTTPS Request)
                                ┌────────────────────────────────┐
                                │ AgentGateway (Envoy Ingress)   │
                                └───────────────┬────────────────┘
                                                │
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ HUB CLUSTER (primaryhub / secondaryhub) - CONTROL PLANE ONLY                                           │
│                                                                                                         │
│ ┌───────────────────────┐    ┌────────────────────────┐    ┌────────────────────────────────────────┐  │
│ │ apiServer (FastAPI)   │───►│ PostgreSQL / Redis     │    │ RabbitMQ Message Broker                │  │
│ └──────────┬────────────┘    └────────────────────────┘    └────────────────────────────────────────┘  │
│            │                                                                                            │
│            ▼ HTTP POST /sandboxes                                                                       │
│ ┌────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│ │ opensandbox-server (OCM-Enabled Workload Scheduler)                                                │  │
│ │  • Resolves Target Spoke via OCM Placement Engine                                                  │  │
│ │  • Generates OCM ManifestWork Spec                                                                 │  │
│ └──────────┬─────────────────────────────────────────────────────────────────────────────────────────┘  │
│            │                                                                                            │
│            ▼ Submits ManifestWork to Hub K8s API                                                        │
│ ┌────────────────────────────────────────────────────────────────────────────────────────────────────┐  │
│ │ OCM Hub Control Plane (work.open-cluster-management.io)                                             │  │
│ │  • ManagedClusterSet: sandbox-spokes                                                               │  │
│ │  • Placement: select-healthy-spoke                                                                 │  │
│ └──────────┬─────────────────────────────────────────────────┬──────────────────────────────────────┘  │
└────────────┼─────────────────────────────────────────────────┼──────────────────────────────────────────┘
             │ OCM Klusterlet ManifestWork Sync                │ OCM Klusterlet ManifestWork Sync
             ▼                                                 ▼
┌─────────────────────────────────────────┐       ┌─────────────────────────────────────────┐
│ SPOKE CLUSTER 1 (spoke-us-east-1)       │       │ SPOKE CLUSTER 2 (spoke-eu-central-1)    │
│ (WORKLOAD ONLY)                         │       │ (WORKLOAD ONLY)                         │
│                                         │       │                                         │
│ ┌─────────────────────────────────────┐ │       │ ┌─────────────────────────────────────┐ │
│ │ Sandbox Pod 1 (gVisor runsc)        │ │       │ │ Sandbox Pod 3 (Kata Firecracker)    │ │
│ └─────────────────────────────────────┘ │       │ └─────────────────────────────────────┘ │
│ ┌─────────────────────────────────────┐ │       │ ┌─────────────────────────────────────┐ │
│ │ Sandbox Pod 2 (Kata Firecracker)    │ │       │ │ Sandbox Pod 4 (gVisor runsc)        │ │
│ └─────────────────────────────────────┘ │       │ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘       └─────────────────────────────────────────┘
```

---

## 3. Required Code Changes in 01Sandbox Components

### 3.1 Changes in `opensandbox-server`

#### A. Add OCM Workload Provider (`src/services/k8s/ocm_provider.py`)
Create a new OCM Workload Provider alongside `batchsandbox_provider.py`. Instead of directly creating a `Pod` on the local Kubernetes API, `OcmWorkloadProvider` wraps the Pod specification into an OCM `ManifestWork` custom resource.

```python
# opensandbox-server/docker-build/src/services/k8s/ocm_provider.py

import logging
from typing import Any, Dict, Optional
from kubernetes import client
from src.services.k8s.workload_provider import WorkloadProvider

logger = logging.getLogger(__name__)

class OcmWorkloadProvider(WorkloadProvider):
    """
    OCM-based Workload Provider.
    Dispatches Sandbox Pods to Spoke Clusters by creating OCM ManifestWork resources.
    """

    def __init__(self, k8s_client: client.CustomObjectsApi, placement_name: str = "sandbox-spoke-placement"):
        self.custom_api = k8s_client
        self.placement_name = placement_name

    def create_workload(
        self,
        sandbox_id: str,
        namespace: str,
        image_spec: Any,
        entrypoint: Any,
        env: Dict[str, str],
        resource_limits: Dict[str, Any],
        labels: Dict[str, str],
        expires_at: Any = None,
        execd_image: str = "",
        extensions: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        # 1. Resolve target spoke cluster from OCM Placement or request metadata
        target_cluster = self._resolve_target_spoke_cluster(extensions)

        # 2. Build the Sandbox Pod Specification
        pod_manifest = self._build_sandbox_pod_manifest(
            sandbox_id=sandbox_id,
            image_spec=image_spec,
            entrypoint=entrypoint,
            env=env,
            resource_limits=resource_limits,
            labels=labels,
            execd_image=execd_image,
            extensions=extensions,
        )

        # 3. Wrap Pod inside OCM ManifestWork Custom Resource
        manifest_work_name = f"mw-sandbox-{sandbox_id}"
        manifest_work = {
            "apiVersion": "work.open-cluster-management.io/v1",
            "kind": "ManifestWork",
            "metadata": {
                "name": manifest_work_name,
                "namespace": target_cluster,  # OCM schedules to spoke via cluster namespace
                "labels": {
                    "sandbox.opensandbox.io/id": sandbox_id,
                    "app.kubernetes.io/part-of": "01sandbox",
                },
            },
            "spec": {
                "workload": {
                    "manifests": [pod_manifest]
                }
            },
        }

        # 4. Submit ManifestWork to Hub Cluster K8s API
        logger.info("Submitting OCM ManifestWork %s targeting spoke cluster: %s", manifest_work_name, target_cluster)
        self.custom_api.create_namespaced_custom_object(
            group="work.open-cluster-management.io",
            version="v1",
            namespace=target_cluster,
            plural="manifestworks",
            body=manifest_work,
        )

        return {
            "name": manifest_work_name,
            "sandbox_id": sandbox_id,
            "spoke_cluster": target_cluster,
            "status": "Pending",
        }

    def _resolve_target_spoke_cluster(self, extensions: Optional[Dict[str, Any]]) -> str:
        if extensions and "target_cluster" in extensions:
            return extensions["target_cluster"]
        # Default fallback to first active spoke cluster in placement
        return "spoke-us-east-1"
```

#### B. Update Workload Provider Factory (`src/services/k8s/provider_factory.py`)
Update `provider_factory.py` to support `workload_provider = "ocm"` from `config.toml`.

```python
# opensandbox-server/docker-build/src/services/k8s/provider_factory.py

def create_workload_provider(provider_type: str, config: AppConfig) -> WorkloadProvider:
    if provider_type == "batchsandbox":
        return BatchSandboxProvider(config)
    elif provider_type == "ocm":
        from src.services.k8s.ocm_provider import OcmWorkloadProvider
        return OcmWorkloadProvider(get_k8s_custom_client(), placement_name=config.kubernetes.placement_name)
    else:
        raise ValueError(f"Unsupported workload_provider: {provider_type}")
```

---

### 3.2 Changes in `opensandbox-controller`

Update `opensandbox-controller` to reconcile `Pool` pre-warmed pod resources across multiple spoke clusters using OCM `Placement`:

1. Read `ManagedClusterSet` (`sandbox-spokes`).
2. When maintaining warm pod buffers (`bufferMin`), create pre-warmed `ManifestWork` pod templates allocated proportionally across active spoke clusters.

---

### 3.3 Changes in `apiServer`

Add region / target cluster extension support in [`apiServer/fastapi/sandboxes/models.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/sandboxes/models.py):

```python
class CreateSandboxRequest(BaseModel):
    image: ImageSpec
    entrypoint: Optional[list[str]] = None
    env: Optional[dict[str, str]] = None
    timeout: Optional[int] = 600
    extensions: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional extension parameters, e.g. {'target_cluster': 'spoke-us-east-1', 'secure_runtime': 'gvisor'}"
    )
```

---

## 4. OCM Production Infrastructure Manifests

Instead of standard single-cluster K8s manifests, the multi-cluster infrastructure uses standard **Open Cluster Management (OCM)** Custom Resources:

### 4.1 OCM `ManagedClusterSet` (Hub Definition)
Defines the cluster pool dedicated for sandbox workloads on the Hub.

```yaml
# manifests/ocm/01-managed-cluster-set.yaml
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: ManagedClusterSet
metadata:
  name: sandbox-spokes
```

---

### 4.2 OCM `ManagedClusterSetBinding` (Hub Namespace Access)
Binds the `sandbox-spokes` cluster set to the `opensandbox` namespace on the Hub cluster.

```yaml
# manifests/ocm/02-managed-cluster-set-binding.yaml
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: ManagedClusterSetBinding
metadata:
  name: sandbox-spokes
  namespace: opensandbox
spec:
  clusterSet: sandbox-spokes
```

---

### 4.3 OCM `Placement` Engine (Spoke Dynamic Selection)
Dynamically selects healthy spoke clusters that support runtime security (`gvisor` and `kata`).

```yaml
# manifests/ocm/03-spoke-placement.yaml
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: Placement
metadata:
  name: sandbox-spoke-placement
  namespace: opensandbox
spec:
  clusterSets:
    - sandbox-spokes
  numberOfClusters: 1
  predicates:
    - requiredClusterSelector:
        labelSelector:
          matchLabels:
            sandbox-workload-capable: "true"
            runtime.gvisor: "true"
            runtime.kata: "true"
```

---

### 4.4 OCM `ManifestWork` (Hub Control Plane Deployment)
OCM manifest to deploy the Control Plane components (`apiServer`, `RabbitMQ`, `PostgreSQL`, `Redis`, `AgentGateway`, `opensandbox-server`, `opensandbox-controller`) onto the Hub clusters (`primaryhub` and `secondaryhub`).

```yaml
# manifests/ocm/04-hub-control-plane-manifestwork.yaml
apiVersion: work.open-cluster-management.io/v1
kind: ManifestWork
metadata:
  name: deploy-01sandbox-control-plane
  namespace: primaryhub
spec:
  workload:
    manifests:
      # 1. Namespace
      - apiVersion: v1
        kind: Namespace
        metadata:
          name: opensandbox
      # 2. Redis Deployment
      - apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: redis
          namespace: opensandbox
        spec:
          replicas: 1
          selector:
            matchLabels:
              app: redis
          template:
            metadata:
              labels:
                app: redis
            spec:
              containers:
                - name: redis
                  image: redis:7-alpine
                  ports:
                    - containerPort: 6379
      # 3. RabbitMQ Deployment
      - apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: rabbitmq
          namespace: opensandbox
        spec:
          replicas: 1
          selector:
            matchLabels:
              app: rabbitmq
          template:
            metadata:
              labels:
                app: rabbitmq
            spec:
              containers:
                - name: rabbitmq
                  image: rabbitmq:3-management-alpine
                  ports:
                    - containerPort: 5672
                    - containerPort: 15672
      # 4. opensandbox-server Deployment
      - apiVersion: apps/v1
        kind: Deployment
        metadata:
          name: opensandbox-server
          namespace: opensandbox
        spec:
          replicas: 2
          selector:
            matchLabels:
              app: opensandbox-server
          template:
            metadata:
              labels:
                app: opensandbox-server
            spec:
              containers:
                - name: opensandbox-server
                  image: sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/server:v0.1.7
                  ports:
                    - containerPort: 80
                  env:
                    - name: WORKLOAD_PROVIDER
                      value: "ocm"
```

---

### 4.5 OCM `ManifestWork` (Dynamic Sandbox Pod Spoke Provisioning)
This is the **exact OCM ManifestWork** generated programmatically by `opensandbox-server` on the Hub to dispatch a sandbox execution pod to `spoke-us-east-1`.

```yaml
# Example Generated ManifestWork created by opensandbox-server
apiVersion: work.open-cluster-management.io/v1
kind: ManifestWork
metadata:
  name: mw-sandbox-a1b2c3d4-5678-90ab
  namespace: spoke-us-east-1    # Target OCM ManagedCluster namespace on Hub
  labels:
    sandbox.opensandbox.io/id: "a1b2c3d4-5678-90ab"
    app.kubernetes.io/part-of: "01sandbox"
spec:
  workload:
    manifests:
      - apiVersion: v1
        kind: Pod
        metadata:
          name: sbx-a1b2c3d4
          namespace: opensandbox-workloads
          labels:
            sandbox.opensandbox.io/id: "a1b2c3d4-5678-90ab"
        spec:
          runtimeClassName: gvisor   # Enables gVisor runsc isolation on Spoke
          initContainers:
            - name: execd-init
              image: sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/execd:v1.0.7
              command: ["/bin/sh", "-c", "cp /usr/local/bin/execd /opensandbox-bin/execd"]
              volumeMounts:
                - name: opensandbox-bin
                  mountPath: /opensandbox-bin
          containers:
            - name: code-interpreter
              image: 01community/01sandbox-codeinterpreter:1.0.0
              ports:
                - containerPort: 44772
                - containerPort: 54321
              resources:
                limits:
                  cpu: "2"
                  memory: 4Gi
                requests:
                  cpu: "500m"
                  memory: 1Gi
              volumeMounts:
                - name: opensandbox-bin
                  mountPath: /opt/opensandbox/bin
          volumes:
            - name: opensandbox-bin
              emptyDir:
                sizeLimit: 1Gi
```

---

## 5. Summary of Multi-Cluster Migration Steps

1. **Configure OCM Managed Clusters:** Register spoke clusters (`spoke-us-east-1`, `spoke-eu-central-1`) to `primaryhub` and `secondaryhub` using `klusterlet`.
2. **Install Runtime Security on Spokes:** Ensure `gVisor` (`runsc`) and `Kata Containers` (`kata-fc`) `RuntimeClass` resources are installed on all spoke nodes.
3. **Deploy Control Plane to Hubs:** Apply `01-managed-cluster-set.yaml`, `02-managed-cluster-set-binding.yaml`, `03-spoke-placement.yaml`, and `04-hub-control-plane-manifestwork.yaml` to deploy `apiServer`, PostgreSQL, Redis, RabbitMQ, AgentGateway, and `opensandbox-server` on the Hub.
4. **Deploy OCM Provider in `opensandbox-server`:** Set `workload_provider = "ocm"` in `opensandbox-server`'s `config.toml` to automatically wrap and dispatch sandbox execution pods via OCM `ManifestWork` to spoke worker clusters.
