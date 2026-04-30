# Technical Depth: CodeInspector Architecture & Implementation

This document provides a deep dive into the internal working principles, architecture, and technical implementation of the CodeInspector platform, specifically focusing on the `agentgateway`, `code-interpreter`, `opensandbox-server`, and the `codeInspector` orchestration layer.

---

## 1. Architecture Overview (Traffic Flow)

The following diagram illustrates how external traffic flows through the system to execute code in an isolated environment.

```mermaid
graph TD
    User([External Client]) -->|HTTP POST /run| MB[MetalLB Load Balancer]
    MB -->|IP Traffic| AG[Agent Gateway - Envoy Proxy]
    
    subgraph "Ingress Layer (agentgateway)"
        AG -->|Auth Check| AGP[AgentgatewayPolicy - Strict API Key]
        AGP -->|Route| HR[HTTPRoute - /]
    end
    
    HR -->|Forward| SAS[Sandbox API Service]
    
    subgraph "Orchestration Layer (apiServer)"
        SAS -->|ClusterIP| AS[API Server - FastAPI]
        AS -->|Backend Strategy| OSB_PROXY[/api/z1sandbox/*]
    end
    
    subgraph "Execution Management (opensandbox-server)"
        OSB_PROXY -->|HTTP/REST| OSS[OpenSandbox Server]
        OSS -->|Lifecycle| OSC[OpenSandbox Controller]
        OSC -->|Provision| CI_POD[Code Interpreter Pod]
    end
    
    subgraph "Isolation Layer (code-interpreter)"
        CI_POD -->|Runsc/gVisor| KERNEL[Isolated Kernel]
        CI_POD -->|Logic| CIS[code-interpreter.sh]
        CIS -->|Security| SO[scanner_orchestrator.py]
        SO -->|Execution| JUPYTER[Jupyter Kernel]
    end
```

### Traffic Path Detail:
1.  **Client Entry**: An external client hits the LoadBalancer IP provided by MetalLB.
2.  **Authentication**: The **Agent Gateway** intercepts the request. It uses a custom `AgentgatewayPolicy` to validate the `X-API-Key` header against a Kubernetes Secret.
3.  **Routing**: Validated requests are routed by the `HTTPRoute` to the internal `sandbox-api-service`.
4.  **Backend Delegation**: The **API Server (FastAPI)** receives the request. Depending on the active backend (configured via `/api/switch`), it delegates execution. For production, it proxies the request to the `opensandbox-server`.
5.  **Sandbox Provisioning**: The **OpenSandbox Server** communicates with the Kubernetes API (via the OpenSandbox Controller CRDs) to ensure an isolated pod (sandbox) is available.
6.  **Code Execution**: The **Code Interpreter** pod, running behind gVisor isolation, receives the instructions, performs automated security scans on the payload, and executes the code via a specialized Jupyter kernel.

---

## 2. Component Deep Dive

### A. Agent Gateway (`agentgateway`)

**Working Principle:**
The Agent Gateway acts as the "Bouncer" for the cluster. It leverages the modern **Kubernetes Gateway API** (the successor to Ingress) to provide a programmable, policy-driven entry point.

**Technical Implementation:**
-   **Gateway (`agentgateway-proxy`)**: Defines the physical infrastructure (Envoy proxy) listening on port 80. It allows routes to be "attached" from any namespace.
-   **Policy Enforcement (`AgentgatewayPolicy`)**: A custom CRD implementation that enforces **Strict API Key Authentication**. This ensures that no request reaches the API Server without a valid credential defined in the `agentgateway-system` namespace.
-   **HTTP Routing (`agentgateway-route`)**: Maps the hostname `test.sandbox.com` to the internal API service. It uses a `URLRewrite` filter to ensure paths are correctly translated as they cross the gateway boundary.

### B. Code Interpreter (`code-interpreter`)

**Working Principle:**
The Code Interpreter is a "Smart Runtime". Unlike a simple Python container, it is a multi-language environment hardened for security, featuring pre-boot security orchestration and multiple interactive kernels.

