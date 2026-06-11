# Scan Job Lifecycle & Permanent Deletion

This document outlines the architecture of the scan jobs router modularization and the technical mechanics of the permanent job deletion feature.

---

## 1. Modularized Scan Jobs Architecture

To decouple sandbox CRUD from scan execution, all scan-job specific routes are modularized inside the `scan_jobs` package on the FastAPI gateway backend:

```
apiServer/fastapi/
├── scan_jobs/
│   ├── __init__.py      # Exports the router factory
│   └── router.py        # All endpoints for quick-scan jobs and lifecycle
```

### Endpoints Modularized:
* `POST /v1/scan-jobs`: Runs quick code security scans.
* `GET /v1/scan-jobs/{job_id}/report`: Retrieves the final report JSON.
* `GET /v1/scan-status/{job_id}`: Polls individual job status.
* `GET /v1/job-status` & `GET /v1/job-id`: Helper metadata routes.
* `DELETE /v1/jobs/{job_id}`: Intercepts cancellation or permanent deletion requests.

---

## 2. End-to-End Permanent Deletion Flow

When a user clicks the delete (trash) icon in the UI Dashboard, a permanent delete and purge is executed across the entire application ecosystem.

```
 [ Frontend UI ]
        │
        │ 1. DELETE /v1/jobs/{job_id}?purge=true
        ▼
 [ Gateway API Proxy / Router ]
        │
        ├─► 2. ReusableJobTracker.delete_job(job_id)
        │         ├─► Clear from in-memory state
        │         └─► Delete Redis keys: job:{job_id}:*
        │
        └─► 3. SandboxBackend.delete_scan_job(job_id)
                  │
                  ▼ (Propagates DELETE request)
        [ OpenSandbox Backend Server ]
                  │
                  └─► 4. shutil.rmtree("/data/{job_id}")
                            (Recursively deletes report & workspace from PVC)
```

### 1. Frontend Trigger
In `useJobStore.ts`, the `removeJob` handler triggers an asynchronous DELETE request:
```typescript
fetch(`${apiBase}/v1/jobs/${jobId}?purge=true`, { method: "DELETE" })
```

### 2. Gateway API Router Interception
The `DELETE /v1/jobs/{job_id}?purge=true` endpoint is invoked:
* **Memory & Redis Cleanup**: The gateway's `ReusableJobTracker` purges the job from its active lists and issues Redis commands to delete the following historical cache keys:
  * `job:{job_id}:metadata`
  * `job:{job_id}:status`
  * `job:{job_id}:events`
  * `job:{job_id}:result`
  * `job:{job_id}:cancelled`
* **Backend Delegation**: The gateway sends a downstream HTTP DELETE request to the remote OpenSandbox server.

### 3. PVC Purge (OpenSandbox Server)
The OpenSandbox Server receives the request at `DELETE /scan-jobs/{job_id}`:
* It reads the mount point path of the shared PVC (e.g. `/data/{job_id}`).
* It executes a recursive file-system deletion using `shutil.rmtree()`.
* This completely destroys all uploaded source code workspaces and the generated security scan report JSONs.

### 4. Repository Scan Auto-Cleanup
Repository scans generate temporary child scan jobs on the OpenSandbox server for individual language scanning.
To prevent storage accumulation on the PVC, the gateway's repository scanner in `file_scanner.py` instantly makes a DELETE request to OpenSandbox to destroy the child PVC directory `/data/{child_job_id}` as soon as the report JSON is retrieved.

---

## 3. Server-Side Verification

To manually inspect if job deletion has worked correctly:

### Verify Redis Keys (Gateway Pod)
```bash
redis-cli KEYS "job:{job_id}:*"
# Should return an empty array, indicating all state metadata has been deleted.
```

### Verify PVC Storage (OpenSandbox Server Pod)
```bash
kubectl -n opensandbox-system exec deploy/opensandbox-server -- ls -la /data/{job_id}
# Should return "No such file or directory" or a 404 error code.
```
