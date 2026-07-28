# API Keys Module Architectural & Operational Guide (`apiServer/fastapi/api_keys`)

This document provides a sequential, end-to-end walkthrough of how the **API Keys System** works across its entire lifecycle—from system startup and key pair generation to token creation, persistence, and request validation.

---

## High-Level Lifecycle Flow

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                PHASE 1: STARTUP & CONFIG                               │
│  [config.py] Resolves/generates RS256 RSA Keypair & Issuer settings                    │
│  [app_state.py] Connects strictly to PostgreSQL & initializes 'api_keys' schema        │
│  [main.py] Mounts 'get_api_keys_router' with dependencies                              │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                               PHASE 2: KEY CREATION & LIFECYCLE                        │
│  1. [POST /v1/api-keys] User submits request (APIKeyCreateRequest)                     │
│  2. Sanitizes key name (bleach + regex) & enforces user quota (Max 5 keys)            │
│  3. Signs RS256 JWT using private key and issuer claims from config.py                │
│  4. Inserts key record into PostgreSQL & adds JTI to Redis set ('active_api_keys')    │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              PHASE 3: REQUEST VERIFICATION                             │
│  1. Client sends request with 'Authorization: Bearer <JWT>'                            │
│  2. [validate_token] Verifies RS256 signature using public JWKS                        │
│  3. Checks Redis set 'active_api_keys' for instant fast-path validation                │
│  4. Enforces Identity Lockdown Guard & updates 'last_used_at' in background            │
└────────────────────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              PHASE 4: KEY REVOCATION & DESTRUCTION                     │
│  1. [DELETE /v1/api-keys/{jti}] Deletes key record from PostgreSQL                     │
│  2. Removes JTI from Redis set ('active_api_keys') for instant cluster-wide revocation │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: System Startup & Configuration

Before any API key can be generated or validated, the server initializes cryptographic parameters and database tables.

