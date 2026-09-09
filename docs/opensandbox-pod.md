# SPEC-SBX-2026-001: OpenSandbox Pod Lifecycle & Provisioning Specification

| Metadata | Value |
|---|---|
| **Spec ID** | `SPEC-SBX-2026-001` |
| **Title** | OpenSandbox Pod Provisioning Architecture & Code Specification |
| **Version** | `1.1.0` |
| **Status** | `APPROVED / ACTIVE` |
| **Author** | Antigravity AI & 01Sandbox Engineering |
| **Target Scope** | `01-Sandbox` Platform Codebase (`opensandbox-server`, `apiServer`, `code-interpreter`, `opensandboxResourcePool`) |
| **Last Updated** | 2026-09-09 |

---

## 1. Purpose & Scope

This Spec-Driven Development (SDD) document acts as the **single source of truth** for both software engineers and AI coding assistants regarding how **sandbox execution pods** are requested, configured, pre-warmed, and dynamically provisioned across the `01-Sandbox` platform.

It contains the **complete code snippets in exact sequential order of execution**, tracing a sandbox pod creation request from initial HTTP ingestion down to container runtime execution.

---

## 2. Sequential Execution Flow Diagram

```
[ External Client / Web UI ]
            │
            ▼ (1) POST /v1/sandboxes
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 1: FastAPI Gateway Router                                          │
│ [apiServer/fastapi/sandboxes/router.py]                                 │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼ (2) Calls backend.create_sandbox()
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 2: HTTP Backend Client Dispatcher                                  │
│ [apiServer/fastapi/backends.py]                                         │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼ (3) HTTP POST http://opensandbox-server:80/api/v1/01sbx/sandboxes
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 3: OpenSandbox Server REST Endpoint                                │
│ [opensandbox-server/docker-build/src/api/lifecycle.py]                 │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼ (4) Invokes sandbox_service.create_sandbox()
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 4: Kubernetes Sandbox Service (Business Logic)                     │
│ [opensandbox-server/docker-build/src/services/k8s/kubernetes_service.py]│
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼ (5) Invokes workload_provider.create_workload()
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 5: Kubernetes Workload Provider & Pod Manifest Construction        │
│ [opensandbox-server/docker-build/src/services/k8s/batchsandbox_provider.py]│
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼ (6) Applies Pod Spec via K8s CustomObjectsApi / CoreV1Api
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 6: Pre-Warmed Pool / Resource Allocation                           │
│ [opensandboxResourcePool/opensandbox-resourcePool.yaml]                 │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼ (7) Pod Instantiated in "opensandbox" Namespace
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 7: Container Image Build & Runtime Script                           │
│ [code-interpreter/Dockerfile] & [code-interpreter/scripts/code-interpreter.sh] │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Code Implementation in Execution Order

### STEP 1: Gateway API Router Ingestion
* **File:** [`apiServer/fastapi/sandboxes/router.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/sandboxes/router.py#L23-L33)
* **Role:** Exposes the public REST route `POST /v1/sandboxes` and forwards the request to the configured backend.

```python
@router.post(
    "/v1/sandboxes",
    response_model=SandboxResponse,
    tags=["Sandboxes"],
    summary="Provision a new isolated sandbox",
    dependencies=[Depends(validate_token)],
)
def create_sandbox(req: CreateSandboxRequest):
    """Creates a new sandbox environment using the active backend."""
    return state.backend.create_sandbox(req)
```

---

