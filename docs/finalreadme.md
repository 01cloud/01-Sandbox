# CodeInspector: Master Documentation

Welcome to **CodeInspector**, an enterprise-grade, Kubernetes-native platform designed to execute untrusted code in hardened, isolated environments. Whether you are building an AI agent, a CI/CD pipeline, or a multi-tenant cloud application, CodeInspector provides the perfect balance between **Rapid Iteration** and **Strict Security Compliance**.

---

## 🌟 1. Overview & Key Pillars

CodeInspector is built on a **Facade Architecture**, offering a unified, high-performance API that abstracts away the complexity of managing multiple execution environments. Every line of code submitted is securely "sandboxed" and automatically audited for vulnerabilities, secrets, and policy violations.

### Key Pillars
1. **Kernel-Level Isolation**: Powered by **gVisor (runsc)**, CodeInspector provides a second layer of defense. Even if a process escapes the container, it remains trapped within a sandboxed kernel.
2. **The Asynchronous Advantage**: A "Fire-and-Forget" architecture ensures sub-100ms job acknowledgment, enabling seamless user experiences under load.
3. **Isolated Persistence**: Every execution job is assigned a unique cryptographic path on a shared PVC, preventing cross-tenant data leakage.
4. **Automated Security Pipeline**: Code is audited in real-time by an **Ultra-Strict Security Toolchain** (Semgrep, Gitleaks, Bandit, Trivy, YAMLlint, Kube-Linter, KubeConform).

---

## 🏗️ 2. Architecture & Data Flow

### Traffic Flow & Component Architecture
CodeInspector acts as a "Brain" (FastAPI Orchestrator) that manages "Workers" (Sandboxes). 

```mermaid
graph TD
    User([External Client]) -->|HTTP POST| MB[MetalLB Load Balancer]
    MB -->|IP Traffic| AG[Agent Gateway - Envoy Proxy]
    
    subgraph "Ingress Layer"
        AG -->|Auth Check| AGP[AgentgatewayPolicy - API Key]
        AGP -->|Route| HR[HTTPRoute - /]
    end
    
    HR -->|Forward| SAS[Sandbox API Service]
    
    subgraph "Orchestration Layer"
        SAS -->|FastAPI| AS[API Server]
        AS -->|Proxy| OSB_PROXY[/api/z1sandbox/*]
    end
    
    subgraph "Execution Layer"
        OSB_PROXY -->|REST| OSS[OpenSandbox Server]
        OSS -->|Lifecycle| OSC[OpenSandbox Controller]
        OSC -->|Provision| CI_POD[Code Interpreter Pod / gVisor]
    end
```

### Component Deep Dive
- **Agent Gateway**: High-level proxy (Kubernetes Gateway API + Envoy) providing a strict API Key Authentication policy and routing external traffic into the cluster.
- **Sandbox API Server (FastAPI)**: The central orchestrator exposing the stable HTTP contract. It translates high-level code execution requests to backend-specific commands.
- **OpenSandbox Server & Controller**: The Kubernetes-native sandbox engine managing ephemeral `BatchSandbox` and `Pool` CRDs.
- **Code Interpreter Sandbox**: The isolated execution pod. It dynamically installs Jupyter kernels and orchestrates the pre-boot security scans (`scanner_orchestrator.py`).

### Intelligent Scanning Pipeline Data Flow
1. **Submission**: User submits a `ScanJobRequest` with raw code strings to `/v1/scan-jobs`.
2. **Detection & Persistence**: The OpenSandbox Server uses Intelligent Language Detection to determine the code language, saves it with the correct extension, and mounts it via PVC `subPath` into a new Sandbox.
3. **Scans Run**: `scanner_orchestrator.py` triggers the Ultra-Strict security toolchain within the gVisor-isolated sandbox. This includes policy-based linting (**Kube-Linter**) and strict schema validation (**KubeConform**) for Kubernetes manifests.
4. **Reporting**: Results are aggregated, unified, and saved back to the PVC in `security_scan_report.json`.

---

## ⚡ 3. The Asynchronous Execution Pipeline

To handle high concurrency (e.g., 50+ concurrent bursts), CodeInspector has transitioned from a synchronous blocking model to an asynchronous **"Fire-and-Forget"** paradigm.

