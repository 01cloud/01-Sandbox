# Auth0 Integration & Modular Architecture

This document describes the technical implementation of the Auth0 Identity system and the modularization of the CodeInspector platform.

## Overview

The system evolved from a local-only JWT authentication model into a professional, distributed architecture using **Auth0** for user identity and **NGINX Gateway API** for microservice routing.

---

## 1. Modular Architecture

Originally, the API Server (FastAPI) also hosted the static dashboard HTML. To improve scalability and follow microservices best practices, the system was decoupled into two independent pods:

### Dashboard Pod (`/dashboard`)
- **Technology**: Nginx (Alpine) serving static HTML/JS.
- **Role**: Handles the user-facing login UI and API key generation.
- **Isolation**: Runs as a separate Kubernetes Deployment and Service.

### API Server Pod (`/v1`, `/docs`)
- **Technology**: FastAPI (Python 3.12).
- **Role**: Validates security tokens and proxies requests to security scanners.
- **Cleanup**: Modularized to be strictly "headless" (no UI hosting).

---

## 2. Authentication Flow (RS256 JWT)

The platform uses **Asymmetric Cryptography (RS256)** for security. This ensures that while Auth0 or a local controller can "sign" tokens, the API Server can verify them using only a **Public Key**.

### Multi-Issuer Support
The `validate_token` logic in the backend is designed for a hybrid transition:
1.  **Local Issuer**: Uses hardcoded RS256 Public JWKS for internal/legacy keys.
2.  **Auth0 Issuer**: Automatically detects Auth0 tokens, performs OIDC discovery, and fetches the Auth0 Public JWKS from their CDN.

### Backend Verification Steps
1.  **Unverified Header Check**: Extracts the `kid` (Key ID) and `iss` (Issuer) to determine if the token is from Auth0.
2.  **JWKS Caching**: To ensure sub-millisecond performance, the backend fetches and caches Auth0's public keys. It only refreshes them if a new `kid` is encountered or the cache expires.
3.  **Signature Decoding**: Uses the `cryptography` and `PyJWT` libraries to verify:
    - **Signature**: Must be a valid RS256 signature from the issuer.
    - **Audience (`aud`)**: Must match `https://code-inspector-api`.
    - **Expiration (`exp`)**: Must be in the future.

---

## 3. Frontend Implementation

The developer dashboard integrates the **Auth0 SPA SDK** to provide a secure login experience without requiring a backend for the UI.

### Key Logic:
- **PKCE Flow**: Uses the "Authorization Code Flow with Proof Key for Code Exchange" (PKCE) for secure browser logins.
- **Token Handling**: Uses `getTokenSilently()` to retrieve a JWT Access Token. This token is then displayed to the user as their "API Key".
- **Dynamic Origin**: Automatically detects `window.location.origin` to handle both local and ngrok-based development environments seamlessly.

---

## 4. Infrastructure & Routing

The connection between the Dashboard and the API is managed by the **API Gateway** (`agentgateway`).

### Path-Based Routing (HTTPRoute)
The Gateway API is configured to split traffic based on URL patterns:

| Path Prefix | Targeted Service | Purpose |
| :--- | :--- | :--- |
| `/dashboard` | `dashboard-service` | Serves the Nginx-hosted UI assets. |
| `/` (default) | `sandbox-api-service` | For `/v1/...` and `/docs` (API traffic). |

### Helm Configuration
- **Global Values**: Centralized `AUTH0_DOMAIN` and `AUTH0_AUDIENCE` variables in `values.yaml` serve as the "Source of Truth" for all components.
- **Sub-Charts**: The `dashboard` chart was added as a new sub-chart under `codeInspector/charts/`.

---

## 5. Security Hardening Checklist

To successfully transition to this architecture, the following Auth0 settings were required:
1.  **Application Type**: Must be "Single Page Web Application".
2.  **Grant Types**: Must have "Authorization Code" enabled.
3.  **Allowed Callback URLs**: Must include the ngrok URL (e.g., `https://.../dashboard/index.html`).
4.  **CORS**: `Allowed Web Origins` must include the root ngrok domain.

---

---

## 6. Gateway-Level Authentication Enforcement

To achieve "Zero Trust" architecture, authentication is enforced at the **API Gateway** level using the NGINX Gateway Fabric `AgentgatewayPolicy`. This prevents unauthorized traffic from ever reaching the backend API Server.

### Technical Requirement: JWKS Sanitization
A critical discovery during implementation was that the NGINX JWT validator requires RSA Public Modulus (`n`) and Exponent (`e`) to be strictly in **Base64URL** format (RFC 7515/7517). Standard Auth0 JWKS may include padding (`=`) or characters (`+`, `/`) that NGINX rejects.

**Sanitization Steps Applied:**
1.  **Replace** `+` with `-`.
2.  **Replace** `/` with `_`.
3.  **Trim** all trailing `=` padding characters.

### Configuration (`policy.yaml`)
The Gateway is configured with an `inline` JWKS to ensure immediate availability of the public keys without external dependency during startup:

```yaml
jwtAuthentication:
  providers:
    - issuer: "https://dev-axwc0ui527kw0c5d.us.auth0.com/"
      audiences:
        - "https://code-inspector-api"
      jwks:
        inline: '{"keys":[...sanitized keys...]}'
```

### Verification
If a request arrives without a valid token, the Gateway immediately returns a **401 Unauthorized** with the reason ` JwtAuth`, bypassing the backend entirely.

---

## Summary
By separating the UI and API, moving identity management to Auth0, and enforcing security at the Edge Gateway, the CodeInspector platform now has an enterprise-grade security foundation that can scale to thousands of users without increasing the complexity of the core scanning logic.
