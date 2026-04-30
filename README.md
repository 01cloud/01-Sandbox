# CodeInspector - Sandbox Code Execution Platform

## Overview

CodeInspector is a Kubernetes-based sandbox code execution platform that provides a stable HTTP API for executing code in isolated environments. The system supports multiple sandbox backends (Mock, Subprocess, Docker, E2B, OpenSandbox) and allows hot-swapping backends at runtime without changing client code.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           External Traffic                                  │
│                              (Internet)                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Agent Gateway (Gateway API)                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Gateway: agentgateway-proxy                                         │   │
│  │  - HTTP listener on port 80                                          │   │
│  │  - API Key authentication (Strict mode)                              │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  HTTPRoute: agentgateway-route                                       │   │
│  │  - Hostname: test.sandbox.com                                        │   │
│  │  - Routes to: sandbox-api-service (port 80)                          │   │
│  │  - URL rewrite: / → /                                                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Sandbox Namespace                                 │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Deployment: sandbox-api                                             │   │
│  │  - FastAPI application (Python 3.12)                                 │   │
│  │  - Port: 8000                                                        │   │
│  │  - Replicas: 1 (auto-scales to 10 via HPA)                           │   │
│  │  - Security: non-root, read-only filesystem                          │   │
│  │  - Health checks: /health endpoint                                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Service: sandbox-api-service                                        │   │
│  │  - Type: ClusterIP                                                   │   │
│  │  - Port: 80 → 8000                                                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Ingress: sandbox-api-ingress                                        │   │
│  │  - Host: sandbox-api.local                                           │   │
│  │  - IngressClass: nginx                                               │   │
│  │  - Timeouts: 120s                                                    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Sandbox Backends (Pluggable)                         │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐      │
│  │    Mock      │ │  Subprocess  │ │    Docker    │ │     E2B      │      │
│  │  (Testing)   │ │   (Local)    │ │ (Isolated)   │ │   (Cloud)    │      │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘      │
│                                    │                                        │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  OpenSandbox (via Helm)                                              │   │
│  │  - Controller: manages sandbox lifecycle                             │   │
│  │  - Server: executes code in isolated sandboxes                       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. API Server (FastAPI)

The core component is a FastAPI application that provides a stable HTTP API for code execution. It implements the **Strategy Pattern** to support multiple sandbox backends.

**Key Features:**
- **Hot-swappable backends**: Switch between sandbox implementations at runtime
- **Multiple language support**: Python, JavaScript, Bash
- **Session management**: Create and manage execution sessions
- **Health checks**: Built-in health monitoring endpoints
- **Auto-generated API docs**: Swagger UI at `/docs`

