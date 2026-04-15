# CodeInspector API - Modular Architecture Description

This document provides a detailed overview of the modularized FastAPI architecture used in the CodeInspector API. The system is designed to be a transparent gateway and management layer for the internal OpenSandbox service.

## Architecture Overview

The API is split into four primary modules to ensure a clean separation of concerns:

-   **`models.py`**: Data Transfer Objects (DTOs) and validation schemas.
-   **`config.py`**: Environment-based configuration and security headers.
-   **`backends.py`**: Logic for interacting with the execution clusters.
-   **`codeinspectior_api.py`**: The application entry point and routing layer.

---

## Component Details

### 1. `models.py`
This module defines the "language" used by the API. It uses **Pydantic** to enforce strict data validation for all incoming and outgoing requests.
-   **Execution Models**: `RunRequest` and `RunResponse` handle the core code execution requests.
-   **Sandbox Lifecycle**: `CreateSandboxRequest` and `SandboxResponse` define the requirements for provisioning new isolated environments.
-   **Enums**: `Language` restricts execution to specific supported runtimes (Python, JavaScript, Bash).

### 2. `config.py`
This module handles all external dependencies and environment-specific settings.
-   **Base URL**: `opensandbox_base_url()` dynamically resolves the upstream service address (usually a Kubernetes ClusterIP service).
-   **Headers**: `opensandbox_headers()` centralizes the management of the `OPEN-SANDBOX-API-KEY`, ensuring that every request to the backend is authenticated correctly.

### 3. `backends.py`
This module implements the **Strategy Pattern**.
-   **`SandboxBackend` (Abstract)**: An interface that defines what any execution backend *must* do (run code, create sandboxes, check health).
-   **`GenericHTTPBackend` (Concrete)**: The actual implementation that communicates with the OpenSandbox server via HTTP. It handles:
    -   Network timeouts.
    -   Error wrapping.
    -   Response parsing and mapping.

### 4. `codeinspectior_api.py`
This is the "brain" of the application. It initializes the FastAPI app and sets up the routes.
-   **App State**: Initializes the global `state.backend` based on configuration.
-   **Native Routes**: Provides high-level, typed endpoints like `/v1/sandboxes`.
-   **Transparent Proxy**: Contains the `_do_proxy` helper which forwards any request under `/backend/opensandbox/*` directly to the backend while injecting authentication and stripping unnecessary headers.
-   **Dynamic Documentation**: Bridges the gap between the local API and the remote service by fetching and patching the backend's `openapi.json` on the fly.

---

## Request Lifecycle (Example: Sandbox Creation)

1.  **Incoming Request**: A client sends a POST request to `/v1/sandboxes` with a JSON payload.
2.  **Validation**: FastAPI uses the `CreateSandboxRequest` model from `models.py` to validate the image spec and resource limits.
3.  **Backend Dispatch**: The route calls `state.backend.create_sandbox(req)`.
4.  **HTTP Transmission**: The `GenericHTTPBackend` (in `backends.py`) fetches the API key from `config.py`, wraps the request, and sends it to the internal OpenSandbox service.
5.  **Response Handling**: The response from the backend is parsed into a `SandboxResponse` and returned to the client.

## Benefits of this Modularization

