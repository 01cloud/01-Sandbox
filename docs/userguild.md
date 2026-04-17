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



## 2. System Health & Auditing

You can easily verify the state of CodeInspector using its secondary endpoints:

### Check Health (`/health`)
Verifies the application health status and backend readiness.
```bash
curl https://dipper-shun-glowing.ngrok-free.dev/health
```


---

## 3. Interactive API Guide (Swagger UI)

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

## 4. Advanced: Infrastructure Deployment (For Technical Users)

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