**API Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/run` | POST | Execute code in the active backend |
| `/health` | GET | Check backend health status |
| `/api/switch` | POST | Hot-swap the active backend |
| `/backend` | GET | Get current backend info |
| `/backends` | GET | List all available backends |
| `/api/{name}` | POST | Execute code in a specific backend |

**Supported Backends:**

| Backend | Description | Use Case |
|---------|-------------|----------|
| `mock` | Returns mock responses | Testing, development |
| `subprocess` | Executes code locally via subprocess | Simple execution, no isolation |
| `docker` | Runs code in Docker containers | Isolated execution, resource limits |
| `e2b` | Uses E2B cloud sandbox service | Production, scalable execution |
| `opensandbox` | Uses OpenSandbox operator | Kubernetes-native sandbox management |

**Source Files:**
- [`apiServer/fastapi/sandbox_fastapi.py`](apiServer/fastapi/sandbox_fastapi.py) - Main application
- [`apiServer/fastapi/Dockerfile`](apiServer/fastapi/Dockerfile) - Multi-stage Docker build
- [`apiServer/fastapi/requirements.txt`](apiServer/fastapi/requirements.txt) - Python dependencies

### 2. Kubernetes Deployment

The API server is deployed to Kubernetes with production-grade configurations.

**Deployment Features:**
- **Namespace isolation**: All resources in `sandbox` namespace
- **Rolling updates**: Zero-downtime deployments
- **Auto-scaling**: HorizontalPodAutoscaler (1-10 replicas based on CPU/memory)
- **Security**: Non-root user, read-only filesystem, dropped capabilities
- **Health probes**: Liveness, readiness, and startup probes
- **Resource limits**: CPU 100m-500m, Memory 128Mi-256Mi
- **Pod topology spread**: Distributes pods across nodes

**Kubernetes Resources:**

| Resource | Name | Purpose |
|----------|------|---------|
| Namespace | `sandbox` | Isolates all sandbox resources |
| ConfigMap | `sandbox-api-config` | Non-sensitive environment configuration |
| Secret | `sandbox-api-secret` | Sensitive credentials (E2B API key) |
| Deployment | `sandbox-api` | Runs the FastAPI application |
| Service | `sandbox-api-service` | Stable internal endpoint (ClusterIP) |
| Ingress | `sandbox-api-ingress` | External access via nginx |
| HPA | `sandbox-api-hpa` | Auto-scales based on CPU/memory |

**Source File:**
- [`apiServer/k8s/api-deployment.yaml`](apiServer/k8s/api-deployment.yaml)

### 3. Agent Gateway

The Agent Gateway provides external access to the sandbox API using the Kubernetes Gateway API.

**Components:**

#### Gateway (`agentgateway-proxy`)
- **GatewayClass**: `agentgateway`
- **Protocol**: HTTP on port 80
- **Route admission**: Allows routes from all namespaces

#### HTTPRoute (`agentgateway-route`)
- **Hostname**: `test.sandbox.com`
- **Backend**: `sandbox-api-service` (port 80)
- **URL rewrite**: Preserves path prefix

#### Authentication Policy (`AgentgatewayPolicy`)
- **Mode**: Strict API key authentication
- **Secret reference**: `apikey` secret in `agentgateway-system` namespace

**Source Files:**
- [`agentgateway/agentgateway-proxy.yaml`](agentgateway/agentgateway-proxy.yaml)
- [`agentgateway/agentgateway-httproute.yaml`](agentgateway/agentgateway-httproute.yaml)
- [`agentgateway/agentgatewaypolicy.yaml`](agentgateway/agentgatewaypolicy.yaml)
- [`agentgateway/secret.yaml`](agentgateway/secret.yaml)

### 4. OpenSandbox

OpenSandbox is a Kubernetes operator for managing sandbox lifecycles. It's deployed via Helm charts.

**Components:**
- **Controller**: Manages sandbox lifecycle and resource allocation
- **Server**: Executes code in isolated sandbox environments

**Helm Chart Structure:**
```
opensandbox/
├── Chart.yaml              # Chart metadata
├── values.yaml             # Default configuration
├── charts/
│   ├── opensandbox-controller/
│   │   └── templates/
│   │       └── crds/       # Custom Resource Definitions
│   └── opensandbox-server/
│       └── templates/
```

**Configuration:**
- Controller replicas: 1
- Server replicas: 1
- Log level: info

**Source Files:**
- [`opensandbox/Chart.yaml`](opensandbox/Chart.yaml)
- [`opensandbox/values.yaml`](opensandbox/values.yaml)

### 5. Kind Cluster with gVisor

The development environment uses a Kind (Kubernetes in Docker) cluster with gVisor runtime for enhanced security.

**Cluster Configuration:**
- **Name**: `codebot`
- **Nodes**: 1 control-plane + 1 worker
- **Image**: `kindest/node-gvisor:latest`
- **Runtime**: gVisor (runsc) for container isolation

**gVisor Benefits:**
- Kernel-level isolation
- Reduced attack surface
- Better security than standard containers

**Source Files:**
- [`kindCluster/kind-config.yaml`](kindCluster/kind-config.yaml)
- [`kindCluster/Dockerfile`](kindCluster/Dockerfile)
- [`kindCluster/runtimeclass.yaml`](kindCluster/runtimeclass.yaml)

### 6. MetalLB Load Balancer

MetalLB provides load balancing for bare-metal Kubernetes clusters.

**Configuration:**
- **IP Pool**: `172.18.0.200-172.18.0.250`
- **Mode**: L2 advertisement
- **Namespace**: `metallb-system`

**Source File:**
- [`k8s/metallb/metallb.yaml`](k8s/metallb/metallb.yaml)

## Data Flow

### Code Execution Request Flow

```
1. Client sends POST /run with code payload
   ↓
2. Agent Gateway validates API key
   ↓
3. HTTPRoute routes to sandbox-api-service
   ↓
4. FastAPI receives request
   ↓
5. Active backend executes code:
   - Mock: Returns mock response
   - Subprocess: Runs locally via subprocess
   - Docker: Spins up container, executes, removes
   - E2B: Sends to E2B cloud service
   - OpenSandbox: Delegates to OpenSandbox operator
   ↓
