# Exposing CodeInspector API via Ngrok & AgentGateway

This document details the configuration steps, bug fixes, and operational instructions required to securely expose the CodeInspector API Server (`sandbox-api-service`) to external users via Ngrok, while strictly enforcing API Key Authentication through the Kubernetes AgentGateway.

## 1. Architectural Overview

The CodeInspector project utilizes an **API Gateway Pattern**. 
Instead of exposing internal microservices explicitly, all traffic is securely funneled through the **AgentGateway** (`agentgateway-proxy`, an Envoy-based Kubernetes Gateway API implementation). 

The AgentGateway is responsible for:
1. Receiving external traffic.
2. Evaluating HTTP Routes (e.g., mapping custom hostnames like your Ngrok domain).
3. Applying Traffic Policies (e.g., the `AgentgatewayPolicy` which strictly enforces API Key Authentication).
4. Securely routing authenticated traffic to the backend `apiServer` microservice located in a different cluster namespace.

---

## 2. Ngrok Setup & Exposing the Gateway

A critical architectural constraint is that Ngrok **must** tunnel directly to the AgentGateway's LoadBalancer IP, rather than the raw backend API locally. If Ngrok tunnels the backend directly (e.g., `ngrok http 8000`), the K8s AgentGateway—and its authentication policies—are completely bypassed.

### Securing the Tunnel
Because the Kubernetes `HTTPRoute` evaluates the incoming hostname, Ngrok must be configured to pass the correct domain.

**The functional Ngrok command used is:**
```bash
ngrok http --domain=dipper-shun-glowing.ngrok-free.dev 172.18.0.200:80
```
- **`172.18.0.200:80`**: The assigned MetalLB LoadBalancer IP exposing the AgentGateway within the Kubernetes cluster.
- **`--domain`**: Ensures the traffic hits the AgentGateway with the `dipper-shun-glowing.ngrok-free.dev` HTTP Host header.

---

## 3. Changes Made to AgentGateway Configurations

To successfully bind this domain to the Gateway, we updated the Helm chart `values.yaml` associated with the AgentGateway HTTPRoute.

1. **Host Mappings**:
   Updated the host configurations located in `codeInspector/values.yaml` and `codeInspector/charts/agentgateway/values.yaml`.
   ```yaml
   agentgateway:
     httproute:
       hostnames:
         - dipper-shun-glowing.ngrok-free.dev
   ```

---

## 4. Mapping the API target to AgentGateway (Bug Fixes)

During configuration, we identified that the `agentgateway-route` was silently dropping traffic with a `BackendNotFound` error because it was attempting to resolve the `sandbox-api-service` locally within the `agentgateway-system` namespace, while the service actually resides in `opensandbox-system`. 

### The Fix
Kubernetes Gateway APIs strictly prohibit cross-namespace routing by default for security reasons. To map the API backend correctly, we applied two structural changes to the Helm charts:

**1. Explicit Namespace Backend References:**
Modified `/charts/agentgateway/templates/httproute.yaml` to specify the target namespace explicitly:
```yaml
  backendRefs:
    - group: ""
      kind: Service
      name: {{ .Values.httproute.backendService.name }}
      namespace: opensandbox-system  # Added explicit cross-namespace reference
      port: {{ .Values.httproute.backendService.port }}
```

**2. Created a ReferenceGrant Security Policy:**
Created `/charts/apiServer/templates/referencegrant.yaml` within the destination namespace. This `ReferenceGrant` explicitly enables trust, allowing the HTTPRoute in `agentgateway-system` to route traffic into `opensandbox-system`.
```yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-agentgateway
  namespace: opensandbox-system
spec:
  from:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    namespace: agentgateway-system # Trust origin
  to:
  - group: ""
    kind: Service # Permitted destination resource
```

After modifying the charts, we ran `helm upgrade codeinspector ./codeInspector -n default` to successfully synchronize the Kubernetes cluster.

---

## 5. Authentication Specifications

Through testing, we finalized the exact mechanisms required to interface correctly with the `AgentgatewayPolicy`:

