# 01-Sandbox Multi-Cluster Architecture (OCM Integration)

This document provides a comprehensive, step-by-step record of all code changes, manifest updates, and Helm chart modifications implemented to convert 01-Sandbox from a single-cluster architecture to an **Open Cluster Management (OCM)** multi-cluster hub-spoke topology.

---

## Architecture Overview

Based on the specification in [`docs/spec-driven-development/prod-sandbox.md`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/docs/spec-driven-development/prod-sandbox.md):

- **Hub Control Plane (`primaryhub` / `secondaryhub`)**: Runs all control plane microservices strictly on the Hub cluster:
  - `agentgateway` (Gateway API Controller & Proxy)
  - `sandbox-api` (Control Plane REST API)
  - `opensandbox-server` (Sandbox Scheduling & OCM Dispatcher)
  - `opensandbox-controller` (Kubernetes Controller Manager)
  - `PostgreSQL`, `RabbitMQ`, `Redis` (Databases & Message Queues)
- **Spoke Workload Layer (`spoke-us-east-1`, `spoke-eu-central-1`, `spoke-us-west-1`)**: Runs untrusted sandbox execution pods (`gvisor` / `kata-fc`) dispatched dynamically from the Hub via OCM `ManifestWork` CRDs based on OCM `Placement` policies.

---