### STEP 2: HTTP Backend Client Dispatcher
* **File:** [`apiServer/fastapi/backends.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/backends.py#L132-L144)
* **Role:** Formats the request payload and makes an HTTP POST call to the internal control plane server (`http://opensandbox-server:80/api/v1/01sbx/sandboxes`).

```python
def create_sandbox(self, req: CreateSandboxRequest) -> SandboxResponse:
    """Directly provisions a sandbox on the remote OpenSandbox server."""
    prefix = opensandbox_route_prefix()
    with httpx.Client(timeout=30) as client:
        r = client.post(
            f"{self._url}{prefix}/sandboxes",
            json=req.dict(exclude_none=True),
            headers=opensandbox_headers(),
        )
        r.raise_for_status()
        data = r.json()
        return SandboxResponse(**data)
```

---

### STEP 3: OpenSandbox Server REST Endpoint
* **File:** [`opensandbox-server/docker-build/src/api/lifecycle.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/api/lifecycle.py#L334-L383)
* **Role:** Receives the `/sandboxes` HTTP POST request in the `opensandbox-server` microservice and delegates to `sandbox_service`.

```python
@router.post(
    "/sandboxes",
    response_model=CreateSandboxResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Sandbox creation accepted for asynchronous provisioning"},
        400: {"model": ErrorResponse, "description": "Invalid or malformed request"},
        500: {"model": ErrorResponse, "description": "Unexpected server error"},
    },
)
async def create_sandbox(
    request: CreateSandboxRequest,
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
) -> CreateSandboxResponse:
    """
    Create a sandbox from a container image.
    Creates a new sandbox with optional resource limits, env vars, and metadata.
    """
    return sandbox_service.create_sandbox(request)
```

---

### STEP 4: Kubernetes Sandbox Service (Business Logic)
* **File:** [`opensandbox-server/docker-build/src/services/k8s/kubernetes_service.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/kubernetes_service.py#L251-L340)
* **Role:** Generates the unique RFC4122 UUID, validates request parameters, sets TTL expiration timestamps, attaches Kubernetes labels, and requests workload creation.

```python
def create_sandbox(self, request: CreateSandboxRequest) -> CreateSandboxResponse:
    # 1. Validate parameters & entrypoint
    ensure_entrypoint(request.entrypoint)
    ensure_metadata_labels(request.metadata)
    ensure_timeout_within_limit(
        request.timeout,
        self.app_config.server.max_sandbox_timeout_seconds,
    )

    # 2. Generate unique Sandbox UUID
    sandbox_id = self.generate_sandbox_id()

    # 3. Calculate expiration timestamp (TTL)
    created_at = datetime.now(timezone.utc)
    expires_at = None
    if request.timeout is not None:
        expires_at = calculate_expiration_or_raise(created_at, request.timeout)

    # 4. Construct mandatory Kubernetes labels
    labels = {SANDBOX_ID_LABEL: sandbox_id}
    if expires_at is None:
        labels[SANDBOX_MANUAL_CLEANUP_LABEL] = "true"

    # 5. Extract resource limits (CPU/Memory)
    resource_limits = {}
    if request.resource_limits and request.resource_limits.root:
        resource_limits = request.resource_limits.root

    # 6. Invoke workload provider to build and submit K8s Pod
    workload_info = self.workload_provider.create_workload(
        sandbox_id=sandbox_id,
        namespace=self.namespace,
        image_spec=request.image,
        entrypoint=request.entrypoint,
        env=request.env or {},
        resource_limits=resource_limits,
        labels=labels,
        expires_at=expires_at,
        execd_image=self.execd_image,
        extensions=request.extensions,
        network_policy=request.network_policy,
        volumes=request.volumes,
    )

    logger.info("Created sandbox (Async): id=%s, workload=%s", sandbox_id, workload_info.get("name"))
    return workload_info
```

---

### STEP 5: Kubernetes Workload Provider & Pod Manifest Construction
* **File:** [`opensandbox-server/docker-build/src/services/k8s/batchsandbox_provider.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/batchsandbox_provider.py#L207-L255)
* **Role:** Constructs the Kubernetes Pod spec with `execd` initContainer, main container, volumes, security `runtimeClassName`, and submits it to the Kubernetes API cluster.

```python
# 1. Build init container for execd agent installation
init_container = self._build_execd_init_container(execd_image)

# 2. Build main container with execution sandbox image & env vars
main_container = self._build_main_container(
    image_spec=image_spec,
    entrypoint=entrypoint,
    env=env,
    resource_limits=resource_limits,
    has_network_policy=network_policy is not None,
    image_pull_policy=image_pull_policy,
)

# 3. Assemble Pod specification dictionary
pod_spec: Dict[str, Any] = {
    "initContainers": [self._container_to_dict(init_container)],
    "containers": [self._container_to_dict(main_container)],
    "volumes": [{"name": "opensandbox-bin", "emptyDir": {"sizeLimit": "1Gi"}}],
}

# 4. Resolve and inject secure runtimeClassName (gvisor, kata-fc, kata-qemu)
runtime_class = self.runtime_class
if extensions and "runtimeClassName" in extensions:
    runtime_class = extensions["runtimeClassName"]
elif extensions and "secure_runtime" in extensions:
    sr = extensions["secure_runtime"]
    if sr == "gvisor":
        runtime_class = "gvisor"
    elif sr in ("kata-fc", "firecracker"):
        runtime_class = "kata-fc"

if runtime_class:
    pod_spec["runtimeClassName"] = runtime_class

# 5. Submit Pod resource creation request to Kubernetes API
```

---

### STEP 6: Pre-Warmed Pool / Resource Specification
* **File:** [`opensandboxResourcePool/opensandbox-resourcePool.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandboxResourcePool/opensandbox-resourcePool.yaml)
* **Role:** Defines the CRD template for pre-warming sandbox pods to ensure sub-millisecond cold start times.

```yaml
apiVersion: sandbox.opensandbox.io/v1alpha1
kind: Pool
metadata:
  name: my-sandbox-pool
spec:
  template:
    spec:
      containers:
      - name: sandbox-container
        image: 01community/01sandbox-codeinterpreter:1.0.0
        ports:
        - containerPort: 44772   # execd daemon port
        - containerPort: 54321   # Jupyter server port
  capacitySpec:
    bufferMin: 1      # Pre-warms 1 warm pod minimum
    bufferMax: 1
    poolMin: 1
    poolMax: 1
```

---

### STEP 7: Server Runtime Configuration
* **File:** [`opensandbox-server/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/values.yaml#L56-L76)
* **Role:** Server configuration (`configToml`) setting the runtime provider to Kubernetes `batchsandbox`.

```toml
[server]
host = "0.0.0.0"
port = 80
log_level = "INFO"

[runtime]
type = "kubernetes"
execd_image = "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/execd:v1.0.7"

[kubernetes]
namespace = "opensandbox"
sandbox_create_timeout_seconds = 600
workload_provider = "batchsandbox"
batchsandbox_template_file = "/etc/opensandbox/example.batchsandbox-template.yaml"
```

---

### STEP 8: Container Build & Runtime Entrypoint
* **Dockerfile:** [`code-interpreter/Dockerfile`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/code-interpreter/Dockerfile#L115-L122)
* **Entrypoint:** [`code-interpreter/scripts/code-interpreter.sh`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/code-interpreter/scripts/code-interpreter.sh)
* **Role:** Builds the `01community/01sandbox-codeinterpreter` image and executes startup scripts inside the container pod once launched.

```dockerfile
COPY src/ /opt/opensandbox/src/
COPY rules/ /opt/opensandbox/rules/
COPY scripts/code-interpreter.sh /opt/opensandbox/code-interpreter.sh

RUN chmod +x /opt/opensandbox/code-interpreter.sh

ENTRYPOINT ["/opt/opensandbox/code-interpreter.sh"]
```

---

## 4. Component File Reference

| Step # | Stage | File Path | Key Symbol / Method |
|---|---|---|---|
| **1** | Ingestion Router | [`apiServer/fastapi/sandboxes/router.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/sandboxes/router.py#L30) | `create_sandbox()` |
| **2** | HTTP Dispatcher | [`apiServer/fastapi/backends.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/backends.py#L132) | `GenericHTTPBackend.create_sandbox()` |
| **3** | Server Endpoint | [`opensandbox-server/docker-build/src/api/lifecycle.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/api/lifecycle.py#L358) | `create_sandbox()` |
| **4** | K8s Business Logic | [`opensandbox-server/docker-build/src/services/k8s/kubernetes_service.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/kubernetes_service.py#L251) | `KubernetesSandboxService.create_sandbox()` |
| **5** | Pod Spec & K8s API | [`opensandbox-server/docker-build/src/services/k8s/batchsandbox_provider.py`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/docker-build/src/services/k8s/batchsandbox_provider.py#L207) | `BatchSandboxProvider.create_workload()` |
| **6** | Warm Pool CRD | [`opensandboxResourcePool/opensandbox-resourcePool.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandboxResourcePool/opensandbox-resourcePool.yaml) | `kind: Pool` |
| **7** | Server Config | [`opensandbox-server/values.yaml`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/opensandbox-server/values.yaml#L56) | `configToml` |
| **8** | Container Entrypoint | [`code-interpreter/Dockerfile`](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/code-interpreter/Dockerfile#L122) | `ENTRYPOINT` |

---

## 5. Security & Runtime Isolation Summary

| Secure Runtime | Hypervisor / Interceptor | Target Class |
|---|---|---|
| `gvisor` | Google gVisor user-space kernel | Process-level syscall isolation |
| `kata-fc` | AWS Firecracker microVM | Hardware-assisted microVM boundary |
| `kata-qemu` | QEMU Hypervisor | Full hardware virtualization |