1. **Authentication Headers**:
   The standard `api-key:` header is not expected by default. The `Strict` policy maps to the base-64 encoded `secret.yaml` via standard HTTP authorization schemas.
   
   The required header format is:
   ```text
   Authorization: Bearer <YOUR_DECODED_API_KEY>
   ```

2. **HTTP Verbs Constraints**:
   The backend Uvicorn/FastAPI instance inherently rejects HTTP `HEAD` HTTP method requests on endpoints designated exclusively for `GET`. When attempting to ping the `/health` endpoint using `curl -I` (which defaults to a HEAD request method), it will result in an unhandled `405 Method Not Allowed`. 
   
   **Use `-X GET` explicitly across API tests.**

## 6. Processes for Authenticating and Accessing the API

When sharing your API, **you should provide the domain `https://dipper-shun-glowing.ngrok-free.dev`.**

**However, there is a very important catch when dealing with strict API Key enforcement!**

Because your AgentGateway is doing its job and strictly enforcing the API key for every request on this domain, you cannot simply type `https://dipper-shun-glowing.ngrok-free.dev/docs` into a normal browser window. If you or someone else tries to visit that URL in Google Chrome or Firefox, the browser does not send the `Authorization: Bearer...` header by default. The AgentGateway will immediately block the browser and return a `401 Unauthorized` blank page.

### A. Accessing via Web Browser (Viewing Swagger `/docs`)
Because of the limitation described above, you must use a workaround if you wish to visually browse the Swagger UI.

**Workaround Process:**
1. Install a header modification extension (e.g., **ModHeader** for Chrome/Firefox).
2. Configure the extension to inject a **Request Header**.
3. Set the Name to `Authorization` and the Value to `Bearer N2YwMDIxZTEtNGUzNS1jNzgzLTRkYjAtYjE2YzRkZGVmNjcy`.
4. Navigate to the `/docs` URL. The AgentGateway will authorize the modified request and load the Swagger UI framework.

### B. Accessing via API Clients (Postman / Insomnia)
API clients are the intended method for manual interactions and testing.
1. Create a new `GET` or `POST` request to your desired endpoint (e.g., `https://dipper-shun-glowing.ngrok-free.dev/health`).
2. Navigate to the **Authorization** configuration tab.
3. Select **Bearer Token** as the authorization type.
4. Input your API key (`N2YwMDIxZTEtNGUzNS1jNzgzLTRkYjAtYjE2YzRkZGVmNjcy`) into the token field.
5. Execute the request.

### C. Accessing via Application Code or CLI script
Automated systems, scripts, and applications must bind the header explicitly to their HTTP transport layers.

**Python Example (`requests` library):**
```python
import requests

url = "https://dipper-shun-glowing.ngrok-free.dev/health"
headers = {
    "Authorization": "Bearer N2YwMDIxZTEtNGUzNS1jNzgzLTRkYjAtYjE2YzRkZGVmNjcy"
}

response = requests.get(url, headers=headers)
print(response.json())
```

**cURL Example (CLI):**
```bash
curl -X GET "https://dipper-shun-glowing.ngrok-free.dev/health" \
  -H "Authorization: Bearer N2YwMDIxZTEtNGUzNS1jNzgzLTRkYjAtYjE2YzRkZGVmNjcy"
```

**cURL Example (CLI): POST request for scan job**
```
curl -X POST "https://dipper-shun-glowing.ngrok-free.dev/api/z1sandbox/v1/scan-jobs" \
  -H "Authorization: Bearer N2YwMDIxZTEtNGUzNS1jNzgzLTRkYjAtYjE2YzRkZGVmNjcy" \
  -H "Content-Type: application/json" \
  -d '{
    "code": "import os\nimport sqlite3\n\ndef run_command(user_input):\n    os.system(f\"echo {user_input}\")\n\ndef db_query(user_name):\n    conn = sqlite3.connect(\"example.db\")\n    cursor = conn.cursor()\n    cursor.execute(f\"SELECT * FROM users WHERE name = {user_name}\")\n"
}'
```
