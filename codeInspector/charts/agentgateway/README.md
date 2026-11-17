# AgentGateway Helm Chart (`agentgateway`)

## 1. Overview & Problem Statement

In modern distributed microservices and containerized environments, securing, routing, and observing external incoming traffic (North-South traffic) is critical. Historically, Kubernetes relied on the **Ingress API** (`networking.k8s.io/v1/Ingress`), which suffered from significant vendor fragmentation, lack of standardized advanced traffic policies (such as header matching, rate limiting, and request redirection), and monolithic role separation.

The modern cloud-native successor is the **Kubernetes Gateway API** (`gateway.networking.k8s.io`). The `agentgateway` chart configures the declarative Gateway API data-plane routing, traffic authorization, local rate limiting, and cryptographic secret bindings for the **01-Sandbox** platform. It deploys the routing infrastructure that acts as the front door for incoming developer requests, sandboxed code execution calls, and API interactions.

```
       [ External Client / ngrok / Ingress ]
                         │
                         ▼
             ┌───────────────────────┐
             │   Gateway Listener    │  (port 80 HTTP)
             │ (agentgateway-proxy)  │
             └───────────┬───────────┘
                         │
            AgentgatewayPolicy Checks:
            - Rate Limiter (Local, per-HTTPRoute)
            - Private CIDR Check for /metrics endpoints
            - JWT signing key referenced for token validation
                         │
                         ▼
             ┌───────────────────────┐
             │       HTTPRoute       │  (agentgateway-api-route)
             │   PathPrefix: /       │
             └───────────┬───────────┘
                         │ Forward to backend
                         ▼
             ┌───────────────────────┐
             │  sandbox-api-service  │  (Namespace: opensandbox-system, Port 80)
             └───────────────────────┘
```

---

## 2. Core Kubernetes Concepts Explained for Beginners

To understand how this chart operates, you need familiarity with foundational Kubernetes primitives:

- **Custom Resource Definition (CRD):** Extends Kubernetes beyond built-in objects (like Pods and Services). The Gateway API introduces CRDs such as `GatewayClass`, `Gateway`, and `HTTPRoute`. The AgentGateway project introduces `AgentgatewayPolicy`.
- **Gateway API Separation of Concerns:**
  - *Infrastructure Provider / Cluster Operator:* Configures the `GatewayClass` and `Gateway` resource (defining IP addresses, ports, and TLS listeners).
  - *Application Developer:* Configures `HTTPRoute` resources that attach to the Gateway and route traffic to backend Services.
- **`Gateway` (`gateway.networking.k8s.io/v1`):** Represents the physical or virtual data-plane proxy infrastructure (Envoy) listening on specific network interfaces, ports, and protocols.
- **`HTTPRoute` (`gateway.networking.k8s.io/v1`):** Specifies HTTP-level routing logic, including hostname matching, path prefixes, and backend service dispatching.
- **`Secret` (`v1`):** Stores sensitive cryptographic keys (e.g., JWT signing keys or API keys) used by proxies and policies without hardcoding credentials into manifests.
- **Custom Policy (`AgentgatewayPolicy`):** An Envoy-compatible policy attachment binding directly to HTTPRoutes or Gateways to enforce rate limiting and network-level access control.

---

## 3. Template-by-Template Manifest Breakdown

The templates in [codeInspector/charts/agentgateway/templates/](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway/templates/) generate the following resources:

### 3.1. [gateway.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway/templates/gateway.yaml)
- **Kind:** `Gateway` (`gateway.networking.k8s.io/v1`)
- **Metadata:** Name `agentgateway-proxy`, deployed to `.Values.namespace` (default: `agentgateway-system`).
- **Functionality:**
  - References `gatewayClassName: {{ .Values.gateway.gatewayClassName }}` (default: `agentgateway`).
  - Iterates over `.Values.gateway.listeners` to open network listeners (typically HTTP on port 80).
  - Explicitly sets `allowedRoutes.namespaces.from: All`, enabling cross-namespace route binding so `HTTPRoute` objects in other namespaces (such as `opensandbox-system` or `sandbox`) can attach to this gateway.
  - Supports optional TLS termination via `certificateRefs` when `tls` block is present on a listener.

### 3.2. [httproute.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway/templates/httproute.yaml)
- **Kind:** `HTTPRoute` (`gateway.networking.k8s.io/v1`)
- **Metadata:** Name `agentgateway-api-route`.
- **Functionality:**
  - Binds to parent Gateway `agentgateway-proxy` via `parentRefs`.
  - Configures `hostnames` matching `.Values.httproute.hostnames` (e.g., public FQDN or ngrok tunnels).
  - Routes all requests matching `PathPrefix: /` to the backend service `sandbox-api-service` located **cross-namespace** in `opensandbox-system` on port 80.

### 3.3. [policy.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway/templates/policy.yaml)
- **Kind:** `AgentgatewayPolicy` (`agentgateway.dev/v1alpha1`)
- **Metadata:** Name `apikey-auth`.
- **Conditional Rendering:** Only rendered when `.Values.policy.authenticationType` is **not** `"none"`.
- **Functionality:**
  - Enforces **local rate limiting** per HTTPRoute. When rate limiting is disabled in values, it falls back to a permissive ceiling of `10,000 requests / Minutes` to protect against denial-of-service exhaustion without hard blocking.
  - Does **not** perform authentication itself — it references the `Secret` mounted by the proxy for JWT or API key validation.

