# API Security Hardening & Key Management

This document outlines the processes involved in hardening the 01 Sandbox API authentication, managing cryptographic keys, and recovering from database corruption.

## 1. Cryptographic Key Management (RSA/JWT)

To ensure secure token signing (RS256) while maintaining compatibility with external providers like Auth0, we implemented a unified JWKS (JSON Web Key Set) strategy.

### 1.1 Generating a Fresh Key Pair
We transitioned from hardcoded keys to locally generated RSA pairs to ensure maximum entropy and correct formatting.

```bash
# Generate a 2048-bit RSA Private Key
openssl genrsa -out private.pem 2048

# Extract the base64-encoded version for Kubernetes Secrets
base64 -w 0 private.pem
```

### 1.2 Unified JWKS Architecture
The API server needs to validate tokens from two sources:
1.  **Internal API Keys**: Signed by our local private key.
2.  **Auth0 Tokens**: Signed by Auth0's private keys.

We merged these into a single `JWT_PUBLIC_JWKS` environment variable containing:
- The public components (`n` and `e`) of our local `private.pem`.
- The public keys fetched from `https://dev-1u502t8piuwyzb28.us.auth0.com/.well-known/jwks.json`.

---

## 2. Kubernetes Secrets Hardening

Sensitive data was moved out of version-controlled `values.yaml` and into a Kubernetes Secret named `sandbox-api-secret`.

### 2.1 Secret Schema
The following keys are managed exclusively in the cluster:
- `JWT_PRIVATE_KEY`: The raw RSA private key used for signing.
- `JWT_PUBLIC_JWKS`: The unified JSON set for validation.
- `E2B_API_KEY`: External sandbox provider key.

### 2.2 Injection via Deployment
The `apiServer` deployment was updated to source these values securely:

```yaml
env:
  - name: JWT_PRIVATE_KEY
    valueFrom:
      secretKeyRef:
        name: sandbox-api-secret
        key: JWT_PRIVATE_KEY
```

---

## 3. Database Recovery Process (PostgreSQL)

During the stabilization phase, the PostgreSQL volume encountered corruption (`PANIC: could not locate a valid checkpoint record`).

### 3.1 Volume Reset Procedure
1.  **Scale Down**: Stop the crashing pod to release the volume lock.
2.  **Delete PVC**: `kubectl delete pvc postgres-pvc` to wipe corrupted sectors.
3.  **Redeploy**: `helm upgrade` to recreate a fresh, clean volume.
4.  **Re-initialize**: Restart the `sandbox-api` pod to trigger its internal `init_db()` migration scripts, recreating the `api_keys` relation.

---

## 4. CORS Policy Stabilization

We resolved the `Missing Access-Control-Allow-Origin` error by:
1.  **Code Correction**: Updating `CORSMiddleware` in `codeinspectior_api.py` to handle pre-flight `OPTIONS` requests even during backend 500 errors.
2.  **Gateway Consistency**: Ensuring the Agent Gateway passes the `Origin` header through to the backend service.

---

---

## 6. Beginner's Guide: From Scratch to Secure API

If you are setting up the security for a new environment or need to reset everything from the beginning, follow these steps in order.

### Step 1: Generate your Private Key
Open your terminal on your local computer and run:
```bash
openssl genrsa -out private.pem 2048
```
This creates a file called `private.pem`. **Never share this file or upload it to GitHub.**

### Step 2: Prepare your Security Secret
Kubernetes needs this key to sign tokens. Run this command to create/update the "Vault" (Secret) in your cluster:

```bash
# This creates the secret and puts your private key inside it
kubectl create secret generic sandbox-api-secret \
  -n opensandbox-system \
  --from-file=JWT_PRIVATE_KEY=private.pem \
  --save-config --dry-run=client -o yaml | kubectl apply -f -
```

### Step 3: Get the "Public" components for your Website
Your website needs to know the "Public" part of your key to verify users. Run this script to get the `n` and `e` values:

```bash
python3 -c "
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.backends import default_backend
import base64

with open('private.pem', 'rb') as f:
    key = serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())
    nums = key.public_key().public_numbers()
    n = base64.urlsafe_b64encode(nums.n.to_bytes((nums.n.bit_length() + 7) // 8, 'big')).decode().rstrip('=')
    print(f'\nYour Public Component (n): {n}\n')
"
```

### Step 4: Update the Public JWKS
1. Open `values.yaml`.
2. Find the `JWT_PUBLIC_JWKS` section.
3. Replace the `n` value for the key named `code-inspector-key-01` with the one you just printed.
4. Apply the change:
   ```bash
   helm upgrade --install opensandbox ./codeInspector -n opensandbox-system
   ```

### Step 5: Refresh the Pods
Whenever you change keys or secrets, you must "nudge" the API server to read the new values:
```bash
kubectl rollout restart deployment sandbox-api -n opensandbox-system
```

### Summary Checklist for Beginners
*   **Private Key** -> Stays in `private.pem` and the Kubernetes **Secret**.
*   **Public Key (n and e)** -> Stays in the `values.yaml` **JWKS**.
*   **Auth0 Keys** -> Must always remain in the JWKS list so you can still log in.
*   **500 Errors?** -> Usually means the database is down or the Private Key in Step 2 was formatted incorrectly.