6. Returns RunResponse with stdout, stderr, exit code, duration
```

### Backend Switching Flow

```
1. Client sends POST /api/switch with backend name
   ↓
2. FastAPI validates backend exists
   ↓
3. Performs health check on new backend
   ↓
4. Updates application state
   ↓
5. All subsequent /run requests use new backend
```

## Security

### Container Security
- **Non-root execution**: Runs as user 1000
- **Read-only filesystem**: Prevents runtime modifications
- **Dropped capabilities**: All Linux capabilities dropped
- **No privilege escalation**: Prevents privilege escalation attacks

### Network Security
- **API key authentication**: Strict mode via Agent Gateway
- **Namespace isolation**: Resources isolated in `sandbox` namespace
- **ClusterIP service**: Internal-only access by default

### Sandbox Isolation
- **gVisor runtime**: Kernel-level isolation for containers
- **Resource limits**: CPU and memory constraints
- **Network isolation**: Docker containers run with `--network none`
- **Timeout enforcement**: Prevents runaway executions

## Deployment

### Prerequisites
- Kubernetes cluster (Kind, minikube, or cloud provider)
- kubectl configured
- Helm 3.x
- Docker (for building images)

### Quick Start

1. **Create Kind cluster with gVisor:**
   ```bash
   cd kindCluster
   docker build -t kindest/node-gvisor:latest .
   kind create cluster --config kind-config.yaml
   ```

2. **Install MetalLB:**
   ```bash
   kubectl apply -f https://raw.githubusercontent.com/metallb/metallb/v0.13.7/config/manifests/metallb-native.yaml
   kubectl apply -f k8s/metallb/crds/crds.yaml
   kubectl apply -f k8s/metallb/metallb.yaml
   ```

3. **Install Agent Gateway:**
   ```bash
   kubectl apply -f agentgateway/agentgateway-proxy.yaml
   kubectl apply -f agentgateway/secret.yaml
   kubectl apply -f agentgateway/agentgatewaypolicy.yaml
   kubectl apply -f agentgateway/agentgateway-httproute.yaml
   ```

4. **Install OpenSandbox:**
   ```bash
   cd opensandbox
   helm dependency update
   helm install opensandbox .
   ```

5. **Deploy API Server:**
   ```bash
   cd apiServer/fastapi
   docker build -t fastapi:1.0.0 .
   kubectl apply -f ../k8s/api-deployment.yaml
   ```

6. **Verify deployment:**
   ```bash
   kubectl get pods -n sandbox
   kubectl get svc -n sandbox
   kubectl get ingress -n sandbox
   ```

## Usage Examples

### Execute Python Code
```bash
curl -X POST http://sandbox-api.local/run \
  -H "Content-Type: application/json" \
  -H "X-API-Key: N2YwMDIxZTEtNGUzNS1jNzgzLTRkYjAtYjE2YzRkZGVmNjcy" \
  -d '{
    "code": "print(\"Hello, World!\")",
    "language": "python",
    "timeout": 30
  }'
```

### Switch Backend
```bash
curl -X POST http://sandbox-api.local/api/switch \
  -H "Content-Type: application/json" \
  -H "X-API-Key: N2YwMDIxZTEtNGUzNS1jNzgzLTRkYjAtYjE2YzRkZGVmNjcy" \
  -d '{
    "backend": "docker",
    "validate": true
  }'
```

### Check Health
```bash
curl http://sandbox-api.local/health
```

### List Available Backends
```bash
curl http://sandbox-api.local/backends
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PYTHONUNBUFFERED` | Disable Python output buffering | `1` |
| `APP_ENV` | Application environment | `production` |
| `PORT` | API server port | `8000` |
| `BACKEND_URL_OPENSANDBOX` | OpenSandbox server URL | `http://opensandbox-server.opensandbox-system.svc.cluster.local` |
| `E2B_API_KEY` | E2B cloud sandbox API key | (empty) |

### Resource Limits

| Resource | Request | Limit |
|----------|---------|-------|
| CPU | 100m (0.1 core) | 500m (0.5 core) |
| Memory | 128Mi | 256Mi |

### Auto-scaling

| Metric | Target Utilization |
|--------|-------------------|
| CPU | 70% |
| Memory | 80% |