### 3.4. [policy-metrics.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway/templates/policy-metrics.yaml)
- **Kind:** `AgentgatewayPolicy` (`agentgateway.dev/v1alpha1`)
- **Metadata:** Name `api-metrics-private-policy`.
- **Functionality:**
  - Implements zero-trust network authorization for internal observability endpoints.
  - **Action: `Deny`** — matches requests against `/metrics` or `/api/v1/01sbx/metrics`.
  - Evaluates CEL-like source IP expressions: requests targeting those paths are rejected unless originating from authorized CIDRs defined in `.Values.global.allowedIps` (e.g., WireGuard mesh IPs for the Prometheus scraper).

### 3.5. [secret.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway/templates/secret.yaml)
- **Kind:** `Secret` (`v1`)
- **Metadata:** Dynamically named `jwt-signing-key` (when `authenticationType == "jwt"`) or `apikey` (for API key mode).
- **Secret Types:**
  - JWT: `type: Opaque`, stores `signing-key` from `.Values.secret.signingKey`.
  - API Key: `type: extauth.solo.io/apikey`, stores `api-key` from `.Values.secret.apiKey`.
- **Note:** Template carries a residual label `app: httpbin` from the upstream template origin — functionally harmless but should be updated in production.

---

## 4. Configuration Guide (`values.yaml`)

| Parameter | Type | Default | Description |
|---|---|---|---|
| `namespace` | string | `"agentgateway-system"` | Target Kubernetes namespace for Gateway resources. |
| `httproute.hostnames` | list | `["shininess-enroll-going.ngrok-free.dev"]` | Domain names matched by the HTTPRoute. |
| `httproute.backendService.name` | string | `"sandbox-api-service"` | Destination Kubernetes Service name (unused in current httproute.yaml, hardcoded there). |
| `httproute.backendService.port` | integer | `80` | Destination port on the backend Service. |
| `gateway.gatewayClassName` | string | `"agentgateway"` | GatewayClass managed by the `agentgateway-controller`. |
| `gateway.listeners` | list | `[{"protocol": "HTTP", "port": 80, "name": "http"}]` | Network listener definitions. Add `tls` sub-key for HTTPS termination. |
| `policy.authenticationType` | string | `"jwt"` | Auth mode: `"jwt"` or `"apikey"`. Use `"none"` to skip rendering the rate-limit policy entirely. |
| `policy.rateLimit.enabled` | boolean | `false` | Enable strict rate limiting; when false, a permissive 10,000 req/min ceiling applies. |
| `policy.rateLimit.requests` | integer | `500` | Allowed request quota per time window (when `rateLimit.enabled: true`). |
| `policy.rateLimit.unit` | string | `"Minutes"` | Rate limit window unit (`"Minutes"`, `"Hours"`, `"Seconds"`). |
| `secret.signingKey` | string | `"default-signing-secret-change-me"` | JWT signing key stored in the `jwt-signing-key` Secret. **Must be overridden in production.** |
| `global.allowedIps` | list | `[]` | CIDRs that bypass the `/metrics` deny policy (e.g., `["10.99.0.1"]`). |

---

## 5. Architectural Role in the 01-Sandbox Ecosystem

Within the **01-Sandbox** multi-cluster architecture:
1. **Edge Entry Point:** `agentgateway` sits directly behind the edge ingress or tunnel (e.g., ngrok or AWS Network Load Balancer).
2. **Rate-Limit Gate:** Prevents API abuse and denial-of-service attacks before requests reach compute-heavy components like `apiServer` or sandbox runner pods.
3. **Observability Protection:** By pairing with [policy-metrics.yaml](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/codeInspector/charts/agentgateway/templates/policy-metrics.yaml), it ensures external actors cannot scrape Prometheus telemetry, while the PrimaryHub Prometheus scraper (over WireGuard IP `10.99.0.x`) retains unhindered access.

---

## 6. Practical Debugging & Troubleshooting

### Check Gateway & Route Status
```bash
# Verify Gateway is programmed and has an assigned address
kubectl get gateway -n agentgateway-system
kubectl describe gateway agentgateway-proxy -n agentgateway-system

# Verify HTTPRoute parent binding status
kubectl get httproute -n agentgateway-system
kubectl describe httproute agentgateway-api-route -n agentgateway-system
```
*Look for `Status: True` under `Reason: Accepted` and `Reason: Programmed`.*

### Verify Policy Enforcement
```bash
# Inspect active rate-limiting and access-control policies
kubectl get agentgatewaypolicy -n agentgateway-system
kubectl describe agentgatewaypolicy api-metrics-private-policy -n agentgateway-system
```

### Common Failure Modes
1. **`HTTPRoute` Not Accepted:** The `parentRef` name or namespace doesn't match the `Gateway`, or the Gateway's `allowedRoutes` doesn't permit the route's namespace.
2. **Metrics Request Denied (403 Forbidden) for Prometheus scraper:** The scraper's WireGuard IP is not in `.Values.global.allowedIps`. Verify by checking `kubectl get agentgatewaypolicy api-metrics-private-policy -o yaml`.
3. **Backend Not Found (503 Service Unavailable):** `sandbox-api-service` in `opensandbox-system` has no ready Pod endpoints. Run `kubectl get endpoints sandbox-api-service -n opensandbox-system`.
4. **Rate Limit Triggering Prematurely:** If `rateLimit.enabled: false`, the fallback is 10,000/min. If still triggering, the policy logic may be using the `enabled: true` branch — verify the rendered template with `helm template`.
