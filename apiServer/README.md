# Sandbox API

A FastAPI-based code execution service that supports **hot-swappable sandbox backends** — switch between Mock, Subprocess, Docker, Firecracker, or E2B without changing a single client request.

---

## Table of Contents

- [Overview](#overview)
- [Project Structure](#project-structure)
- [Available Backends](#available-backends)
- [API Endpoints](#api-endpoints)
- [Local Development](#local-development)
- [Docker](#docker)
- [Kubernetes Deployment](#kubernetes-deployment)
- [Environment Variables](#environment-variables)
- [Usage Examples](#usage-examples)

---

## Overview

The Sandbox API allows you to execute code securely in isolated environments. The backend powering execution can be swapped at runtime — the HTTP interface stays identical regardless of which backend is active.

```
Client  →  POST /run  →  SandboxAPI  →  [Mock | Subprocess | Docker | E2B]
                          (stable)           (swappable)
```

---

## Project Structure

```
.
├── sandbox_fastapi.py     # FastAPI application
├── requirements.txt       # Python dependencies
├── Dockerfile             # Two-stage Docker build
├── docker-compose.yml     # Docker Compose setup
├── .dockerignore          # Docker build exclusions
├── api-deployment.yaml    # Kubernetes manifest
└── README.md              # This file
```

---

## Available Backends

| Backend      | Description                                  | Extra Setup             |
|--------------|----------------------------------------------|-------------------------|
| `mock`       | Fake responses, no dependencies (default)    | None                    |
| `subprocess` | Runs code in local OS subprocess             | Python/Node installed   |
| `docker`     | Ephemeral containers, network-isolated       | Docker daemon running   |
| `firecracker`| MicroVM isolation (stub — bring your own)   | Firecracker installed   |
| `e2b`        | E2B cloud sandboxes                          | `E2B_API_KEY` set       |

---

## API Endpoints

### Execution

| Method | Endpoint | Description                          |
|--------|----------|--------------------------------------|
| `POST` | `/run`   | Execute code in the active backend   |

**Request body:**
```json
{
  "code": "print('hello')",
  "language": "python",
  "timeout": 30
}
```

**Supported languages:** `python`, `javascript`, `bash`

**Response:**
```json
{
  "stdout": "hello\n",
  "stderr": "",
  "exit_code": 0,
  "duration_ms": 12.4,
  "sandbox_id": "abc-123",
  "backend": "subprocess"
}
```

---

### Management

| Method   | Endpoint           | Description                          |
|----------|--------------------|--------------------------------------|
| `GET`    | `/health`          | Health check of the active backend   |
| `GET`    | `/backend`         | Show current backend name & status   |
| `POST`   | `/api/switch`  | Hot-swap to a different backend      |
| `GET`    | `/backends`        | List all registered backends         |

**Switch backend request:**
```json
{
  "backend": "docker",
  "validate": true
}
```

> Setting `"validate": true` runs a health check before switching.
> If the backend is unreachable, the switch is aborted and the current backend stays active.

---

### Sessions

| Method     | Endpoint                  | Description                    |
|------------|---------------------------|--------------------------------|
| `POST`     | `/session`                | Open a persistent session      |
| `GET`      | `/session/{session_id}`   | Get session info               |
| `DELETE`   | `/session/{session_id}`   | Close and clean up a session   |

---

## Local Development

### Prerequisites

- Python 3.12+
- pip or uv

### Steps

```bash
# 1. Clone the repo
git clone <your-repo-url>
cd <your-repo>

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # macOS / Linux
venv\Scripts\activate           # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the server
uvicorn sandbox_fastapi:app --reload --port 8000
```

### Using uv

```bash
uv pip install -r requirements.txt
uvicorn sandbox_fastapi:app --reload --port 8000
```

### Interactive Docs

Once running, open your browser:

| URL                                  | Description          |
|--------------------------------------|----------------------|
| http://localhost:8000/docs           | Swagger UI           |
| http://localhost:8000/redoc          | ReDoc documentation  |
| http://localhost:8000/health         | Health check         |

---

## Docker

### Build & Run

```bash
# Build the image
docker build -t sandbox-api .

# Run the container
docker run -p 8000:8000 sandbox-api
```

### Docker Compose (recommended)

```bash
# Start
docker compose up --build

# Start in background
docker compose up -d --build

# View logs
docker compose logs -f

# Stop
docker compose down
```

### Using the E2B backend with Docker

```bash
docker run -p 8000:8000 -e E2B_API_KEY=your_key_here sandbox-api
```

---

## Kubernetes Deployment

### Prerequisites

- A running Kubernetes cluster (local: [minikube](https://minikube.sigs.k8s.io/) or [kind](https://kind.sigs.k8s.io/))
- `kubectl` configured to point at your cluster
- Docker image pushed to a registry

### Step 1 — Push your image

```bash
docker build -t youruser/sandbox-api:1.0.0 .
docker push youruser/sandbox-api:1.0.0
```

### Step 2 — Update the image in the manifest

Edit `api-deployment.yaml` and update the image field:

```yaml
image: youruser/sandbox-api:1.0.0   # ← replace this
```

Also update the Ingress host if you have a real domain:

```yaml
host: api.yourdomain.com            # ← replace this
```

### Step 3 — Apply the manifest

```bash
kubectl apply -f api-deployment.yaml
```

### Step 4 — Verify the deployment

```bash
# Check all resources in the sandbox namespace
kubectl get all -n sandbox

# Check pod status
kubectl get pods -n sandbox

# Check pod logs
kubectl logs -n sandbox -l app=sandbox-api

# Describe a pod for detailed info
kubectl describe pod -n sandbox -l app=sandbox-api
```

### Step 5 — Access the API

**Option A — Port forward (local testing, no Ingress needed):**
```bash
kubectl port-forward svc/sandbox-api-service 8000:80 -n sandbox
# Visit: http://localhost:8000/docs
```

**Option B — Via Ingress (production):**

Install the NGINX Ingress controller first:
```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml
```

Then visit your configured host: `http://sandbox-api.local`

### What gets deployed

| Resource                    | Purpose                                           |
|-----------------------------|---------------------------------------------------|
| `Namespace/sandbox`         | Isolates all resources                            |
| `ConfigMap`                 | Non-sensitive environment config                  |
| `Secret`                    | API keys (E2B etc.)                               |
| `Deployment` (2 replicas)   | Runs the FastAPI app with rolling updates         |
| `Service` (ClusterIP)       | Stable internal endpoint on port 80               |
| `Ingress`                   | External access via NGINX                         |
| `HorizontalPodAutoscaler`   | Auto-scales pods from 2 to 10 on CPU/memory load  |

### Useful kubectl commands

```bash
# Scale manually
kubectl scale deployment sandbox-api --replicas=4 -n sandbox

# Rolling restart (picks up new image)
kubectl rollout restart deployment/sandbox-api -n sandbox

# Watch rollout status
kubectl rollout status deployment/sandbox-api -n sandbox

# Delete everything
kubectl delete -f api-deployment.yaml
```

---

## Environment Variables

| Variable         | Required | Description                          |
|------------------|----------|--------------------------------------|
| `E2B_API_KEY`    | No       | Required only when using E2B backend |
| `PYTHONUNBUFFERED` | No     | Set to `1` for real-time log output  |
| `PORT`           | No       | Server port (default: `8000`)        |

### Setting the E2B API key in Kubernetes

```bash
# Encode your key
echo -n "your_e2b_key_here" | base64

# Paste the output into api-deployment.yaml under Secret:
#   E2B_API_KEY: <base64-encoded-value>

# Re-apply
kubectl apply -f api-deployment.yaml
```

---

## Usage Examples

### Switch backend then run code

```bash
# 1. Switch to subprocess backend
curl -X POST http://localhost:8000/api/switch \
  -H "Content-Type: application/json" \
  -d '{"backend": "subprocess", "validate": false}'

# 2. Run Python code — same request, different backend
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"code": "print(2 + 2)", "language": "python"}'

# 3. Run JavaScript
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d '{"code": "console.log(\"hello\")", "language": "javascript"}'

# 4. Check active backend
curl http://localhost:8000/backend
```

### Session lifecycle

```bash
# Open a session
curl -X POST http://localhost:8000/session

# Get session info
curl http://localhost:8000/session/<session_id>

# Close the session
curl -X DELETE http://localhost:8000/session/<session_id>
```

---

## License

MIT