-   **Readability**: Each file is focused on one task (Modeling, Config, Logic, or Routing).
-   **Maintainability**: You can change how backends work in `backends.py` without touching the route definitions in `codeinspectior_api.py`.
-   **Extensibility**: To add a new backend type (e.g., AWS Lambda or Docker), you simply create a new class in `backends.py` that inherits from `SandboxBackend`.
-   **DRY (Don't Repeat Yourself)**: Configuration and authentication logic are defined once in `config.py` and reused across both native routes and the proxy system.

---

## Security Scan Reporting Pipeline (The "Auto-Report" Mechanism)

This section details exactly how the `/v1/scan-jobs` endpoint coordinates between the `apiServer` (the gateway) and the `opensandbox-server` (the execution backend) to automatically block, execute a scan, and return the final report without requiring secondary polling API calls.

### 1. The Gateway Route: `codeinspectior_api.py`

When a client sends a `POST` request, it enters through the API Gateway route.

```python
@app.post("/v1/scan-jobs", response_model=ScanJobResponse, tags=["Security Scan Pipeline"])
async def create_scan_job(req: ScanJobRequest):
    data = await state.backend.create_scan_job(req.dict(exclude_none=True))
    return ScanJobResponse(**data)
```

**How it works:**
- It uses the local `ScanJobRequest` model to validate the JSON payload (like `files`, `code`, and `tools`).
- It delegates the transmission logic to the current backend via `state.backend.create_scan_job()`.
- After receiving the raw dictionary `data` back from the backend, it deserializes the response using `ScanJobResponse(**data)`. FastAPI automatically uses this Pydantic model to construct the final JSON string the client receives, guaranteeing no stray or malformed fields leak out.

### 2. The Model Fix: `models.py`

To allow the gateway to return the full scan report directly in the initial `POST` response, the target DTO (Data Transfer Object) models must match the backend's data exactly. This is implemented in `apiServer/fastapi/models.py`.

```python
class ScanJobResponse(BaseModel):
    """Response returned upon successfully scheduling a scan job."""
    job_id: str
    sandbox_id: Optional[str] = None
    status: Optional[str] = None
    report: Optional[dict] = None
    error: Optional[str] = None
```

**How it works:**
- Before our fix, `ScanJobResponse` lacked the `status`, `report`, and `error` attributes.
- Even though the backend (the `opensandbox-server`) generated the huge scan JSON and sent it over HTTP, the API gateway stripped away everything besides `job_id` and `sandbox_id` when executing `return ScanJobResponse(**data)`. By mirroring the full model, Pydantic no longer truncates the backend's generated `report`.

### 3. The Backend Communicator: `backends.py`

The internal HTTP proxying logic happens inside `GenericHTTPBackend`.

```python
async def create_scan_job(self, req_body: bytes) -> dict:
    """Triggers the isolated scan pipeline and blocks asynchronously for the final result."""
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.post(
            f"{self._url}/v1/scan-jobs",
            content=req_body,
            headers=opensandbox_headers()
        )
        r.raise_for_status()
        return r.json()
```

**How it works:**
- The connection timeout is intentionally boosted to `300` seconds because the remote `opensandbox-server` actively blocks the HTTP connection while a container finishes mounting the code and executing security linters.
- It automatically handles OpenSandbox authentication headers using `opensandbox_headers()`.
- Once the backend responds with an HTTP status, `r.json()` parses the payload back into a Python dictionary, piping the data back to `codeinspectior_api.py`.

### 4. The Execution Engine: `lifecycle.py` (opensandbox-server)

The heavy lifting happens within the core OpenSandbox Kubernetes orchestration layer located in `opensandbox-server/docker-build/src/api/lifecycle.py`.

```python
@router.post("/scan-jobs", ...)
async def create_scan_job(request: Request, ...) -> ScanJobResponse:
    # 1. Parsing & Writing Data
    # Detects extension, decodes base64, and writes source files to a PVC.
    
    # 2. Container Provisioning
    sandbox_req = CreateSandboxRequest(...)
    created_sandbox = sandbox_service.create_sandbox(sandbox_req)

    # 3. Intelligent Blocking (The Auto-Trigger mechanism)
    while asyncio.get_event_loop().time() < deadline:
        if os.path.exists(report_path):
            with open(report_path, "r") as f:
                report_data = json.load(f)
            return ScanJobResponse(..., report=report_data)

        # Yields control back to the async loop (non-blocking wait)
        await asyncio.sleep(1) 
```

**How it works:**
- Instead of scheduling the container and immediately returning `202 Accepted` or `201 Created` with just a `job_id`, this function purposefully blocks via a non-destructive `while` loop.
- It constantly probes the shared persistent volume (`scan-pvc`) checking if the security analysis container has finished and successfully written out `/reports/security_scan_report.json`.
- The moment the file is detected, it terminates the `while` loop, ingests the file, parses it via `json.load()`, and populates the `report` attribute of the internal native `ScanJobResponse` object.
- The `opensandbox-server` finally sends this massive JSON back over the network to the `GenericHTTPBackend` inside `backends.py`.

### The Detailed End-to-End Execution Flow

To fully grasp the architecture, here is the step-by-step technical execution flow of a single security scan job:

1. **Client Submission (Ingress)**
   The user/client initiates an HTTP `POST` request to `http://localhost:8000/v1/scan-jobs`. The payload is a JSON document containing either raw code strings or base64-encoded files.
2. **Gateway Reception & Validation (`codeinspectior_api.py`)**
   The FastAPI application instance receives the request. The `create_scan_job` route handler uses the `ScanJobRequest` model to aggressively validate the schema of the incoming JSON.
3. **Internal Proxying (`backends.py`)**
   The route invokes `state.backend.create_scan_job()`. The `GenericHTTPBackend` constructs an internal request to the `opensandbox-server` backend. It explicitly injects the `OPEN-SANDBOX-API-KEY` (retrieved via `config.py`) into the request headers and configures the `httpx.AsyncClient` with a long-lived `timeout=300` to prevent premature disconnections while the scan runs.
4. **Backend Ingestion & File Processing (`lifecycle.py`)**
   The `opensandbox-server` receives the request. The `create_scan_job` method decodes any base64 files and utilizes regex-based heuristic analysis (`detect_extension`) to guess the file language if it wasn't provided (e.g., categorizing as `yaml`, `go`, `py`, or `js`). It writes these files directly onto a mounted Kubernetes `PersistentVolumeClaim` (PVC) under `/data/{job_id}/workspace`.
5. **Kubernetes Container Scheduling (`factory.py` -> `docker/k8s API`)**
   The backend instructs the orchestrator to spin up a new CodeInterpreter container (using the `codeinterpreter:3.1.0` image). This newly scheduled Pod has two critical `VolumeMounts`:
   - `/workspace`: Maps to the PVC subdirectory containing the client's code.
   - `/reports`: Maps to an empty PVC subdirectory where the output will go.
   The container starts executing the security Linters (`semgrep`, `bandit`, etc.) mapped via the `entrypoint`.
6. **Asynchronous Polling & Blocking (`lifecycle.py`)**
   Instead of immediately responding back to the API Server, the fastAPI thread on the OpenSandbox cluster enters a non-blocking `while` loop (`await asyncio.sleep(1)`). Every second, it polls the local file system (which is mounted to the shared PVC) checking if the `/reports/security_scan_report.json` file has been persisted to disk by the security container.
7. **Report Ingestion and Transmission (`lifecycle.py`)**
   Once the security sandbox finishes execution and writes the JSON report, the poll loop detects the file. It opens the file, parses it using `json.load()`, attaches it to the `report` attribute of the internal backend model, and responds with `HTTP 201 Created` containing the massive JSON body.
8. **Gateway Deserialization (`backends.py` -> `codeinspectior_api.py`)**
   The `httpx.AsyncClient` in the `apiServer` receives the HTTP response, successfully resolving the 300-second await. It parses the JSON text stream back into a python dictionary using `.json()`.
9. **Final Output Schema Enforcement (`models.py`)**
   The gateway passes the dictionary to `ScanJobResponse(**data)`. Because the API Server's Pydantic `models.py` schema for `ScanJobResponse` perfectly mirrors the backend model (importantly retaining fields like `report`), FastAPI packages the full payload and sends it back out the edge gateway to the awaiting HTTP Client.