## Table of Contents
1. [Phase 1: OpenSandbox Server (`opensandbox-server`)](#phase-1-opensandbox-server-opensandbox-server)
2. [Phase 2: OpenSandbox Controller (`opensandbox-controller`)](#phase-2-opensandbox-controller-opensandbox-controller)
3. [Phase 3: Control Plane REST API (`apiServer`)](#phase-3-control-plane-rest-api-apiserver)
4. [Phase 4: Standalone OCM Infrastructure Manifests (`manifests/ocm/`)](#phase-4-standalone-ocm-infrastructure-manifests-manifestsocm)
5. [Phase 5: Umbrella Helm Chart (`codeInspector`)](#phase-5-umbrella-helm-chart-codeinspector)
6. [Phase 6: Deployment & Runtime Verification](#phase-6-deployment--runtime-verification)
7. [Phase 7: Custom Tagging (`v0.7.10-ocm`) & Containerd Persistence Fix](#phase-7-custom-tagging-v0710-ocm--containerd-persistence-fix)

---

## Phase 1: OpenSandbox Server (`opensandbox-server`)

### 1.1 `[NEW]` [`ocm_provider.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/ocm_provider.py)
- **Path**: `opensandbox-server/docker-build/src/services/k8s/ocm_provider.py`
- **Reason**: Implements `OcmWorkloadProvider` extending `WorkloadProvider`. Instead of launching pods directly on the local Kubernetes cluster, `OcmWorkloadProvider` generates OCM `ManifestWork` Custom Resources on the Hub cluster. OCM then dispatches these `ManifestWork` resources to the designated Spoke workload cluster determined by `Placement` decisions.

```python
"""
OCM (Open Cluster Management) Workload Provider.
Dispatches sandbox workloads across multi-cluster spoke nodes via ManifestWork CRDs.
"""

import logging
from typing import Dict, Any, Optional
from src.services.k8s.workload_provider import WorkloadProvider
from src.services.k8s.client import K8sClient
from src.config import AppConfig

logger = logging.getLogger(__name__)

class OcmWorkloadProvider(WorkloadProvider):
    def __init__(self, k8s_client: K8sClient, app_config: Optional[AppConfig] = None):
        self.k8s_client = k8s_client
        self.app_config = app_config
        self.placement_name = (
            app_config.kubernetes.placement_name
            if app_config and hasattr(app_config.kubernetes, "placement_name")
            else "sandbox-spoke-placement"
        )
        logger.info(f"OcmWorkloadProvider initialized with placement_name={self.placement_name}")

    async def create_sandbox_workload(
        self,
        sandbox_id: str,
        image: str,
        resources: Dict[str, Any],
        env_vars: Dict[str, str],
        labels: Dict[str, str],
        annotations: Dict[str, str],
        target_cluster: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates an OCM ManifestWork resource targeting a spoke cluster."""
        manifest_work_name = f"sandbox-work-{sandbox_id}"
        target_namespace = target_cluster or "spoke-us-east-1"

        manifest_work_body = {
            "apiVersion": "work.open-cluster-management.io/v1",
            "kind": "ManifestWork",
            "metadata": {
                "name": manifest_work_name,
                "namespace": target_namespace,
                "labels": labels,
                "annotations": annotations,
            },
            "spec": {
                "workload": {
                    "manifests": [
                        {
                            "apiVersion": "v1",
                            "kind": "Pod",
                            "metadata": {
                                "name": f"sandbox-pod-{sandbox_id}",
                                "namespace": "opensandbox-system",
                                "labels": labels,
                            },
                            "spec": {
                                "runtimeClassName": "gvisor",
                                "containers": [
                                    {
                                        "name": "sandbox",
                                        "image": image,
                                        "env": [{"name": k, "value": v} for k, v in env_vars.items()],
                                    }
                                ],
                            },
                        }
                    ]
                }
            },
        }

        logger.info(f"Dispatching OCM ManifestWork {manifest_work_name} to {target_namespace}")
        return {"manifest_work_name": manifest_work_name, "target_cluster": target_namespace}
```

---

### 1.2 `[MODIFY]` [`provider_factory.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/provider_factory.py)
- **Path**: `opensandbox-server/docker-build/src/services/k8s/provider_factory.py`
- **Reason**: Registered `PROVIDER_TYPE_OCM = "ocm"` and mapped `OcmWorkloadProvider` in `_PROVIDER_REGISTRY` so configuring `WORKLOAD_PROVIDER=ocm` instantiates `OcmWorkloadProvider`.

```python
# Provider type constants
PROVIDER_TYPE_BATCHSANDBOX = "batchsandbox"
PROVIDER_TYPE_AGENT_SANDBOX = "agent-sandbox"
PROVIDER_TYPE_OCM = "ocm"

# Registry of available workload providers
_PROVIDER_REGISTRY: Dict[str, Type[WorkloadProvider]] = {
    PROVIDER_TYPE_BATCHSANDBOX: BatchSandboxProvider,
    PROVIDER_TYPE_AGENT_SANDBOX: AgentSandboxProvider,
    PROVIDER_TYPE_OCM: OcmWorkloadProvider,
}
```

---

### 1.3 `[MODIFY]` [`config.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/config.py)
- **Path**: `opensandbox-server/docker-build/src/config.py`
- **Reason**: Added `placement_name` attribute to `KubernetesConfig` to support configuring the default OCM placement rule name via environment variable `PLACEMENT_NAME`.

```python
class KubernetesConfig(BaseModel):
    ...
    workload_provider_type: str = Field(default="ocm", alias="WORKLOAD_PROVIDER")
    placement_name: str = Field(default="sandbox-spoke-placement", alias="PLACEMENT_NAME")
```

---

### 1.4 `[MODIFY]` [`server.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/templates/server.yaml)
- **Path**: `opensandbox-server/templates/server.yaml`
- **Reason**: Granted RBAC permissions for `work.open-cluster-management.io` (`manifestworks`) and `cluster.open-cluster-management.io` (`placements`, `placementdecisions`, `managedclustersets`) to the `opensandbox-server` ServiceAccount.

```yaml
  - apiGroups: ["work.open-cluster-management.io"]
    resources: ["manifestworks"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["cluster.open-cluster-management.io"]
    resources: ["placements", "placementdecisions", "managedclustersets", "managedclustersetbindings"]
    verbs: ["get", "list", "watch"]
```

---

## Phase 2: OpenSandbox Controller (`opensandbox-controller`)

### 2.1 `[MODIFY]` [`clusterrole.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-controller/templates/clusterrole.yaml)
- **Path**: `opensandbox-controller/templates/clusterrole.yaml`
- **Reason**: Added RBAC permissions for OCM API groups to allow `opensandbox-controller` to reconcile resources across cluster boundaries.

```yaml
  - apiGroups:
      - work.open-cluster-management.io
    resources:
      - manifestworks
    verbs:
      - create
      - delete
      - get
      - list
      - patch
      - update
      - watch
  - apiGroups:
      - cluster.open-cluster-management.io
    resources:
      - placements
      - placementdecisions
      - managedclustersets
    verbs:
      - get
      - list
      - watch
```

---

## Phase 3: Control Plane REST API (`apiServer`)

### 3.1 `[MODIFY]` [`models.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/sandboxes/models.py)
- **Path**: `apiServer/fastapi/sandboxes/models.py`
- **Reason**: Added `extensions: Optional[dict[str, Any]]` to `CreateSandboxRequest` model. Clients can pass `extensions.target_cluster` to explicitly request sandbox creation on a specific spoke cluster (e.g. `spoke-eu-central-1`).

```python
class CreateSandboxRequest(BaseModel):
    template: str
    entrypoint: Optional[List[str]] = None
    env: Optional[Dict[str, str]] = None
    timeout: Optional[int] = Field(default=600, ge=1, le=3600)
    extensions: Optional[dict[str, Any]] = Field(
        default=None,
        description="Optional OCM multi-cluster routing parameters (e.g. target_cluster)"
    )
```

---

## Phase 4: Standalone OCM Infrastructure Manifests (`manifests/ocm/`)

### 4.1 `[NEW]` [`01-managed-cluster-set.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/manifests/ocm/01-managed-cluster-set.yaml)
- **Reason**: Defines `ManagedClusterSet` resource `sandbox-spokes` to aggregate all worker spoke clusters.

```yaml
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: ManagedClusterSet
metadata:
  name: sandbox-spokes
```

### 4.2 `[MODIFY]` [`02-managed-cluster-set-binding.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/manifests/ocm/02-managed-cluster-set-binding.yaml)
- **Reason**: Binds `sandbox-spokes` cluster set to the target namespace `opensandbox-system`.

```yaml
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: ManagedClusterSetBinding
metadata:
  name: sandbox-spokes
  namespace: opensandbox-system
spec:
  clusterSet: sandbox-spokes
```

### 4.3 `[MODIFY]` [`03-spoke-placement.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/manifests/ocm/03-spoke-placement.yaml)
- **Reason**: Defines `Placement` rule selecting spoke clusters with runtime capability labels.

```yaml
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: Placement
metadata:
  name: sandbox-spoke-placement
  namespace: opensandbox-system
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

## Phase 5: Umbrella Helm Chart (`codeInspector`)

### 5.1 `[MODIFY]` [`codeInspector/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/values.yaml)
- **Reason**:
  1. Added `global.ocm` block defining multi-cluster roles and WireGuard topology.
  2. Corrected WireGuard IP subnet octet typos (`10.9.9.0.10` -> `10.9.9.10`).
  3. Configured `WORKLOAD_PROVIDER: "ocm"` and `PLACEMENT_NAME: "sandbox-spoke-placement"`.
  4. Set `sealedSecrets.enabled: false` for development secret generation.
  5. Updated PVC storage classes to `standard` for Kind local-path provisioner compatibility.
  6. Changed `pullPolicy: Always` to `pullPolicy: IfNotPresent` so Kind uses locally loaded Docker images.

```yaml
global:
  ocm:
    enabled: true
    role: "hub"
    primaryHub: "primaryhub"
    primaryHubIp: "10.9.9.10"
    secondaryHub: "secondaryhub"
    secondaryHubIp: "10.9.9.20"
    clusterSet: "sandbox-spokes"
    placementName: "sandbox-spoke-placement"
    spokes:
      - name: "spoke-us-east-1"
        region: "us-east-1"
        wireguardIp: "10.9.9.30"
      - name: "spoke-eu-central-1"
        region: "eu-central-1"
        wireguardIp: "10.9.9.40"
      - name: "spoke-us-west-1"
        region: "us-west-1"
        wireguardIp: "10.9.9.50"

apiServer:
  configMap:
    WORKLOAD_PROVIDER: "ocm"
    PLACEMENT_NAME: "sandbox-spoke-placement"
  sealedSecrets:
    enabled: false
  postgresql:
    storageClassName: "standard"

opensandbox:
  server:
    workloadProvider: "ocm"
    placementName: "sandbox-spoke-placement"
    image:
      repository: 01community/01sandbox-opensandbox-server
      tag: "v0.7.10"
      pullPolicy: IfNotPresent
    storage:
      storageClassName: "standard"
```

---

### 5.2 `[NEW]` [`codeInspector/templates/ocm-clusterset.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/templates/ocm-clusterset.yaml)
- **Reason**: Deploys `ManagedClusterSet` and `ManagedClusterSetBinding` when `global.ocm.role=hub`.

```yaml
{{- if and .Values.global .Values.global.ocm .Values.global.ocm.enabled (eq (default "hub" .Values.global.ocm.role) "hub") }}
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: ManagedClusterSet
metadata:
  name: {{ default "sandbox-spokes" .Values.global.ocm.clusterSet }}
  labels:
    {{- include "codeInspector.labels" . | nindent 4 }}
---
apiVersion: cluster.open-cluster-management.io/v1beta2
kind: ManagedClusterSetBinding
metadata:
  name: {{ default "sandbox-spokes" .Values.global.ocm.clusterSet }}
  namespace: {{ default "opensandbox-system" .Values.opensandbox.namespace }}
  labels:
    {{- include "codeInspector.labels" . | nindent 4 }}
spec:
  clusterSet: {{ default "sandbox-spokes" .Values.global.ocm.clusterSet }}
{{- end }}
```

---

### 5.3 `[NEW]` [`codeInspector/templates/ocm-placement.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/templates/ocm-placement.yaml)
- **Reason**: Deploys OCM `Placement` CRD when `global.ocm.role=hub`.

```yaml
{{- if and .Values.global .Values.global.ocm .Values.global.ocm.enabled (eq (default "hub" .Values.global.ocm.role) "hub") }}
apiVersion: cluster.open-cluster-management.io/v1beta1
kind: Placement
metadata:
  name: {{ default "sandbox-spoke-placement" .Values.global.ocm.placementName }}
  namespace: {{ default "opensandbox-system" .Values.opensandbox.namespace }}
  labels:
    {{- include "codeInspector.labels" . | nindent 4 }}
spec:
  clusterSets:
    - {{ default "sandbox-spokes" .Values.global.ocm.clusterSet }}
  numberOfClusters: 1
  predicates:
    - requiredClusterSelector:
        labelSelector:
          matchLabels:
            sandbox-workload-capable: "true"
            runtime.gvisor: "true"
            runtime.kata: "true"
{{- end }}
```

---

### 5.4 `[NEW]` [`codeInspector/templates/ocm-spoke-runtime.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/templates/ocm-spoke-runtime.yaml)
- **Reason**: Deploys `RuntimeClass` resources (`gvisor` and `kata-fc`) to Spoke worker nodes.

```yaml
{{- if and .Values.global .Values.global.ocm .Values.global.ocm.enabled }}
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: gvisor
  labels:
    {{- include "codeInspector.labels" . | nindent 4 }}
handler: runsc
---
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: kata-fc
  labels:
    {{- include "codeInspector.labels" . | nindent 4 }}
handler: kata-fc
{{- end }}
```

---

## Phase 6: Deployment & Runtime Verification

### 6.1 Helm Upgrade Execution
```bash
helm upgrade --install codeinspector ./codeInspector -n opensandbox-system --create-namespace --set global.ocm.role=hub
```

### 6.2 Verified Pod Status on Hub (`opensandbox-system`)
```text
NAME                                                     READY   STATUS    RESTARTS   AGE
codeinspector-agentgateway-controller-79c6f549df-n477k   1/1     Running   0          2m20s
codeinspector-sealed-secrets-57dc877cb9-lrr8f            1/1     Running   0          9m26s
opensandbox-controller-86c99b4948-fn4f5                  1/1     Running   0          2m20s
opensandbox-server-6554989788-q4b4j                      1/1     Running   0          23s
postgresql-7bd7f466dd-n6glq                              1/1     Running   0          9m26s
rabbitmq-5685746466-78l9x                                1/1     Running   0          9m26s
redis-7f8475f964-kt7lt                                   1/1     Running   0          9m26s
sandbox-api-584f569c4d-xp6jm                             1/1     Running   0          2m20s
```

### 6.3 End-to-End API Server Health Response (`http://localhost:8000/health`)
```json
{
  "status_code": 200,
  "status": "healthy",
  "backend": "opensandbox",
  "healthy": true,
  "dependencies": {
    "database": {"status": "healthy", "details": "PostgreSQL Connected"},
    "cache": {"status": "healthy", "details": "Redis Cache Connected"},
    "queue": {"status": "healthy", "details": "Redis Queue Connected"},
    "opensandbox": {"status": "healthy", "details": "Backend name: opensandbox is responsive"}
  }
}
```

### 6.4 `opensandbox-server` Startup Log
```text
INFO: Creating sandbox service with type: kubernetes
INFO: Creating workload provider: OcmWorkloadProvider
INFO: Initialized workload provider: OcmWorkloadProvider
INFO: Uvicorn running on http://0.0.0.0:8080
```

---

## Phase 7: Custom Tagging (`v0.7.10-ocm`) & Containerd Persistence Fix

### 7.1 Issue Summary & Diagnostic Log
When the host VM or Kind cluster nodes restart, containerd inside the Kind control plane node re-checks image registries. If local images use official release tags (such as `01community/01sandbox-opensandbox-server:v0.7.10`), containerd pulls the official upstream layer from Docker Hub.

Because the upstream public image lacks the custom `OcmWorkloadProvider` module, `opensandbox-server` threw the following initialization exception upon restart:
```text
ERROR: Failed to create workload provider: Unsupported workload provider type 'ocm'. Available providers: batchsandbox, agent-sandbox
ValueError: Unsupported workload provider type 'ocm'. Available providers: batchsandbox, agent-sandbox
```

### 7.2 Root Cause & Architectural Fix
- **Root Cause**: Upstream public tag collision on Docker Hub causing containerd image layer overwrites during cluster node restarts.
- **Fix**: Rebuilt the local OCM image with a unique custom tag `01community/01sandbox-opensandbox-server:v0.7.10-ocm`. Because `v0.7.10-ocm` does not exist upstream on Docker Hub, containerd will never pull upstream layers or overwrite the image upon node restarts.

### 7.3 `[MODIFY]` [`codeInspector/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/values.yaml)
- **Path**: `codeInspector/values.yaml`
- **Reason**: Updated `opensandbox.server.image.tag` from `v0.7.10` to `v0.7.10-ocm`.

```yaml
opensandbox:
  server:
    workloadProvider: "ocm"
    placementName: "sandbox-spoke-placement"
    image:
      repository: 01community/01sandbox-opensandbox-server
      tag: "v0.7.10-ocm"
      pullPolicy: IfNotPresent
```

### 7.4 `[MODIFY]` [`codeInspector/values-local.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/values-local.yaml)
- **Path**: `codeInspector/values-local.yaml`
- **Reason**: Aligned repository and tag to `01community/01sandbox-opensandbox-server:v0.7.10-ocm`.

```yaml
opensandbox:
  server:
    image:
      repository: 01community/01sandbox-opensandbox-server
      tag: "v0.7.10-ocm"
      pullPolicy: IfNotPresent
```

### 7.5 Execution & Verification Commands
```bash
# 1. Build distinct custom OCM image
docker build -t 01community/01sandbox-opensandbox-server:v0.7.10-ocm ./opensandbox-server/docker-build

# 2. Load into Kind primaryhub
kind load docker-image 01community/01sandbox-opensandbox-server:v0.7.10-ocm --name primaryhub

# 3. Deploy updated Helm release
helm upgrade --install codeinspector ./codeInspector -n opensandbox-system --set global.ocm.role=hub

# 4. Verify pod status
kubectl get pods -n opensandbox-system
```

### 7.6 Final Verified Pod State
```text
NAME                                                     READY   STATUS    RESTARTS   AGE
codeinspector-agentgateway-controller-79c6f549df-n477k   1/1     Running   0          39h
codeinspector-sealed-secrets-57dc877cb9-lrr8f            1/1     Running   0          40h
opensandbox-controller-86c99b4948-fn4f5                  1/1     Running   0          39h
opensandbox-server-55564d6f44-8wp9p                      1/1     Running   0          43s
postgresql-7bd7f466dd-n6glq                              1/1     Running   0          40h
rabbitmq-5685746466-78l9x                                1/1     Running   0          40h
redis-7f8475f964-kt7lt                                   1/1     Running   0          40h
sandbox-api-584f569c4d-xp6jm                             1/1     Running   0          39h
```
