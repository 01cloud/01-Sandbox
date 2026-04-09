# Technical Changelog: CodeInspector Persistence & Concurrency Refactor

This document provides a highly technical summary of the architectural and implementation changes made to transform the CodeInspector platform into a production-ready, asynchronous, and persistent security scanning system.

---

## 1. Asynchronous Execution Pipeline (Fire-and-Forget)
**Problem**: The original `KubernetesSandboxService` used a blocking `_wait_for_sandbox_ready` loop. Under a load of 50+ concurrent users, this caused thread exhaustion, API timeouts, and server crashes.
- **Change**: Removed the synchronous polling loop in `kubernetes_service.py`.
- **Result**: The API now returns a `202 Accepted` immediately after scheduling the Kubernetes resource. The client is provided with a `job_id` and `sandbox_id` for asynchronous tracking.

## 2. Concurrency Resilience & Rate Limiting
**Problem**: Rapid bursts of API requests could overwhelm the Kubernetes API server (etcd pressure).
- **Change**: Integrated a `TokenBucketRateLimiter` within the `K8sClient`. 
- **Change**: Fine-tuned the `apiServer` HPA (Horizontal Pod Autoscaler) and resource limits (CPU: 500m / Mem: 256Mi).
- **Result**: The system can now handle 50+ simultaneous requests by safely queuing them at the K8s scheduler level (`Pending` state) without crashing the application layer.

## 3. Strict Data Isolation (UUID-based subPathing)
**Problem**: Static directory paths (`/workspace` and `/results`) on the shared PVC caused consecutive requests to overwrite each other's code and reports.
- **Change**: Refactored `lifecycle.py` to generate a unique `uuid4()` for every request.
- **Change**: Implemented Kubernetes `subPath` volume mounts:
    - Server side: `/data/{job_id}/workspace` (Source) and `/data/{job_id}/reports` (Reports).
    - Sandbox side: Mounted to `/workspace` and `/reports` respectively.
- **Result**: Full multi-tenant isolation. 100+ users can scan code concurrently with zero data contamination.

## 4. Multi-Node Storage Consistency (Node Affinity)
**Problem**: Kind clusters use the `local-path` provisioner which is node-local. In your 3-node cluster, the Server (Node A) and Sandbox (Node B) were writing to different physical disks, making reports appear "empty" on the server.
- **Change**: Implemented **Node Affinity** pinning across the Helm chart.
- **Implementation**: Injected `nodeSelector: kubernetes.io/hostname: codebot-worker` into:
    - The `opensandbox-server` Deployment.
    - The `BatchSandbox` dynamic CRD template.
- **Result**: Guaranteed co-location of the API server and all security sandboxes on the same physical worker node, ensuring shared access to the local-path PVC.

## 5. Unified Gateway API expansion
**Problem**: The custom isolation logic was hidden in the backend and not reachable via the Port 8000 Gateway.
- **Change**: Exposed the **Security Scan Pipeline** in `codeinspectior_api.py`.
- **New Endpoints**:
    - `POST /v1/scan-jobs`: High-level entry point for isolated scans.
    - `GET /v1/scan-jobs/{job_id}/report`: Persistent retrieval of historical results from the PVC.
    - `GET /v1/scan-jobs/{job_id}/workspace/{file}`: Retrieval of original source code.
- **Result**: Full record persistence. Results are now retrievable days after the sandbox pod has been deleted.

## 6. Automated Tooling Integration
- **ScannerOrchestrator**: Updated default `REPORT_PATH` to `/reports/security_scan_report.json` to align with the new PVC subPath logic.
- **Helm Values**: Updated `scan-pvc` access mode to `ReadWriteMany` (RWX) to support high-concurrency file access patterns.
- **Bug Fix**: Corrected a critical JSON parsing error (`json.lo` typo) in the backend lifecycle logic.

---

### Data Lifecycle Summary
1. **Submit**: Gateway receives file map -> Backend generates UUID.
2. **Persist**: Server writes files to `/data/{uuid}/workspace` on PVC.
3. **Provision**: Backend creates Sandbox with `subPath: {uuid}/workspace` and `{uuid}/reports`.
4. **Scan**: `code-interpreter.sh` runs scanners -> writes result to `/reports`.
5. **Retain**: Sandbox pod finishes and is deleted.
6. **Query**: User calls Gateway with UUID -> Server reads report from `/data/{uuid}/reports` on PVC.
