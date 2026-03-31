# OpenSandbox Controller Installation Guide

## Overview

This document records the changes made to fix the Helm template type mismatch error and the installation process for the OpenSandbox Controller.

## Issues Encountered and Fixes

### 1. Template Type Mismatch Error

**Error Message:**
```
Error: INSTALLATION FAILED: template: opensandbox-controller/templates/deployment.yaml:55:50: executing "opensandbox-controller/templates/deployment.yaml" at <gt .Values.controller.kubeClient.qps 0>: error calling gt: incompatible types for comparison: float64 and int
```

**Root Cause:**
The Helm template was comparing `.Values.controller.kubeClient.qps` (which could be either float64 or int64 depending on how it was set) with `0` (an integer), causing a type mismatch error in Helm's template engine.

**Fix Applied:**
Updated [`deployment.yaml`](OpenSandbox/kubernetes/charts/opensandbox-controller/templates/deployment.yaml:55) to use the `float64` function for type-safe comparisons:

```yaml
# Before (line 55):
{{- if and .Values.controller.kubeClient (gt .Values.controller.kubeClient.qps 0) }}

# After (line 55):
{{- if and .Values.controller.kubeClient (gt (float64 .Values.controller.kubeClient.qps) 0.0) }}

# Before (line 58):
{{- if and .Values.controller.kubeClient (gt .Values.controller.kubeClient.burst 0) }}

# After (line 58):
{{- if and .Values.controller.kubeClient (gt (float64 .Values.controller.kubeClient.burst) 0.0) }}
```

### 2. Namespace Not Found Error

**Error Message:**
```
Error: INSTALLATION FAILED: Unable to continue with install: namespaces "opensandbox-system" not found
```

**Root Cause:**
The chart defaults to deploying to the `opensandbox-system` namespace, which didn't exist in the cluster.

**Fix Applied:**
Created the namespace:
```bash
kubectl create namespace opensandbox-system
```

### 3. Existing Failed Release

**Error Message:**
```
Error: INSTALLATION FAILED: cannot re-use a name that is still in use
```

**Root Cause:**
A previous failed Helm release with the same name existed in the cluster.

**Fix Applied:**
Uninstalled the existing release:
```bash
helm uninstall opensandbox-controller -n default
```

## Installation Commands

### Prerequisites

1. Ensure you have a running Kubernetes cluster
2. Ensure `kubectl` is configured to access your cluster
3. Ensure `helm` is installed (version 3.x recommended)

### Step-by-Step Installation

#### 1. Create the Required Namespace

```bash
kubectl create namespace opensandbox-system
```

#### 2. Install the Helm Chart

```bash
cd OpenSandbox/kubernetes/charts/opensandbox-controller
helm install opensandbox-controller . -f values.yaml --namespace opensandbox-system
```

#### 3. Verify the Installation

Check the controller pod status:
```bash
kubectl get pods -n opensandbox-system -l "app.kubernetes.io/name=opensandbox"
```

Expected output:
```
NAME                                              READY   STATUS    RESTARTS   AGE
opensandbox-controller-manager-xxxxxxxxxx-xxxxx   1/1     Running   0          <age>
```

Check the installed CRDs:
```bash
kubectl get crd | grep -i sandbox
```

Expected output:
```
batchsandboxes.sandbox.opensandbox.io   <timestamp>
pools.sandbox.opensandbox.io            <timestamp>
```

#### 4. Check Helm Release Status

```bash
helm status opensandbox-controller -n opensandbox-system
```

## Customization Options

### Override Namespace

To deploy to a different namespace:
```bash
helm install opensandbox-controller . -f values.yaml --namespace my-namespace
```

### Override Image Repository

To use a custom image:
```bash
helm install opensandbox-controller . -f values.yaml --namespace opensandbox-system \
  --set controller.image.repository=my-registry/controller \
  --set controller.image.tag=v1.0.0
```

### Configure Kubernetes Client Rate Limiter

To customize QPS and burst values:
```bash
helm install opensandbox-controller . -f values.yaml --namespace opensandbox-system \
  --set controller.kubeClient.qps=200 \
  --set controller.kubeClient.burst=400
```

### Disable Leader Election

For single-replica deployments:
```bash
helm install opensandbox-controller . -f values.yaml --namespace opensandbox-system \
  --set controller.leaderElection.enabled=false
```

## Uninstallation

To uninstall the controller:
```bash
helm uninstall opensandbox-controller -n opensandbox-system
```

Note: CRDs are kept by default due to the `crds.keep: true` setting in `values.yaml`. To remove them:
```bash
kubectl delete crd batchsandboxes.sandbox.opensandbox.io
kubectl delete crd pools.sandbox.opensandbox.io
```

## Troubleshooting

### Check Controller Logs

```bash
kubectl logs -n opensandbox-system -l "app.kubernetes.io/name=opensandbox" -c manager
```

### Check Events

```bash
kubectl get events -n opensandbox-system --sort-by='.lastTimestamp'
```

### Describe Controller Pod

```bash
kubectl describe pod -n opensandbox-system -l "app.kubernetes.io/name=opensandbox"
```

## Files Modified

- [`OpenSandbox/kubernetes/charts/opensandbox-controller/templates/deployment.yaml`](OpenSandbox/kubernetes/charts/opensandbox-controller/templates/deployment.yaml:55) - Fixed type mismatch in `gt` function comparisons

## References

- [OpenSandbox Documentation](https://github.com/alibaba/OpenSandbox/blob/main/kubernetes/README.md)
- [Helm Chart Values](OpenSandbox/kubernetes/charts/opensandbox-controller/values.yaml)
- [Example Configurations](https://github.com/alibaba/OpenSandbox/tree/main/kubernetes/config/samples)
