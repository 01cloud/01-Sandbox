# CodeInspector Helm Chart

This Helm chart deploys all CodeInspector components in one go.

## Components

The chart includes the following sub-charts:

1. **agentgateway** - Gateway and routing configuration for agent services
2. **apiServer** - FastAPI-based API server with deployment, service, ingress, and HPA
3. **metallb** - MetalLB load balancer configuration with CRDs
4. **kindCluster** - RuntimeClass configuration for gVisor
5. **opensandboxResourcePool** - OpenSandbox resource pool configuration

## Installation

### Install all components

```bash
helm install codeInspector ./codeInspector
```

### Install with custom values

```bash
helm install codeInspector ./codeInspector -f custom-values.yaml
```

### Install specific components only

You can enable/disable individual components using the `enabled` flag:

```bash
# Install only apiServer and agentgateway
helm install codeInspector ./codeInspector \
  --set agentgateway.enabled=true \
  --set apiServer.enabled=true \
  --set metallb.enabled=false \
  --set kindCluster.enabled=false \
  --set opensandboxResourcePool.enabled=false
```

## Configuration

### Global Values

| Parameter | Description | Default |
|-----------|-------------|---------|
| `agentgateway.enabled` | Enable agentgateway component | `true` |
| `apiServer.enabled` | Enable apiServer component | `true` |
| `metallb.enabled` | Enable metallb component | `true` |
| `kindCluster.enabled` | Enable kindCluster component | `true` |
| `opensandboxResourcePool.enabled` | Enable opensandboxResourcePool component | `true` |

### Agent Gateway Values

| Parameter | Description | Default |
|-----------|-------------|---------|
| `agentgateway.namespace` | Namespace for agentgateway resources | `agentgateway-system` |
| `agentgateway.httproute.hostnames` | List of hostnames for HTTPRoute | `["test.sandbox.com"]` |
| `agentgateway.httproute.backendService.name` | Backend service name | `sandbox-api-service` |
| `agentgateway.httproute.backendService.port` | Backend service port | `80` |
| `agentgateway.gateway.gatewayClassName` | Gateway class name | `agentgateway` |
| `agentgateway.policy.apiKeyAuthentication.mode` | API key authentication mode | `Strict` |
| `agentgateway.secret.apiKey` | API key for authentication | `N2YwMDIxZTEtNGUzNS1jNzgzLTRkYjAtYjE2YzRkZGVmNjcy` |

### API Server Values

| Parameter | Description | Default |
|-----------|-------------|---------|
| `apiServer.namespace` | Namespace for apiServer resources | `sandbox` |
| `apiServer.deployment.replicaCount` | Number of replicas | `1` |
| `apiServer.deployment.image.repository` | Docker image repository | `fastapi` |
| `apiServer.deployment.image.tag` | Docker image tag | `1.0.0` |
| `apiServer.deployment.image.pullPolicy` | Image pull policy | `Never` |
| `apiServer.service.type` | Service type | `ClusterIP` |
| `apiServer.service.port` | Service port | `80` |
| `apiServer.service.targetPort` | Service target port | `8000` |
| `apiServer.ingress.enabled` | Enable ingress | `true` |
| `apiServer.ingress.className` | Ingress class name | `nginx` |
| `apiServer.ingress.host` | Ingress host | `sandbox-api.local` |
| `apiServer.hpa.enabled` | Enable HPA | `true` |
| `apiServer.hpa.minReplicas` | Minimum replicas | `1` |
| `apiServer.hpa.maxReplicas` | Maximum replicas | `10` |
| `apiServer.hpa.targetCPUUtilizationPercentage` | CPU target for HPA | `70` |
| `apiServer.hpa.targetMemoryUtilizationPercentage` | Memory target for HPA | `80` |

### MetalLB Values

| Parameter | Description | Default |
|-----------|-------------|---------|
| `metallb.namespace` | Namespace for metallb resources | `metallb-system` |
| `metallb.ipAddressPool.addresses` | List of IP address pools | `["172.18.0.200-172.18.0.250"]` |
| `metallb.l2Advertisement.enabled` | Enable L2 advertisement | `true` |

### Kind Cluster Values

| Parameter | Description | Default |
|-----------|-------------|---------|
| `kindCluster.namespace` | Namespace for kindCluster resources | `kind-cluster` |
| `kindCluster.runtimeClass.name` | RuntimeClass name | `gvisor` |
| `kindCluster.runtimeClass.handler` | RuntimeClass handler | `runsc` |

### OpenSandbox Resource Pool Values

| Parameter | Description | Default |
|-----------|-------------|---------|
| `opensandboxResourcePool.namespace` | Namespace for opensandboxResourcePool resources | `opensandbox-system` |
| `opensandboxResourcePool.pool.name` | Pool name | `my-sandbox-pool` |
| `opensandboxResourcePool.pool.container.image` | Container image | `opensandbox/code-interpreter:v1.0.2` |
| `opensandboxResourcePool.pool.capacitySpec.bufferMin` | Minimum buffer | `3` |
| `opensandboxResourcePool.pool.capacitySpec.bufferMax` | Maximum buffer | `10` |
| `opensandboxResourcePool.pool.capacitySpec.poolMin` | Minimum pool size | `1` |
| `opensandboxResourcePool.pool.capacitySpec.poolMax` | Maximum pool size | `50` |

## Uninstall

```bash
helm uninstall codeInspector
```

## Development

### Lint the chart

```bash
helm lint ./codeInspector
```

### Render templates locally

```bash
helm template ./codeInspector
```

### Render templates with custom values

```bash
helm template ./codeInspector -f custom-values.yaml
```

## Testing