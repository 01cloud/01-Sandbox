# Z1 Agent Sandbox Security Architecture: Auth0 & Edge Gateway Integration

This document outlines the technical architecture, security mechanisms, and solutions implemented to support secure, seamless authentication using **Auth0** across the Z1 Agent Sandbox ecosystem.

---

## 1. Zero Trust Architecture Overview

The system strictly adheres to an "Edge-First" zero-trust validation model where **both** the API infrastructure (AgentGateway) and the Backend Processing API (FastAPI) cooperatively maintain authorization, without overburdening the end-user.

### Components Involved:
1. **Frontend (Dashboard)**: Implements the Auth0 Single Page Application (SPA) SDK to authenticate users via social logins (e.g., Gmail) and fetch a cryptographic RSA-256 JWT.
2. **Envoy Proxy (AgentGateway)**: Implements `Gateway API` with custom `AgentgatewayPolicy`. Acts as the front door, seamlessly transforming session cookies into authenticated bearer headers using **Common Expression Language (CEL)**.
3. **Backend Server (FastAPI)**: Enforces hard cryptographic validation (RS256) ensuring the token signatures are intact and natively rejecting unauthenticated users.

---

## 2. Frictionless Security Flow: Cookie to Bearer Transformation

The primary technical challenge was supporting **Swagger UI** and browser-based access, which natively handle Cookies, while the backend requires an `Authorization: Bearer <JWT>` header for standard OIDC/OAuth2 compliance.

### The "Security Bridge" Mechanism

#### A. Token Ingestion (The Browser)
When a user logs in via the dashboard, the Auth0 SDK retrieves a JWT scoped to `https://code-inspector-api`. This token is stored in an `HttpOnly` (or secure JS-readable) cookie named `inspector_auth`.

#### B. Edge Transformation (Envoy CEL)
The **AgentGateway** (Envoy) is configured with an `HTTPRoute` filter that executes a CEL script on every incoming request. This script handles the conversion logic at the edge, before the request ever reaches the backend.

**The Transformation Script:**
```javascript
'authorization' in request.headers ? 
    request.headers['authorization'] : 
    ('cookie' in request.headers && request.headers['cookie'].contains("inspector_auth=") ? 
        "Bearer " + request.headers['cookie'].split("inspector_auth=")[1].split(";")[0] : 
        "")
```

**Technical Logic:**
1. **Priority Check**: It first checks if a manual `Authorization` header exists (e.g., from a CLI tool or the Swagger "Authorize" padlock). If found, it **respects the manual header** and skips the cookie logic.
2. **Cookie Extraction**: If no header is present, it scans the `Cookie` header for the `inspector_auth=` key.
3. **String Manipulation**: It uses `.split()` to isolate the JWT from the cookie string.
4. **Header Injection**: It prepends `"Bearer "` and injects the result into a new `Authorization` request header.

---

## 3. Backend Enforcement: RS256 Cryptographic Validation

Once the header reaches the **FastAPI Backend**, it undergoes a rigorous multi-step validation process using the `PyJWT` library.

### The RS256 Handshake
Unlike symmetric algorithms (HS256) which share a secret key, Z1 Agent Sandbox uses **RS256 (RSA Signature with SHA-256)**. This is an asymmetric scheme using a Public/Private key pair.

1. **JWKS Discovery**: The backend does not store the secret key. Instead, it periodically fetches the **JSON Web Key Set (JWKS)** from Auth0's public endpoint:
   `https://[auth0-tenant].us.auth0.com/.well-known/jwks.json`
2. **Key Matching**: It reads the `kid` (Key ID) from the JWT header and finds the matching Public Modulus (`n`) and Exponent (`e`) in the JWKS.
3. **Signature Verification**: It mathematically proves that the JWT was signed by the Auth0 Private Key by verifying the payload against the provided signature using the Public Key.
4. **Claims Validation**:
   - `iss` (Issuer): Must match the expected Auth0 Tenant URL.
   - `aud` (Audience): Must match `https://code-inspector-api`.
   - `exp` (Expiration): The current UTC time must be before the expiration timestamp.

