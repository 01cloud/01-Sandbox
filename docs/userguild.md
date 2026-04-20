# CodeInspector User Guide

Welcome to the **CodeInspector** User Guide! CodeInspector is a Kubernetes-native, security-first platform for executing untrusted code in isolated sandbox environments. It features an Automated Security Scanning Pipeline and a Facade Architecture to easily swap execution backends.

This guide provides instructions on how to use the CodeInspector application's REST APIs to execute code, run security scans, and manage execution backends.

---



## 1. Running Automated Security Scans (`/v1/scan-jobs`)

CodeInspector comes equipped with an advanced asynchronous security scanning pipeline. You can submit code files to be executed and audited simultaneously by tools like Semgrep, Bandit, Gitleaks, etc.

**Endpoint:** `POST /v1/scan-jobs`

**Example Request:**
```bash
curl -X POST "https://dipper-shun-glowing.ngrok-free.dev/backend/opensandbox/v1/scan-jobs" \
  -H "Authorization: Bearer xxxxxxxxx" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "Your code in python/js/go/yaml/bash"
}'
```

**How it Works:**
1. **Instant Response**: The API uses a "fire-and-forget" model, instantly returning a `job_id` and `sandbox_id`.
2. **Background Execution**: A fresh sandbox is provisioned in the cluster (e.g., as a gVisor pod).
3. **Execution & Scanning**: Code is analyzed by the specified tools.
4. **Retrieval**: You can continuously poll or retrieve the final report using the returned `job_id` via a `GET /v1/scan-jobs/{job_id}/report` endpoint.

---



---

## 2. Integrating CodeInspector into Your Application

If you are an external user looking to integrate the CodeInspector APIs into your own application (e.g., a CI/CD pipeline, custom dashboard, or bot), follow this standard integration workflow.

### Integration Workflow

1. **Submit the Scan Job:** Send the source code or files to the `/v1/scan-jobs` endpoint.
2. **Retrieve the `job_id`:** The API will instantly return a JSON response containing a unique `job_id`. 
3. **Poll for Execution Status:** Periodically poll the `/v1/scan-status/{job_id}` endpoint to get real-time terminal-like logs of the sandbox execution process.
4. **Fetch the Final Report:** Once the status logs indicate completion, retrieve the structured JSON report from the `/v1/scan-jobs/{job_id}/report` endpoint.

### Example: Python Integration

Here is a practical example of how to orchestrate the API calls using Python's `requests` library:

```python
import requests
import time

BASE_URL = "https://dipper-shun-glowing.ngrok-free.dev/backend/opensandbox"
HEADERS = {
    "Authorization": "Bearer YOUR_API_TOKEN",
    "Content-Type": "application/json"
}

def run_security_scan():
    # 1. Submit the scan job
    payload = {
        "code": "print('hello world')"
    }
    print("[*] Submitting code for analysis...")
    response = requests.post(f"{BASE_URL}/v1/scan-jobs", json=payload, headers=HEADERS)
    job_id = response.json().get("job_id")
    print(f"[*] Job ID received: {job_id}")

    # 2. Poll for Status and Logs
    print("[*] Tailing execution logs...")
    while True:
        status_res = requests.get(f"{BASE_URL}/v1/scan-status/{job_id}", headers=HEADERS)
        
        # The scan-status endpoint returns real-time plain text logs
        logs = status_res.text
        print(logs)
        
        # Check if the process has finished
        if "Security scans complete." in logs or "FAILED" in logs:
            break
            
        time.sleep(3) # Wait before polling again

    # 3. Retrieve the final report
    print("\n[*] Fetching final JSON security report...")
    report_res = requests.get(f"{BASE_URL}/v1/scan-jobs/{job_id}/report", headers=HEADERS)
    print(report_res.json())

if __name__ == "__main__":
    run_security_scan()
```

---



## 3. System Health & Auditing

You can easily verify the state of CodeInspector using its secondary endpoints:

### Check Health (`/health`)
Verifies the application health status and backend readiness.
```bash
curl https://dipper-shun-glowing.ngrok-free.dev/health
```


---

## 4. Interactive API Guide (Swagger UI)

CodeInspector provides an auto-generated, interactive Swagger UI where you can explore and test all available REST APIs directly from your web browser. 

**Accessing Swagger UI:**
You can access the Swagger UI through the following public URL:
- **Public API Docs**: `https://dipper-shun-glowing.ngrok-free.dev/backend/opensandbox/docs`


**How to Use:**
1. Navigate to the `/docs` URL in your browser.
2. Use google extension tool `modHeader` to add `X-API-Key` header with your secure API token.
3. Expand any endpoint (such as `POST /v1/scan-jobs`).
4. Click **Try it out** to safely execute requests directly from the interface. It allows you to modify the request payload and instantly review the system's responses.

---

## 5. Advanced: Infrastructure Deployment (For Technical Users)

CodeInspector consists of multiple Kubernetes-native components. The easiest and recommended way to deploy the core sandbox engine is via **Helm**.

### Deploying the CodeInspector Stack via Helm

The `codeInspector` directory contains the official all-in-one Helm chart, which installs all necessary components including the Agent Gateway, API Server, OpenSandbox Controller/Server, and CRDs.

**Steps to Deploy:**
1. Navigate to the `codeInspector` Helm chart directory:
   ```bash
   cd codeInspector
   ```
2. Install the system into your current Kubernetes context:
   ```bash
   helm install codeinspector .
   ```

### Testing & Verification

Once deployed, you can verify your K8s deployments and test the APIs natively.

**1. Verifying Pods:**
Ensure the FastAPI server, agent gateway, and OpenSandbox controller/server are running cleanly.
```bash
kubectl get pods -n opensandbox-system
```

**2. Testing Locally via Port-Forwarding (Bypass Gateway):**
If you want to debug the underlying FastAPI REST server without going through the Agent Gateway, you can port-forward the service directly:
```bash
kubectl port-forward svc/sandbox-api-service 8000:80 -n opensandbox-system
```
Then run a health check locally:
```bash
curl http://localhost:8000/health
```

---

> [!TIP]
> Always ensure you provide the `X-API-Key` header with your secure API token when accessing CodeInspector through the Agent Gateway, as strict URL enforcement and authentication policies are in place.
