# OpenSandbox (All-in-One)

This Helm chart bundles both the **OpenSandbox Controller** and **OpenSandbox Server** into a single deployment for simplified installation.

## Prerequisites

- Kubernetes 1.21.1+
- Helm 3.0+

## Quick Start

```bash
# Add repo (if published)
wget https://github.com/alibaba/OpenSandbox/releases/download/helm/opensandbox/0.1.0/opensandbox-0.1.0.tgz

# unzip 
 tar -xvf opensandbox-0.1.0.tgz 

# Install all components in one command
helm install opensandbox-controller-server ./opensandbox \
  -f ./opensandbox/values.yaml \
  --namespace opensandbox-system \
  --create-namespace
```


## Components Included

This chart installs:

1. **OpenSandbox Controller** (`opensandbox-controller`)
   - Manages Pool and BatchSandbox CRDs
   - Handles resource pooling and batch delivery
   - Runs as a Kubernetes operator

2. **OpenSandbox Server** (`opensandbox-server`)
   - Provides REST API for sandbox lifecycle management
   - Connects to the controller for resource orchestration
   - Optional ingress gateway support

## Configuration

Most configuration is inherited from the sub-charts. See individual chart documentation:

- [Controller Configuration](../opensandbox-controller/README.md)
- [Server Configuration](../opensandbox-server/README.md)

### Override Sub-chart Values

You can customize values for each component using the sub-chart name as prefix:

```yaml
# values.yaml
opensandbox-controller:
  controller:
    logLevel: debug
    replicaCount: 2

opensandbox-server:
  server:
    replicaCount: 2
    gateway:
      enabled: true
      host: gateway.example.com
```

Then install with:

```bash
# Ensure dependencies are built first
helm dependency build opensandbox

helm install opensandbox ./opensandbox -f values.yaml
```

## Upgrade

```bash
# Ensure dependencies are up to date
helm dependency build opensandbox

helm upgrade opensandbox ./opensandbox -n opensandbox-system
```

## Uninstall

```bash
helm uninstall opensandbox -n opensandbox-system
```

Note: CRDs are kept by default. To remove them:

```bash
kubectl delete crd batchsandboxes.sandbox.opensandbox.io
kubectl delete crd pools.sandbox.opensandbox.io
```