| Parameter | Value |
|-----------|-------|
| Min Replicas | 1 |
| Max Replicas | 10 |

## Monitoring

### Health Checks

The application provides three types of health probes:

1. **Liveness Probe**: Restarts pod if application hangs
   - Path: `/health`
   - Initial delay: 10s
   - Period: 15s
   - Failure threshold: 3

2. **Readiness Probe**: Routes traffic only when ready
   - Path: `/health`
   - Initial delay: 5s
   - Period: 10s
   - Failure threshold: 3

3. **Startup Probe**: Gives application time to start
   - Path: `/health`
   - Failure threshold: 10
   - Period: 5s

### API Documentation

Auto-generated Swagger UI is available at:
```
http://sandbox-api.local/docs
```

## Development

### Local Development

1. **Install dependencies:**
   ```bash
   cd apiServer/fastapi
   pip install -r requirements.txt
   ```

2. **Run locally:**
   ```bash
   uvicorn sandbox_fastapi:app --reload --port 8000
   ```

3. **Access docs:**
   ```
   http://localhost:8000/docs
   ```

### Testing

```bash
cd apiServer/fastapi
pytest
```

## Troubleshooting

### Common Issues

1. **Pod not starting:**
   ```bash
   kubectl describe pod -n sandbox -l app=sandbox-api
   kubectl logs -n sandbox -l app=sandbox-api
   ```

2. **Backend health check failing:**
   ```bash
   curl http://sandbox-api.local/health
   kubectl exec -n sandbox <pod-name> -- docker info
   ```

3. **Ingress not accessible:**
   ```bash
   kubectl get ingress -n sandbox
   kubectl describe ingress -n sandbox sandbox-api-ingress
   ```

4. **Auto-scaling not working:**
   ```bash
   kubectl get hpa -n sandbox
   kubectl describe hpa -n sandbox sandbox-api-hpa
   ```

## Project Structure

```
codeInspector/
├── README.md                          # This file
├── docs/                              # Platform documentation
│   ├── flow.md                        # End-to-end system sequence & process flow
│   ├── scanning-tools.md              # Security scanning tools overview
│   └── userguild.md                   # Comprehensive user guide
├── apiServer/
│   ├── README.md                      # API server documentation
│   ├── fastapi/
│   │   ├── sandbox_fastapi.py         # Main FastAPI application
│   │   ├── Dockerfile                 # Multi-stage Docker build
│   │   └── requirements.txt           # Python dependencies
│   └── k8s/
│       └── api-deployment.yaml        # Kubernetes manifests
├── agentgateway/
│   ├── agentgateway-proxy.yaml        # Gateway definition
│   ├── agentgateway-httproute.yaml    # HTTP routing rules
│   ├── agentgatewaypolicy.yaml        # Authentication policy
│   └── secret.yaml                    # API key secret
├── k8s/
│   ├── deployment/                    # Additional deployments
│   └── metallb/
│       ├── metallb.yaml               # MetalLB configuration
│       ├── README.md                  # MetalLB documentation
│       └── crds/
│           └── crds.yaml              # MetalLB CRDs
├── kindCluster/
│   ├── kind-config.yaml               # Kind cluster configuration
│   ├── Dockerfile                     # gVisor-enabled node image
│   └── runtimeclass.yaml              # gVisor RuntimeClass
└── opensandbox/
    ├── Chart.yaml                     # Helm chart metadata
    ├── values.yaml                    # Default values
    ├── Chart.lock                     # Dependency lock
    └── charts/
        ├── opensandbox-controller/    # Controller sub-chart
        └── opensandbox-server/        # Server sub-chart
```

## License

See individual component licenses for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## Support

For issues and questions:
- Check the troubleshooting section
- Review component-specific READMEs
- Open an issue on GitHub







curl -X POST http://localhost:8080/v1/sandboxes \
  -H "Content-Type: application/json" \
  -H "OPEN-SANDBOX-API-KEY: your-secure-api-key" \
  -d '{
    "image": {
      "uri": "opensandbox/code-interpreter:v1.0.2"
    },
    "entrypoint": ["/opt/opensandbox/code-interpreter.sh"],
    "timeout": 600,
    "env": {
      "PYTHON_VERSION": "3.11"
    },
    "resourceLimits": {
      "cpu": "1",
      "memory": "2Gi"
    },
    "metadata": {
      "project": "my-ai-agent",
      "environment": "production"
    }
  }'