### 1.1 Cryptographic Key Resolution (`config.py`)
File: [config.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/config.py#L111-L251)

`jwt_config()` sets up the RS256 signing credentials through a **3-tier resolution strategy**:

1. **Environment Variable (`JWT_PRIVATE_KEY`)**: Reads the private key string and runs `repair_pem()` to fix escaped `\n` characters or missing headers.
2. **Local File (`private.pem`)**: Reads from disk if env var is absent.
3. **Ephemeral RSA-2048 Generation**: Auto-generates a new RSA-2048 keypair if no key is supplied. The key is cached in `_ephemeral_private_key_pem` so it stays identical across all requests during the process lifetime.

```python
# File: apiServer/fastapi/config.py

def jwt_config():
    raw_private_key = os.environ.get("JWT_PRIVATE_KEY", "")
    processed_key = repair_pem(raw_private_key, is_private=True)

    # Strategy 2: Fallback to local file
    if not processed_key and os.path.exists("private.pem"):
        with open("private.pem", "r") as f:
            processed_key = repair_pem(f.read(), is_private=True)

    # Strategy 3: Ephemeral RSA-2048 Generation
    global _ephemeral_private_key_pem
    if not processed_key:
        if _ephemeral_private_key_pem:
            processed_key = _ephemeral_private_key_pem
        else:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa

            _generated_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend(),
            )
            processed_key = _generated_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            ).decode("utf-8")
            _ephemeral_private_key_pem = processed_key

    # Derives public JWKS (JSON Web Key Set) for token verification
    ...

    return {
        "private_key": processed_key,
        "private_key_obj": private_key_obj,
        "public_jwks": public_jwks,
        "algorithm": os.environ.get("JWT_ALGORITHM", "RS256"),
        "expiration_minutes": int(os.environ.get("JWT_EXPIRATION_MINUTES", "60")),
        "issuer": os.environ.get("JWT_ISSUER", "01 Sandbox"),
        "auth0_domain": os.environ.get("AUTH0_DOMAIN", ""),
        "auth0_audience": os.environ.get("AUTH0_AUDIENCE", "code-inspector-api"),
    }
```

---

### 1.2 Strict Database Initialization (`app_state.py`)
File: [app_state.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/core/app_state.py#L87-L168)

During startup, `AppState.init_db()` connects strictly to PostgreSQL and creates the `api_keys` table:

```python
# File: apiServer/fastapi/core/app_state.py

def get_db_conn(self):
    pg_host = os.environ.get("PG_HOST")
    if not pg_host:
        raise RuntimeError(
            "CRITICAL: PostgreSQL environment variable 'PG_HOST' is missing. "
            "PostgreSQL is strictly required across all environments."
        )
    conn = psycopg2.connect(
        host=pg_host,
        port=os.environ.get("PG_PORT", "5432"),
        user=os.environ.get("PG_USER", "postgres"),
        password=os.environ.get("PG_PASSWORD", ""),
        dbname=os.environ.get("PG_DATABASE", "postgres"),
    )
    return InstrumentedConnection(conn)
```

#### PostgreSQL Table Schema
```sql
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,               -- UUID JTI
    name TEXT,                         -- Sanitized Key Name
    backend TEXT,                      -- Target execution backend (Z1_SANDBOX)
    user_id TEXT,                      -- Unique User Subject ID
    user_email TEXT,                   -- Resolved email address
    created_at TEXT,                   -- ISO-8601 Creation Timestamp
    expires_at TEXT,                   -- ISO-8601 Expiration Timestamp
    last_used_at TEXT,                 -- ISO-8601 Last Invocation Timestamp
    is_revoked INTEGER DEFAULT 0,      -- 0 = Active, 1 = Revoked
    prefix TEXT,                       -- Masked key prefix for UI (e.g. ci_a1b2c3d4)
    expiry_notification_sent INTEGER DEFAULT 0
);
```

---

### 1.3 Router Mounting (`main.py`)
File: [main.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/main.py)

Mounts `get_api_keys_router` onto the main FastAPI application:

```python
from api_keys import get_api_keys_router
from auth.token_validator import validate_token
from core.app_state import AppState

state = AppState()
app.include_router(get_api_keys_router(state, validate_token))
```

---

## Phase 2: Data Schemas (`models.py`)

File: [models.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/api_keys/models.py)

Defines request and response structures in order of operational sequence:

```python
from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

# 1. Supported Sandbox Backends
class APIKeyBackend(str, Enum):
    Z1_SANDBOX = "Z1_SANDBOX"

# 2. Key Creation Request Payload
class APIKeyCreateRequest(BaseModel):
    name: str = Field(..., example="Prod-Scanner-Key")
    backend: APIKeyBackend = Field(APIKeyBackend.Z1_SANDBOX)
    ttl_hours: float = Field(1.0, ge=-1.0)      # -1.0 means infinite lifetime
    ttl_seconds: Optional[float] = Field(None, ge=-1.0)
    user_email: Optional[str] = None

# 3. Response Output Upon Generation (One-Time Reveal)
class GenerateAPIResponse(BaseModel):
    api_key: str                                # The full signed JWT secret
    api_key_id: str                             # JTI (UUID)
    status: str

# 4. Masked Key Metadata Record for Dashboard Listing
class APIKeyRecord(BaseModel):
    id: str
    name: str
    backend: str
    user_id: str
    user_email: Optional[str] = None
    created_at: str
    expires_at: str
    last_used_at: Optional[str] = None
    is_revoked: bool = False
    prefix: str                                 # Safe partial prefix (e.g. ci_a1b2c3d4)

class APIKeyListResponse(BaseModel):
    keys: list[APIKeyRecord]
```

---

## Phase 3: Key Lifecycle Endpoints (`router.py`)

File: [router.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/api_keys/router.py)

The router handles key creation, listing, and revocation in a structured sequence.

### Step 3.1: Creating an API Key (`POST /v1/api-keys`)

Execution sequence inside `create_api_key()`:
1. **Input Sanitization**: Cleans HTML tags via `bleach.clean()` and filters special characters using `re.sub(r"[^a-zA-Z0-9\s\-_]", "", ...)`.
2. **Quota Check**: Queries PostgreSQL to ensure `COUNT(*) < 5` per user.
3. **Expiration Calculation**: Calculates `expires_at` based on `ttl_hours` or `ttl_seconds`.
4. **JWT Signing**: Constructs token payload with `sub`, `iss`, `jti`, `exp`, `backend` and signs it using `jwt.encode()` with RS256.
5. **PostgreSQL Insertion**: Inserts key record with a safe UI prefix (`ci_<jti[:8]>`).
6. **Redis Allowlist Sync**: Adds `jti` to the Redis set `active_api_keys`.

```python
# File: apiServer/fastapi/api_keys/router.py

@router.post(
    "/v1/api-keys",
    response_model=GenerateAPIResponse,
    tags=["Security"],
    dependencies=[Depends(validate_token)],
)
async def create_api_key(
    req: APIKeyCreateRequest,
    request: Request,
    payload: dict = Depends(validate_token),
):
    user_id = payload.get("sub")

    # 1. Sanitization
    clean_name = bleach.clean(req.name, tags=[], strip=True).strip()
    sanitized_name = re.sub(r"[^a-zA-Z0-9\s\-_]", "", clean_name).strip() or "Untitled Key"

    # 2. Quota Check (Max 5 keys)
    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM api_keys WHERE LOWER(user_id) = LOWER(%s)", (user_id,))
    count = cursor.fetchone()[0]
    conn.close()

    if count >= 5:
        raise HTTPException(
            status_code=403,
            detail="API Key limit reached (Max 5). Please delete an existing key to create a new one.",
        )

    # 3. Expiration Math
    jti = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.UTC)
    ttl_hours = req.ttl_hours
    if req.ttl_seconds is not None:
        ttl_hours = -1 if req.ttl_seconds == -1 else req.ttl_seconds / 3600.0

    expires_at = (now + datetime.timedelta(days=365 * 100)) if ttl_hours == -1 else (now + datetime.timedelta(hours=ttl_hours))

    # 4. JWT Signing (RS256)
    conf = jwt_config()
    token_payload = {
        "sub": user_id,
        "iat": now,
        "exp": expires_at,
        "iss": conf["issuer"],               # Uses issuer setting ("01 Sandbox")
        "aud": "code-inspector-api",
        "jti": jti,
        "backend": req.backend.value,
    }

    signing_key = conf.get("private_key_obj") or conf["private_key"]
    token = jwt.encode(
        token_payload,
        signing_key,
        algorithm=conf["algorithm"],         # "RS256"
        headers={"kid": "code-inspector-key-01"},
    )

    # 5. Persist to PostgreSQL
    conn = state.get_db_conn()
    cursor = conn.cursor()
    query = """
        INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    cursor.execute(query, (jti, sanitized_name, req.backend.value, user_id, user_email, now.isoformat(), expires_at.isoformat(), f"ci_{jti[:8]}"))
    conn.commit()
    conn.close()

    # 6. Instant Cluster Activation via Redis Set
    if state.use_redis:
        state.redis_client.sadd("active_api_keys", jti)

    return GenerateAPIResponse(api_key=token, api_key_id=jti, status=status_msg)
```

---

### Step 3.2: Listing User API Keys (`GET /v1/api-keys`)

Queries active, unexpired keys for the user and performs email self-healing:

```python
@router.get(
    "/v1/api-keys",
    response_model=APIKeyListResponse,
    tags=["Security"],
    dependencies=[Depends(validate_token)],
)
async def list_user_api_keys(
    request: Request, payload: dict = Depends(validate_token)
):
    user_id = payload.get("sub")
    conn = state.get_db_conn()
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    query = "SELECT * FROM api_keys WHERE LOWER(user_id) = LOWER(%s) AND expires_at > %s"
    cursor.execute(query, (user_id, now_iso))
    rows = [dict(r) for r in cursor.fetchall()]

    # Email Self-Healing (Auth0 Claims -> /userinfo fallback -> DB Update)
    ...
    conn.close()
    return APIKeyListResponse(keys=[APIKeyRecord(...) for row in rows])
```

---

### Step 3.3: Revoking an API Key (`DELETE /v1/api-keys/{jti}`)

Deletes the key record from PostgreSQL and instantly removes `jti` from Redis:

```python
@router.delete(
    "/v1/api-keys/{jti}",
    tags=["Security"],
    dependencies=[Depends(validate_token)],
)
async def delete_api_key(jti: str, payload: dict = Depends(validate_token)):
    user_id = payload.get("sub")
    conn = state.get_db_conn()
    cursor = conn.cursor()

    query = "DELETE FROM api_keys WHERE id = %s AND user_id = %s"
    cursor.execute(query, (jti, user_id))
    rows_deleted = cursor.rowcount
    conn.commit()
    conn.close()

    if rows_deleted == 0:
        raise HTTPException(status_code=404, detail="Key not found or unauthorized")

    # Instant Cluster-Wide Invalidation in Redis
    if state.use_redis:
        state.redis_client.srem("active_api_keys", jti)

    return {"status": "success", "message": f"Key {jti} has been permanently destroyed across the cluster."}
```

---

## Phase 4: Request Verification Flow (`token_validator.py`)

File: [token_validator.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/auth/token_validator.py#L95-L487)

Every API request carrying a `Bearer <token>` goes through `validate_token()` in the following sequential order:

```text
Incoming Request ──► 1. Extract Bearer Token
                           │
                           ▼
                     2. Verify RS256 Signature using Public JWKS
                           │
                           ▼
                     3. Redis Fast Path: sismember("active_api_keys", jti)
                           ├── FOUND (True) ──► Allow Access (Sub-millisecond)
                           └── MISS (False)  ──► Fallback Query to PostgreSQL 'api_keys' table
                                                   │
                                                   ▼
                     4. Enforce Identity Lockdown Guard (cookie_sub == apikey_sub)
                           │
                           ▼
                     5. Trigger Async Task: update_last_used(jti) in background
```

### Reference Code: Verification & Lockdown
```python
# File: apiServer/fastapi/auth/token_validator.py

# 1. Fast Path Validation using Redis Set
if state.use_redis:
    is_valid = state.redis_client.sismember("active_api_keys", jti)

# 2. Fallback Database Check
if not is_valid:
    conn = state.get_db_conn()
    cursor = conn.cursor()
    query = "SELECT is_revoked, expires_at, backend FROM api_keys WHERE id = %s"
    cursor.execute(query, (jti,))
    row = cursor.fetchone()
    conn.close()
    ...

# 3. Identity Lockdown Guard
if auth_header_raw and auth0_cookie:
    if cookie_sub and apikey_sub and cookie_sub != apikey_sub:
        raise HTTPException(
            status_code=403,
            detail="Identity Lockdown: You cannot use an API key that belongs to another user.",
        )

# 4. Background Timestamp Update (Non-Blocking)
asyncio.create_task(update_last_used(jti))
```

---

## Summary Matrix of Module Responsibilities

| File | Primary Responsibility | Sequential Stage |
| :--- | :--- | :--- |
| **[config.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/config.py)** | Resolves/Generates RS256 RSA Keys & Issuer config | **Phase 1 (Startup)** |
| **[app_state.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/core/app_state.py)** | Strictly initializes PostgreSQL pool and `api_keys` schema | **Phase 1 (Startup)** |
| **[models.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/api_keys/models.py)** | Defines request payloads (`APIKeyCreateRequest`) and schemas | **Phase 2 (Data Modeling)** |
| **[router.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/api_keys/router.py)** | Implements key generation (`POST`), listing (`GET`), and revocation (`DELETE`) | **Phase 3 (Key Operations)** |
| **[token_validator.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/auth/token_validator.py)** | Validates JWT signatures, Redis allowlist, and user identity lockdown | **Phase 4 (Request Verification)** |
