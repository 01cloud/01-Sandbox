# AgentGateway with kgateway (MCP + Azure AD JWT Authentication)

Agentgateway is an open-source, AI-first data plane that provides connectivity for agents, MCP tools, LLMs, and inference workloads in any environment.

In Kubernetes environments, you can use **kgateway** as the control plane to quickly spin up and manage the lifecycle of agentgateway proxies. The control plane translates Kubernetes Gateway API and custom resources such as `AgentgatewayPolicy` and `AgentgatewayBackend` into proxy configuration for the data plane.

---

## 📦 Architecture Overview

- **kgateway (Control Plane)**: Manages Gateway API + Agentgateway CRDs
- **agentgateway (Data Plane)**: Handles runtime traffic for agents/MCP/LLMs
- **MCP Server**: Backend service exposed via agentgateway
- **Azure AD**: Provides JWT authentication and authorization

---

## 🚀 Installation & Setup

### 1. Deploy Kubernetes Gateway API CRDs

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml
```

---

### 2. Deploy kgateway CRDs

```bash
helm upgrade -i \
  --create-namespace \
  --namespace kgateway-system \
  --version v2.2.0-main \
  kgateway-crds \
  oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds
```

---

### 3. Install kgateway Control Plane (Enable AgentGateway)

```bash
helm upgrade -i \
  --namespace kgateway-system \
  --version v2.2.0-main \
  kgateway \
  oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --set agentgateway.enabled=true \
  --set controller.image.pullPolicy=Always
```

---

### 4. Verify Installation

```bash
kubectl get pods -n kgateway-system
```

---

## 🌐 Create AgentGateway Proxy

Create a Gateway using the `agentgateway` GatewayClass.

### `mcp-gateway-proxy.yml`

```yaml
kind: Gateway
apiVersion: gateway.networking.k8s.io/v1
metadata:
  name: agentgateway
  labels:
    app: agentgateway
spec:
  gatewayClassName: kgateway
  listeners:
  - protocol: HTTP
    port: 8080
    name: http
    allowedRoutes:
      namespaces:
        from: All
```


---

## ⚙️ Confirm AgentGateway Feature Flag

```bash
helm get values kgateway -n kgateway-system
```

Expected output:

```yaml
agentgateway:
  enabled: true
```

---

## 🧠 Deploy MCP Server

### `mcp-example-deployment.yml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mcp-example
spec:
  selector:
    matchLabels:
      app: mcp-example
  template:
    metadata:
      labels:
        app: mcp-example
    spec:
      containers:
      - name: mcp-example
        image: kamalberrybytes/mcp:1.0.0
        imagePullPolicy: Always
---
apiVersion: v1
kind: Service
metadata:
  name: mcp-example-service
  labels:
    app: mcp-example
spec:
  selector:
    app: mcp-example
  ports:
  - port: 8000
    targetPort: 8000
    appProtocol: kgateway.dev/mcp
```

---

## 🔐 Azure AD JWT Authentication Policy

### `agent-gateway-policy.yml`

```yaml
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayPolicy
metadata:
  name: azure-mcp-authn-policy
spec:
  targetRefs:
    - name: agentgateway
      kind: Gateway
      group: gateway.networking.k8s.io
  traffic:
    jwtAuthentication:
      mode: Strict
      providers:
        - issuer: https://sts.windows.net/TENANT_ID/
          jwks:
            remote:
              uri: https://login.microsoftonline.com/TENANT_ID/discovery/keys
              cacheDuration: 5m
          audiences:
            - "api://CLIENT_ID"
```

---

## 🔗 Create MCP Backend

### `mcp-example-backend.yml`

```yaml
apiVersion: agentgateway.dev/v1alpha1
kind: AgentgatewayBackend
metadata:
  name: mcp-example-backend
spec:
  mcp:
    targets:
    - name: mcp-example-target
      static:
        host: mcp-example-service.default.svc.cluster.local
        port: 8000
        protocol: StreamableHTTP
```

---

## 🚦 Create HTTPRoute

### `mcp-example-http-route.yml`

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-example
spec:
  parentRefs:
  - name: agentgateway
  rules:
   - matches:
       - path:
           type: PathPrefix
           value: /mcp/mcp-example
     backendRefs:
       - name: mcp-example-backend
         group: agentgateway.dev
         kind: AgentgatewayBackend
```

---

## 🌍 Port Forwarding

```bash
kubectl port-forward svc/agentgateway 8000:8080 --address 0.0.0.0
```

---

## 🔑 Generate Azure AD Token

```bash
curl -X POST https://login.microsoftonline.com/TENANT_ID/oauth2/v2.0/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=CLIENT_ID" \
  -d "client_secret=CLIENT_SECRET" \
  -d "scope=api://CLIENT_ID/.default" \
  -d "grant_type=client_credentials"
```

---

## 📌 Notes

- Create separate manifests for multiple MCP servers:
  - `[mcpservername].deployment.yml`
  - `[mcpservername].backend.yml`
  - `[mcpservername].http-route.yml`

- Validate CRDs:

```bash
kubectl api-resources
```

---

## 📚 Reference

https://kgateway.dev/docs/agentgateway/main/mcp/static-mcp/