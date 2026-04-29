# Developer API Key & Security Architecture

This document outlines the multi-layered security architecture used by the **01 Sandbox** platform to ensure secure, identity-locked, and time-bound code execution.

---

## 1. Identity Path Alignment (Cross-User Isolation)

To prevent "Key Hijacking" (where User B attempts to use User A's leaked API key), the system enforces a mandatory **Identity Path Alignment** check. 

### How it works:
When a request is made through the dashboard, the backend performs a dual-identity verification:

1.  **Header Extraction**: The system decodes the `Authorization: Bearer` header (the Developer API Key). It extracts the `sub` claim (the unique Auth0 User ID).
2.  **Cookie Extraction**: The system decodes the `inspector_auth` session cookie (the Auth0 Dashboard Session). It extracts the `sub` claim from this as well.
3.  **Lockdown Check**: The system performs an equality check: `apikey.sub == session.sub`.

> [!IMPORTANT]
> If a mismatch is detected, the system issues a **403 Forbidden** response. Even a cryptographically valid key is rejected if it does not belong to the user currently logged into the dashboard.

---

## 2. Expiration & TTL Enforcement

All Developer API Keys are time-bound by either a user-defined TTL (e.g., 1 hour) or a system default.

### Cryptographic Expiry (`exp` claim)
Every generated key is a JWT containing an `exp` (Expiration Time) claim.
- **Validation**: The `apiServer` use the `PyJWT` library to decode the token.
- **Automatic Sentry**: The `jwt.decode()` function compares the `exp` claim against the current server time.
- **Immediate Rejection**: If the current time is greater than the `exp` time, the library throws an `ExpiredSignatureError`, resulting in a **401 Unauthorized** response.

---

## 3. Defense-in-Depth: Roles & Responsibilities

The system uses a layered "Sentry" approach to distribute security overhead and maximize protection.

### Layer 1: AgentGateway (The Edge Sentry)
The gateway sits at the network perimeter and handles high-speed gatekeeping.
- **Responsibilities**: 
    - Cookie-to-Token transformation for "Zero-Touch" redirections.
    - Standard cryptographic verification (Signature and `exp` time).
- **Goal**: Stop invalid or expired requests before they reach internal microservices or consume sandbox resources.

### Layer 2: API Server (The Application Sentry)
The API Server handles the business logic and identity-specific security.
- **Responsibilities**:
    - **Identity Path Alignment**: Performing the `sub` equality check.
    - **Revocation Check**: Consulting the Postgres/Redis database to see if a key was manually revoked before its `exp` time expired.
- **Goal**: Ensure that valid requests are being made by the authorized owner of the key.

---

## 4. Security Philosophy

This architecture moves from **Stateless Authentication** (simply checking if a key is valid) to **Zero-Trust Identity Awareness** (verifying who is using the key and where they are using it from).

- **Principle of Least Privilege**: Keys are scoped to specific backends (e.g., `Z1_SANDBOX`).
- **One-Time Reveal**: API keys are only shown once during creation and are thereafter stored only as hashes or masked records in the database.
- **Identity Lockdown**: By pinning keys to Auth0 sessions, we effectively neutralize the risk of leaked keys being used by malicious third parties via the dashboard UI.