**Technical Implementation:**
-   **Multi-Environment Support**: Uses `code-interpreter-env.sh` to manage PATH and library isolation for Python (3.12+), Node.js (18+), Go (1.25+), and Java (21+).
-   **Pre-Boot Orchestration (`code-interpreter.sh`)**:
    -   **Clone3 Workaround**: Implements a workaround for `clone3` syscall issues encountered in environments with older seccomp profiles or kernels (e.g., when running on certain older K8s nodes).
    -   **Dynamic Kernel Installation**: Background-installs Jupyter kernels for all supported languages on startup to reduce image size while maintaining flexibility.
-   **Automated Security Scanning (`scanner_orchestrator.py`)**:
    -   **Trigger**: Automatically executes whenever files are detected in `/workspace`.
    -   **Toolchain**: Orchestrates `Semgrep` (logic bugs), `Gitleaks` (secrets), `Bandit` (Python anti-patterns), `Trivy` (vulnerabilities), and `YAMLlint` (config).
    -   **Reporting**: Generates a unified JSON risk report and prints a human-readable summary to the pod logs, allowing the API Server to block or flag dangerous code before it runs.

### C. OpenSandbox Server (`opensandbox-server`)

**Working Principle:**
The OpenSandbox Server is the "Management Tier". It abstracts the complexity of Kubernetes pod lifecycles into a simple REST API consumed by the main API Server.

**Technical Implementation:**
-   **Lifecycle Management (`api/lifecycle.py`)**:
    -   Acts as the system's "Brain", orchestrating the translation of high-level API calls into Kubernetes actions.
    -   **Intelligent Language Detection**: Automatically parses input code to determine the correct runtime environment (Python, Node, Go, etc.) and saves it with appropriate extensions to the PVC.
    -   **Dynamic Provisioning**: Manages the instantiation of `BatchSandbox` resources, ensuring volumes are correctly mounted and environment variables are injected.
-   **Data Integrity (`api/schema.py`)**: 
    -   Implements strict Pydantic models for all API interactions.
    -   Ensures type safety across the bridge between the FastAPI management layer and the Kubernetes custom resources.
-   **Security Middleware (`middleware/auth.py`)**:
    -   Provides an integrated **Authentication Layer** that validates the `OPEN-SANDBOX-API-KEY` header.
    -   Includes strict regex-based routing for proxy paths to prevent path traversal attacks.
-   **Configuration (`config.toml`)**: Centralizes the runtime behavior. It defines the Kubernetes namespace (`opensandbox`) and the `workload_provider` (usually `batchsandbox`).
-   **Resource Pooling**: Manages the `Pool` CRD, which pre-warms a set of "Standby" sandboxes. This allows for sub-second sandbox allocation by avoiding the overhead of "Pod Pending" states.
-   **Persistence for Scans**: Configures a dedicated PersistentVolumeClaim (`scan-pvc`) mounted at `/data`. This allows security scan reports to persist even after the execution pod is terminated, providing an audit trail.

### D. CodeInspector Helm Chart (`codeInspector`)

**Working Principle:**
The CodeInspector Helm chart is the "System Orchestrator". It treats the entire stack as a single unit, managing dependencies and cross-component configuration.

**Technical Implementation:**
-   **Dependency Management**: Uses sub-charts for `agentgateway`, `apiServer`, `opensandbox`, and infrastructure (`metallb`, `kindCluster`).
-   **Integrated Configuration**: The root `values.yaml` propagates global settings (like API keys, registry URLs, and resource limits) down to every component.
-   **Namespace Strategy**: Orchestrates the creation and isolation of the `sandbox` namespace for the API and the `opensandbox-system` for the execution layer, maintaining a strict security boundary.

---

## 3. Security Hardening Details

-   **Runtime Isolation**: All sandboxes are executed using **gVisor (`runsc`)**. This provides a second kernel layer, preventing a compromised process from escaping the container and attacking the Kubernetes node.
-   **Network Isolation**: Sandboxes are typically deployed with `network: none` or strict NetworkPolicies to prevent data exfiltration once the code code is in flight.
-   **Filesystem**: The API Server runs with a **Read-Only Filesystem** and dropped Linux capabilities to minimize the blast radius of any potential application-level vulnerability.
-   **API Security**: Every layer (Gateway, API Server, OpenSandbox) reinforces the API Key requirement, ensuring "defense in depth".
