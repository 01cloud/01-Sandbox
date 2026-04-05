# OpenSandbox & CodeInspector Setup and Fixes

## 1. Extracting Standalone Docker Build Contexts

To allow independent Docker image builds rather than relying solely on the provided remote registry images, standalone build directories containing the necessary source code and `Dockerfile` contexts were placed inside their respective helm charts.

**Server Docker Build Context:**
- Location: `kubernetes/charts/opensandbox-server/docker-build/`
- **To build locally:**
  ```bash
  cd /home/kamal/Desktop/OpenSandbox/kubernetes/charts/opensandbox-server/docker-build
  docker build -t opensandbox-server:local .
  ```

**Controller Docker Build Context:**
- Location: `kubernetes/charts/opensandbox-controller/docker-build/`
- **To build locally:**
  ```bash
  cd /home/kamal/Desktop/OpenSandbox/kubernetes/charts/opensandbox-controller/docker-build
  docker build -t opensandbox-controller:local .
  ```

---

## 2. Resolving CrashLoopBackOff Issues

During the deployment of the `codeInspector` helm chart, the `opensandbox-server` and `opensandbox-controller` pods encountered `CrashLoopBackOff` states. The following underlying issues were successfully identified and fixed natively within the `codeInspector` subcharts:

### Issue 2.1: Controller Missing CRDs and RBAC
- **Symptoms**: The `opensandbox-controller` failed to start as it threw internal API errors regarding the missing `BatchSandbox` and `Pool` CustomResourceDefinitions (CRDs), and encountered standard "Forbidden" RBAC limits.
- **Fix Applied**: 
  - Rendered and manually applied the CRDs generated inside the `templates/crds/` folder.
  - Updated the standalone `ClusterRole` inside `kubernetes/charts/codeInspector/charts/opensandbox/templates/controller.yaml` to explicitly append the required `sandbox.opensandbox.io` apiGroup permissions.

### Issue 2.2: Controller Liveness/Readiness Probe Errors
- **Symptoms**: The controller pods failed their liveness checks and were aggressively restarted by Kubernetes.
- **Fix Applied**: Corrected the port mappings for the controller probes in `controller.yaml` from port `8080` to the actual metrics and healthz port `8081`. We also adjusted the paths from `/health` and `/ready` to standard controller conventions `/healthz` and `/readyz`.

### Issue 2.3: Server `kube-config` Mounting Issue
- **Symptoms**: The `opensandbox-server` crashed immediately with the exception `Invalid kube-config file. No configuration found.` This happened because the server image bakes in a TOML config anticipating `kubeconfig_path = "~/.kube/config"`, which the pod does not own.
- **Fix Applied**: Injected an inline `ConfigMap` template into `kubernetes/charts/codeInspector/charts/opensandbox/templates/server.yaml` that specifies `kubeconfig_path = ""` (forcing secure `<incluster>` configuration hooks). We then explicitly mounted this ConfigMap as a volume.

### Issue 2.4: Server Readiness Probe Typo
- **Symptoms**: The server remained `0/1 Ready` indefinitely. Because it never became healthy according to Kubernetes, the `RollingUpdate` controller was blocked from gracefully scaling down the previous broken replica sets, leaving them to crash infinitely.
- **Fix Applied**: Detected a typo in the `readinessProbe` path within the `codeInspector` server deployment. We updated the endpoint from `/ready` (which returned standard 404s) to the actual FastAPI implemented `/health` route.

---

## 3. Full Deployment Instructions

If you need to deploy the application cleanly from scratch using these baked-in fixes, please follow the steps below:

### 1. Deploy the CodeInspector Helm Chart
Install or upgrade the `codeInspector` release. The fixes we appended to the internal `opensandbox` subchart (CRD auto-installation, RBAC, ConfigMaps, and probe fixes) will automatically attach perfectly:

```bash
cd /home/kamal/Desktop/OpenSandbox/kubernetes/charts
helm upgrade --install codeinspector ./codeInspector -n default
```

### 2. Verify Deployment
Check the generated system namespace to guarantee the controller and server have stabilized to `1/1 Running`:

```bash
kubectl get pods -n opensandbox-system
```

---

## 4. Fix for Missing BatchSandbox CRD Error

### Issue
The `opensandbox-controller` pod was stuck in a `CrashLoopBackOff` state. Checking the pod logs revealed the following error:
`no matches for kind "BatchSandbox" in version "sandbox.opensandbox.io/v1alpha1"`

This indicated that the `BatchSandbox` (and potentially `Pool`) CustomResourceDefinitions (CRDs) were missing from the cluster.

### Fix
The CRDs for `BatchSandbox` and `Pool` were originally defined as Helm templates inside the `opensandbox-controller` chart (under `templates/crds/`). Because they contained Helm template syntax `{{ ... }}`, Helm's native CRD loader ignored them, reducing them to standalone components that broke core subchart functionality.

We resolved this by using `helm template` to render the CRDs into purely valid YAML manifests, and directly injected the compiled definitions into `codeInspector/charts/opensandbox/crds/crds.yaml`.
By leveraging Helm 3's native CRD mechanism, Helm automatically identifies all specs within a chart's (and its subchart's) `crds/` directory, cleanly deploying them *before* rendering any associated templates. 

Now, running `helm install` natively deploys the required Sandbox CRDs, allowing the controller to immediately initialize its workers and transition to a healthy `1/1 Running` state.