---

## 4. Resolving Integration Edge-Cases

### The "Opaque Token" Bug
**Issue**: Using `getTokenSilently()` without an `audience` resulted in an Opaque Token (short string) instead of a JWT.
**Fix**: Patched the SPA Init to strictly demand `audience: 'https://code-inspector-api'`, forcing Auth0 to issue a verifiable 3-part JWT.

### The Middleware Bouncer
**Issue**: Strict Gateway mode caused 401s for browser users because the JWT Authentication filter ran *before* the Cookie Transformation filter.
**Fix**: Switched Gateway to `Permissive` mode and moved the final "Bouncer" logic to a **FastAPI Middleware**.
- If a user visits `/docs` and the `validate_token` check fails, the middleware issues an **HTTP 307 Redirect** back to the dashboard login page, ensuring a smooth UX while maintaining 100% security coverage.

---

## 5. Operations & Token Adjustments

- **TTL**: Tokens are currently valid for 24 hours.
- **Revocation**: Handled by Auth0. If a user is deactivated in the console, their JWT signatures will fail validation once refreshed or expired.
- **Admin Access**: Security auditors can adjust scopes and roles directly within the Auth0 Dashboard under **User Management** ➔ **Roles**.

---

## 6. Technical Summary & Final Architecture


### 1. The Authentication Handshake (Auth0)
*   **Identity Provider**: Users authenticate via Auth0 (Social Logins like Gmail).
*   **Token Type**: Auth0 issues an **RS256 JWT** (cryptographically signed with a private key held only by Auth0).
*   **Storage**: The dashboard stores this JWT in a browser cookie named `inspector_auth`.

### 2. The "Security Bridge" (Cookie to Bearer Transformation)
This is the most critical part of the system. It allows a browser-based user to talk to a header-based API without manual token pasting.
*   **The Component**: **Agent Gateway** (Envoy) sitting at the edge.
*   **The Code**: A **Common Expression Language (CEL)** script located in the Helm template: [`agentgateway/templates/policy.yaml`](file:///home/berrybytes/Desktop/codeInspector/codeInspector/charts/agentgateway/templates/policy.yaml).
*   **The Logic**: 
    1.  Gateway checks if an `Authorization` header already exists.
    2.  If not, it scans the incoming `Cookie` header for `inspector_auth=`.
    3.  It extracts the JWT string and **injects** a new `Authorization: Bearer <JWT>` header into the request context.
    4.  The request is then forwarded to the backend.

### 3. Backend Enforcement (FastAPI)
*   **Validation**: The backend uses the `validate_token` dependency.
*   **Cryptography**: It fetches the **Public JSON Web Keys (JWKS)** from your Auth0 tenant.
*   **Verification**: It mathematically proves that the JWT signature is valid using the Public Key.
*   **The Bouncer (Middleware)**: If a user visits `/docs` and the token is missing or invalid, the backend issues an **HTTP 307 Temporary Redirect** back to the dashboard/login page.

### 4. Production Hardening
We have hardened the `values.yaml` for production deployment:
*   **Removed `JWT_PRIVATE_KEY`**: Since Auth0 handles the signing, your backend no longer stores any private keys. It only stores the **Public Keys (JWKS)** for validation.
*   **Zero-Trust**: Security is enforced as close to the sandbox as possible (at the FastAPI level), ensuring that even if the gateway is bypassed, the request remains protected.

### 5. Observability & Verification
We verified the entire flow by analyzing the `sandbox-api` logs:
1.  **Stage 1 (Unauthenticated)**: `GET /docs ... 307 Temporary Redirect` (The Bouncer sends you to log in).
2.  **Stage 2 (Authenticated)**: `GET /docs ... 200 OK` (The Gateway injected the header, and the Backend validated it).

---
*Last Updated: 2026-04-23 by Antigravity*
