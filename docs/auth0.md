# CodeInspector Security Architecture: Auth0 & Edge Gateway Integration

This document outlines the technical architecture, security mechanisms, and solutions implemented to support secure, seamless authentication using **Auth0** across the CodeInspector ecosystem.

---

## 1. Zero Trust Architecture Overview

The system strictly adheres to an "Edge-First" zero-trust validation model where **both** the API infrastructure (AgentGateway) and the Backend Processing API (FastAPI) cooperatively maintain authorization, without overburdening the end-user.

### Components Involved:
1. **Frontend (Dashboard)**: Implements the Auth0 Single Page Application (SPA) SDK to authenticate users via social logins (e.g., Gmail) and fetch a cryptographic RSA-256 JWT.
2. **Envoy Proxy (AgentGateway)**: Implements `Gateway API` with custom `AgentgatewayPolicy`. Acts as the front door, seamlessly transforming session cookies into authenticated bearer headers.
3. **Backend Server (FastAPI)**: Enforces hard cryptographic validation mathematically ensuring the token signatures are intact and natively rejecting unauthenticated users attempting to view API documentation.

---

## 2. Frictionless Security Flow

The major challenge was providing strict edge security for raw APIs while maintaining a smooth UX for users operating through browser-based interfaces (like Swagger UI), which natively rely on Cookies rather than `Authorization: Bearer <token>` headers. 

### The Security Pipeline
1. **Auth0 JWT Retrieval**: When a user logs in, the Auth0 Client explicitly requests an Access Token scoped specifically to `https://code-inspector-api`. This forces Auth0 to return a mathematically verifiable 3-part RS256 JWT rather than an opaque session string.
2. **Cookie Storage**: The dashboard stores this raw JWT inside an `inspector_auth` Strict Cookie.
3. **Browser Navigation**: When the user accesses the `/docs` or the API internally, the browser automatically attaches this cookie.
4. **Envoy Header Transformation**: 
   The AgentGateway executes a granular CEL expression to seamlessly "bridge" the browser's cookie-based world to the backend's header-based world. 
   ```javascript
   'authorization' in request.headers ? request.headers['authorization'] : ('cookie' in request.headers && request.headers['cookie'].contains("inspector_auth=") ? "Bearer " + request.headers['cookie'].split("inspector_auth=")[1].split(";")[0] : "")
   ```
   *Action*: It reads the `inspector_auth` cookie and injects its value into the `Authorization: Bearer` header. Importantly, if the user explicitly provided an `Authorization` header manually, the gateway skips the cookie read to respect their manual input.
5. **Backend Verification**: The FastAPI server executes the `validate_token` dependency. It captures the newly injected `Authorization` header, decodes it, fetches the public JWKs directly from the Auth0 Tenant `.well-known/jwks.json`, and cryptographically proves the signature.

---

## 3. Resolving Core Deployment Issues

During the transition from Javascript-based soft-gating to Infrastructure-level strict gating, several edge-case technical barriers were encountered and patched.

### Bug 1: The "Not enough segments" Error
**Symptoms:** Users received a generic `Invalid token: Not enough segments` when trying to execute an API call from the Swagger UI after logging in with Gmail.
**Root Cause:** The Auth0 SPA client was originally invoking `getTokenSilently()` without explicitly scoping the `authorizationParams: { audience: ... }`. Auth0 interprets this as a generic profile lookup and defaults to returning an **Opaque Token** (a 32-character random string) instead of a JWT. Opaque tokens lack the `header.payload.signature` segments required by PyJWT, causing instant backend crashes.
**Resolution:** The SPA Initialization was patched to strictly demand the matching audience parameter during silent token requests, forcing the generation of a compliant JWT.

### Bug 2: Envoy Overwriting Valid Padlock Tokens
**Symptoms:** If a user deliberately used the "Authorize" Padlock green button in Swagger UI to paste a valid JWT, the API returned 401 Unauthorized.
**Root Cause:** The Envoy transformation rule blindly executed a `set` command. Because Swagger UI sent *both* the explicitly pasted `Authorization` header and the background browser `Cookie`, Envoy extracted the cookie string (which could be expired or malformed) and aggressively overwrote the valid manually-provided header. 
**Resolution:** The Gateway Policy CEL script was upgraded stringently using the ternary `'authorization' in request.headers ? ...` allowing explicit authorization headers to effortlessly bypass the cookie extraction logic.

### Bug 3: The Strict Mode Gateway Bouncer Deadlock
**Symptoms:** Configuring `AgentgatewayPolicy` to `Strict` mode on a route accessed natively by a browser successfully threw an `authentication failure: no bearer token found` error. 
**Root Cause:** Envoy executes JWT Authentication filters *before* executing Route Transformation configurations. The Gateway expected an `Authorization` header immediately, but the script that converted the user's `Cookie` into that header had not triggered yet, creating an impassable deadlock.
**Resolution:** The "Middleware Security Bridge" architecture was launched. 
- The Gateway policies were shifted to `Permissive`, allowing the request to survive long enough for cookie transformation.
- A **FastAPI Middleware** (`CookieAuthRedirectMiddleware`) was designed specifically as the "Bouncer". If an unauthenticated user attempts to visit the documentation routes, the backend safely and instantly executes an `HTTP 307 Temporary Redirect` natively bouncing them back to the dashboard, ensuring airtight security without requiring Gateway deadlocks.

---

## 4. Auth0 Operations & Token Adjustments

**Time-To-Live (TTL):**
Tokens generated by Auth0 default explicitly to a 24-hour expiration duration. The AgentGateway and the Python Backend aggressively honor this restriction. If the `exp` claim has elapsed, execution is blocked outright.

**Customizing Limits:**
Any adjustments to the Auth0 duration parameters must be configured within the Auth0 Admin Console:
1. Navigate to **Applications** ➔ **APIs**.
2. Select the designated API target (`https://code-inspector-api`).
3. Modify the **Token Expiration (Seconds)** setting (e.g., `3600` for 1 hour).
