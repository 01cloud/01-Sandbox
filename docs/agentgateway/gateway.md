# Agent Gateway (kgateway) Documentation

This document describes the design, routing architecture, installation process, and troubleshooting steps for the `agentgateway` component in the cluster.

---

## 1. Routing Architecture

The `agentgateway` acts as the single entry point (API Gateway) for all traffic coming from the VM node (`10.0.10.9`) or other external clients. It binds to the static IP address **`10.0.10.9`** managed by MetalLB.

```mermaid
graph TD
    Client["VM Client / External (10.0.10.9)"] -->|Requests 10.0.10.9| Proxy["agentgateway-proxy (10.0.10.9)"]

    Proxy -->|HTTP/S /api/v1/01sbx| APIServer["sandbox-api-service:80"]
    Proxy -->|HTTP/S /grafana| Grafana["grafana-service:80"]
    Proxy -->|HTTP/S /prometheus| Prometheus["prometheus-service:9090"]
    Proxy -->|HTTP/S /rabbitmq| RabbitMQMgmt["rabbitmq-service:15672"]

    Proxy -->|TCP:5432| Postgres["postgresql-service:5432"]
    Proxy -->|TCP:6379| Redis["redis-service:6379"]
    Proxy -->|TCP:5672| RabbitMQAMQP["rabbitmq-service:5672"]
```

### Routing Tables & Exposed Ports

| Protocol | External Port | Route Path / Type | Backend Service | Destination Namespace |
| :--- | :--- | :--- | :--- | :--- |
| **HTTP** | `80` | Prefix `/api/v1/01sbx` | `sandbox-api-service:80` | `opensandbox-system` |
| **HTTP** | `80` | Prefix `/grafana` | `grafana-service:80` | `monitoring` |
| **HTTP** | `80` | Prefix `/prometheus` | `prometheus-service:9090` | `monitoring` |
| **HTTP** | `80` | Prefix `/rabbitmq` | `rabbitmq-service:15672` (Rewrite `/` -> `/`) | `opensandbox-system` |
| **HTTPS** | `443` | SSL Offloading | *Same as above* | *Same as above* |
| **TCP** | `5432` | TCP Route | `postgresql-service:5432` | `opensandbox-system` |
| **TCP** | `6379` | TCP Route | `redis-service:6379` | `opensandbox-system` |
| **TCP** | `5672` | TCP Route | `rabbitmq-service:5672` (AMQP) | `opensandbox-system` |

---

## 2. Installation & Setup Process

The installation process has been simplified so that all required CustomResourceDefinitions (CRDs) and the Agent Gateway Controller are bundled directly into the `codeInspector` Helm chart.

### Step 1: Deploy the codeInspector Umbrella Chart
All required components—including the Gateway API CRDs, the Agent Gateway helper CRDs, and the Agent Gateway Controller—are fully bundled as part of the `codeInspector` Helm chart. You can install everything in one go:

```bash
helm upgrade --install codeinspector ./codeInspector -f ./codeInspector/values.yaml -n default
```

### Step 2: Apply GatewayClass Schema Validation Patch
To prevent validation conflicts between the API Server and the controller regarding `status.supportedFeatures`, patch the `gatewayclasses` CRD to strip the strict schema constraint:

```bash
python3 -c '
import yaml, subprocess
crd = yaml.safe_load(subprocess.check_output(["kubectl", "get", "crd", "gatewayclasses.gateway.networking.k8s.io", "-o", "yaml"]))
for v in crd.get("spec", {}).get("versions", []):
    props = v.get("schema", {}).get("openAPIV3Schema", {}).get("properties", {}).get("status", {}).get("properties", {})
    if "supportedFeatures" in props:
        del props["supportedFeatures"]
subprocess.Popen(["kubectl", "apply", "-f", "-"], stdin=subprocess.PIPE).communicate(yaml.dump(crd).encode())
'
```

After applying the patch, restart the controller deployment to ensure it registers the gateway class correctly:

```bash
kubectl rollout restart deployment codeinspector-agentgateway-controller -n default
```

### Step 3: Configure Gateway Bindings in Helm
In your main `values.yaml` file, define the Gateway configuration to request the static IP address:

```yaml
agentgateway:
  enabled: true
  namespace: agentgateway-system
  gateway:
    gatewayClassName: agentgateway
    addresses:
      - value: 10.0.10.9  # Requests this IP from MetalLB
    listeners:
      - protocol: HTTP
        port: 80
        name: http
      - protocol: HTTPS
        port: 443
        name: https
        tls:
          certificateRefs:
            - name: agentgateway-tls
      - protocol: TCP
        port: 5432
        name: postgresql
      - protocol: TCP
        port: 6379
        name: redis
      - protocol: TCP
        port: 5672
        name: rabbitmq-amqp
```

---

## 3. Configuration Details

### ReferenceGrants
Cross-namespace routing in Gateway API requires explicit authorization via `ReferenceGrant`. The route resources inside backend namespaces (such as `monitoring` or `opensandbox-system`) must be allowed to attach to the gateway in the `agentgateway-system` namespace.

Example `ReferenceGrant` for TCP routes in `opensandbox-system`:

```yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-agentgateway-route
  namespace: opensandbox-system
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: TCPRoute
      namespace: agentgateway-system
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: agentgateway-system
  to:
    - group: ""
      kind: Service
```

---

## 4. Troubleshooting & Verification

### Verify Services and IP Allocation
Confirm that the proxy service has successfully bound to the external IP `10.0.10.9`:

```bash
kubectl get svc -n agentgateway-system
```

### Inspect Controller Logs
If the proxy service is not created or updated, check the controller logs for validation issues:

```bash
kubectl logs -n default deployment/codeinspector-agentgateway-controller
```