- **Non-Blocking Architecture**: The server creates the Sandbox cluster resource and immediately responds with a `job_id`. Threads are released instantly, dropping latency to <100ms.
- **Concurrency Resilience**: Using `TokenBucketRateLimiter` and optimized HPAs, the system queues simultaneous requests safely at the Kubernetes scheduler level (`Pending` state).
- **Process Logging**: The API outputs a unified, terminal-style `process.log` updated by both the API server (provisioning phases) and the Sandbox (scanning phases).

---

## 🌐 4. Exposing the API & Gateway Integration

All external traffic to the Sandbox API MUST pass through the **Agent Gateway** which enforces strict Authentication via `AgentgatewayPolicy`.

### Ngrok Exposure Example
Ngrok must tunnel directly to the AgentGateway's LoadBalancer IP:
```bash
ngrok http --domain=your-custom.ngrok-free.dev <AGENTGATEWAY_LB_IP>:80
```

### Navigating Strict API Contexts
Because the Gateway demands an API key on every request:
1. **Browsers**: Directly visiting the Swagger `/docs` in Chrome will yield a `401 Unauthorized`. You must use a header injector extension (like ModHeader) to add: `Authorization: Bearer <API_KEY>`.
2. **API Clients**: Provide the token under standard Bearer Token authorization.

---

## 🚀 5. User Integration Guide

External applications (CI/CD pipelines, dashboards, bots) interact with CodeInspector via a standard integration workflow.

### The Polling Workflow
1. **Submit Job** to `POST /v1/scan-jobs`
2. **Retrieve `job_id`** from response
3. **Poll Status** at `GET /v1/scan-status/{job_id}` (or simply `/v1/scan-status` for the latest session job)
4. **Fetch Final Report** at `GET /v1/scan-jobs/{job_id}/report`

### Example Integration (Python)

```python
import requests
import time

BASE_URL = "https://your-custom.ngrok-free.dev/api/z1sandbox"
HEADERS = {
    "Authorization": "Bearer YOUR_API_TOKEN",
    "Content-Type": "application/json"
}

def run_security_scan():
    # 1. Submit scan job
    payload = {"code": "import os\nos.system('echo Vulnerability')"}
    print("[*] Submitting code for analysis...")
    response = requests.post(f"{BASE_URL}/v1/scan-jobs", json=payload, headers=HEADERS)
    job_id = response.json().get("job_id")
    print(f"[*] Job ID received: {job_id}")

    # 2. Poll for terminal-style Status Logs
    print("[*] Tailing execution logs...")
    while True:
        status_res = requests.get(f"{BASE_URL}/v1/scan-status/{job_id}", headers=HEADERS)
        logs = status_res.text
        print(logs)
        
        if "Security scans complete." in logs or "FAILED" in logs:
            break
        time.sleep(3)

    # 3. Retrieve structured JSON report
    print("\n[*] Fetching final security report...")
    report_res = requests.get(f"{BASE_URL}/v1/scan-jobs/{job_id}/report", headers=HEADERS)
    print(report_res.json())

if __name__ == "__main__":
    run_security_scan()
```

---

## ⚙️ 6. Deployment & Configuration

CodeInspector is orchestrated entirely via **Helm**. To deploy:

```bash
cd codeInspector
helm upgrade --install codeinspector . -n default --create-namespace
```

### Kubernetes Highlights & Technical Fixes Handled Natively
During cluster stabilization, several critical issues were resolved within the internal chart definitions:
- **Missing CRDs**: Re-compiled raw YAML CRDs so Helm registers `BatchSandbox` and `Pool` configurations properly before the controller initializes.
- **RBAC Limitations**: Upgraded `ClusterRole` permissions to map precisely to the `sandbox.opensandbox.io` apiGroups.
- **Kube-Config Injection**: Applied an inline ConfigMap (`kubeconfig_path = ""`) to correctly enable in-cluster auth for the `opensandbox-server`.
- **Node Affinity Pinning**: Enforced `kubernetes.io/hostname: codebot-worker` nodeSelector logic so the Server and Sandboxes execute on the identical physical node, guaranteeing local-path PVC read/write consistency.

**To verify the stack:**
```bash
kubectl get pods -n opensandbox-system
kubectl get pods -n agentgateway-system
kubectl get batchsandbox -A
```
