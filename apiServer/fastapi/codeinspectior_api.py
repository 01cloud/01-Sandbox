"""
codeinspector_api.py
====================

A clean, simplified proxy API specifically designed to forward traffic 
to the internal OpenSandbox backend.

It removes all hardcoded sandbox routing (like /sandboxes, /batched),
instead relying completely transparently on `/api/z1sandbox/{proxy_path}` 
to communicate with the `opensandbox-server` kubernetes service.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status, Depends
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, StreamingResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
import json
import re
import bleach

# Modular Imports
from models import (
    RunRequest, RunResponse, StatusResponse, 
    CreateSandboxRequest, SandboxResponse,
    ScanJobRequest, ScanJobResponse,
    GenerateAPIResponse, APIKeyCreateRequest,
    APIKeyListResponse, APIKeyRecord
)
from config import opensandbox_base_url, opensandbox_headers, gateway_secret_config, jwt_config
from backends import SandboxBackend, GenericHTTPBackend

import secrets
import base64
import jwt
import datetime
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
import redis
import asyncio
import uuid
from typing import List, Dict
 

# ─────────────────────────────────────────────
# 1. Modular Integration
# ─────────────────────────────────────────────

from state import state
from auth import validate_token
import history

# Initialize databases
state.init_db()
history.init_history_db()


# ─────────────────────────────────────────────
# 2. Global API Instantiation & Lifespan
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrapping hook logs startup variables for transparency securely."""
    # Launch the key janitor to purge expired keys automatically
    asyncio.create_task(cleanup_expired_keys_task())
    yield
    print("[shutdown] Ceasing operations successfully...")




app = FastAPI(
    title="CodeInspector API Manager",
    description="A centralized proxy relaying connections mapping standard interaction seamlessly to the underlying actual code-evaluation clusters locally natively successfully.",
    version="2.1.0",
    docs_url=None, # Overriding with custom route below
    lifespan=lifespan,
)

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")

async def log_headers(request: Request, call_next):
    print(f"[DEBUG HEADERS] {request.method} {request.url.path} Headers: {dict(request.headers)}")
    response = await call_next(request)
    return response

# Include Modular Routers
app.include_router(history.router)
@app.middleware("http")
async def cookie_auth_redirect_middleware(request: Request, call_next):
    if request.url.path in ["/docs", "/redoc"] or (request.url.path.startswith("/api/") and request.url.path.endswith(("/docs", "/redoc"))):
        # Allow documentation to be public to avoid cookie issues during development
        return await call_next(request)

    return await call_next(request)


# Modularized Authentication & History System


async def update_last_used(jti: str):
    """Updates the last_used_at timestamp in the central database."""
    try:
        conn = state.get_db_conn()
        cursor = conn.cursor()
        now = datetime.datetime.now(datetime.UTC).isoformat()
        query = "UPDATE api_keys SET last_used_at = %s WHERE id = %s" if state.use_postgres else "UPDATE api_keys SET last_used_at = ? WHERE id = ?"
        cursor.execute(query, (now, jti))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[background] Error updating last_used_at: {str(e)}")


async def cleanup_expired_keys_task():
    """
    Background worker that purges expired keys from Postgres and Redis every hour.
    This ensures the 'Back Door' is closed even if no one is currently trying to use the keys.
    """
    while True:
        try:
            now_iso = datetime.datetime.now(datetime.UTC).isoformat()
            print(f"[Janitor] Running cleanup for keys expired before {now_iso}...")
            
            conn = state.get_db_conn()
            cursor = conn.cursor()
            
            # 1. Identify expired keys for Redis cleanup
            query_find = "SELECT id FROM api_keys WHERE expires_at < %s" if state.use_postgres else "SELECT id FROM api_keys WHERE expires_at < ?"
            cursor.execute(query_find, (now_iso,))
            expired_ids = [row[0] for row in cursor.fetchall()]
            
            if expired_ids and state.use_redis:
                for eid in expired_ids:
                    state.redis_client.srem("active_api_keys", eid)
                print(f"[Janitor] Removed {len(expired_ids)} expired keys from Redis")

            # 2. Delete from Database
            query_del = "DELETE FROM api_keys WHERE expires_at < %s" if state.use_postgres else "DELETE FROM api_keys WHERE expires_at < ?"
            cursor.execute(query_del, (now_iso,))
            deleted_count = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            if deleted_count > 0:
                print(f"[Janitor] Successfully purged {deleted_count} expired keys from database.")
                
        except Exception as e:
            print(f"[Janitor] Error during cleanup: {str(e)}")
            
        # Run every minute to handle short-lived keys (like 5-min TTL)
        await asyncio.sleep(60)


# ─────────────────────────────────────────────
# 3. Global Base Operations & Custom Docs
# ─────────────────────────────────────────────

def render_swagger_ui(openapi_url: str, title: str):
    """
    Manually renders Swagger UI HTML with a raw JS requestInterceptor 
    to enable automatic cookie forwarding (withCredentials).
    """
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <link type="text/css" rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
    <title>{title}</title>
    </head>
    <body>
    <div id="swagger-ui"></div>
    <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
    <script>
        const ui = SwaggerUIBundle({{
            url: '{openapi_url}',
            dom_id: '#swagger-ui',
            presets: [
                SwaggerUIBundle.presets.apis,
                SwaggerUIBundle.SwaggerUIStandalonePreset
            ],
            layout: "BaseLayout",
            deepLinking: true,
            displayOperationId: true,
            persistAuthorization: true,
            requestInterceptor: (req) => {{
                req.credentials = 'include';
                return req;
            }}
        }});

        // AUTO-AUTHORIZATION: Read the developer key from the session cookie
        const getCookie = (name) => {{
            const value = `; ${{document.cookie}}`;
            const parts = value.split(`; ${{name}}=`);
            if (parts.length === 2) return parts.pop().split(';').shift();
        }};

        // Robust Authorization Injector
        const autoAuthorize = () => {{
            const token = getCookie('execution_token') || getCookie('inspector_auth');
            if (token && ui && ui.authActions) {{
                const formattedToken = token.startsWith('Bearer ') ? token : `Bearer ${{token}}`;
                
                // Clear any old auth and apply the new one
                ui.authActions.authorize({{
                    "BearerAuth": {{
                        name: "BearerAuth",
                        schema: {{
                            type: "apiKey",
                            in: "header",
                            name: "Authorization"
                        }},
                        value: formattedToken
                    }}
                }});
                console.log("[Zero-Touch] Successfully bound Developer Key to Swagger session.");
            }} else {{
                console.warn("[Zero-Touch] Waiting for UI or Cookie... Retrying in 1s");
                setTimeout(autoAuthorize, 1000);
            }}
        }};

        // Initial trigger
        setTimeout(autoAuthorize, 1000);
    </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return render_swagger_ui(app.openapi_url, app.title + " - Docs")

@app.get("/openapi.json", include_in_schema=False)
async def custom_openapi_json():
    """
    Patches the global OpenAPI spec to define the Cookies as the security scheme.
    """
    spec = app.openapi()
    spec.setdefault("components", {})
    spec["components"].setdefault("securitySchemes", {})
    spec["components"]["securitySchemes"]["CookieAuth"] = {
        "type": "apiKey",
        "in": "cookie",
        "name": "inspector_auth",
    }
    spec["security"] = [{"CookieAuth": []}]
    return JSONResponse(content=spec)


# Cache for remote JWKS (Auth0)
jwks_cache = {
    "last_updated": 0,
    "jwks": None
}

async def get_remote_jwks(url: str):
    """
    Fetches and caches the remote JWKS (e.g., from Auth0).
    """
    now = time.time()
    if jwks_cache["jwks"] and (now - jwks_cache["last_updated"] < 3600):
        return jwks_cache["jwks"]
    
    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        r.raise_for_status()
        jwks_data = r.json()
        jwks = jwt.PyJWKSet.from_dict(jwks_data)
        jwks_cache["jwks"] = jwks
        jwks_cache["last_updated"] = now
        return jwks




@app.get("/health", response_model=StatusResponse, summary="Retrieve active connection tracking properties", tags=["System"])
def health():
    """Confirms running state natively mapping logic checks."""
    return StatusResponse(
        backend=state.backend.name,
        healthy=state.backend.health_check(),
    )

@app.get("/v1/health", response_model=StatusResponse, summary="Retrieve active connection tracking properties (V1)", tags=["System"], dependencies=[Depends(validate_token)])
def health_v1():
    """Alias for /health scoped to /v1 for gateway compatibility."""
    return health()


@app.post("/run", response_model=RunResponse, summary="Dispatch synchronous script explicitly", tags=["System"])
def run_code(req: RunRequest):
    """Evaluates payload instructions passing securely to the configured backend."""
    return state.backend.run(req.code, req.language.value, req.timeout)


# ─────────────────────────────────────────────
# 4. OpenSandbox Proxy Forwarding & Docs
# ─────────────────────────────────────────────

@app.get("/api/{backend_id}/docs", include_in_schema=False, dependencies=[Depends(validate_token)])
async def get_backend_docs(backend_id: str):
    """
    Renders actual upstream OpenSandbox Swagger API with custom authentication logic.
    """
    return render_swagger_ui(f"/api/{backend_id}/openapi.json", f"{backend_id.upper()} — Remote API Docs")


@app.get("/api/{backend_id}/openapi.json", include_in_schema=False, dependencies=[Depends(validate_token)])
async def get_backend_openapi_spec(backend_id: str):
    """Translates and patches explicitly upstream OpenAPI spec."""
    base_url = opensandbox_base_url(backend_id)

    for spec_path in ["/openapi.json", "/v1/openapi.json", "/docs/openapi.json"]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{base_url}{spec_path}")
                if r.status_code == 200:
                    spec = r.json()
                    spec["servers"] = [{"url": f"/api/{backend_id}"}]
                    spec.setdefault("components", {})
                    spec["components"].setdefault("securitySchemes", {})
                    spec["components"]["securitySchemes"]["BearerAuth"] = {
                        "type": "apiKey",
                        "name": "Authorization",
                        "in": "header",
                        "description": "Automatically populated via session binding."
                    }
                    spec["security"] = [{"BearerAuth": []}]
                    return JSONResponse(content=spec)
        except Exception:
            continue

    raise HTTPException(status_code=404, detail=f"Target upstream openapi.json not found on {base_url} for backend {backend_id}")


async def _do_proxy(backend_id: str, proxy_path: str, request: Request):
    """Internal proxy routing logic forwarding transparently upstream."""
    base_url = opensandbox_base_url(backend_id)
    # If the proxy_path doesn't already start with the required prefix for the backend, 
    # we might need to prepend it, but let's assume for now the client sends 
    # the correct full path that the backend expects.
    # We'll normalize the proxy_path to ensure it starts with / for joining
    normalized_path = proxy_path if proxy_path.startswith("/") else f"/{proxy_path}"
    target_url = f"{base_url.rstrip('/')}{normalized_path}"
    
    params = dict(request.query_params)
    body = await request.body()
    
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in ["host", "content-length"]
    }
    
    # Auto-inject OpenSandbox authorization safely
    headers.update(opensandbox_headers())

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.request(
                method=request.method,
                url=target_url,
                params=params,
                content=body,
                headers=headers,
                timeout=300.0,
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers={
                    k: v for k, v in resp.headers.items()
                    if k.lower() not in ["content-encoding", "transfer-encoding"]
                },
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Proxy routing failed targeting {target_url}: {type(exc).__name__} - {exc}",
            )


@app.api_route("/api/{version}/{backend_id}/{proxy_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], tags=["Proxy Backend"], summary="Dynamic Versioned Proxy Request", dependencies=[Depends(validate_token)])
async def dynamic_versioned_proxy(version: str, backend_id: str, proxy_path: str, request: Request):
    """
    Catch-all for URLs like /api/v1/01sbx/scan-jobs
    Funnels directly to the backend while preserving the full path.
    """
    full_proxy_path = f"/api/{version}/{backend_id}/{proxy_path}"
    # Use the backend_id to find the internal URL, but fallback to opensandbox
    return await _do_proxy(backend_id, full_proxy_path, request)


@app.api_route("/api/{backend_id}/{proxy_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"], tags=["Proxy Backend"], summary="Legacy Dynamic Proxy Request", dependencies=[Depends(validate_token)])
async def dynamic_proxy(backend_id: str, proxy_path: str, request: Request):
    """
    Legacy support for /api/z1sandbox/docs style URLs
    """
    full_proxy_path = f"/api/{backend_id}/{proxy_path}"
    return await _do_proxy(backend_id, full_proxy_path, request)

# ─────────────────────────────────────────────
# 5. Native Sandbox Management (V1)
# ─────────────────────────────────────────────

@app.post("/v1/run", tags=["Legacy Code Interpreter"])
async def create_sandbox_session(req: CreateSandboxRequest, payload: dict = Depends(validate_token)):
    """Legacy entry point for sandbox execution."""
    return await state.backend.create_sandbox(req)


@app.get("/v1/sandboxes", response_model=list[SandboxResponse], tags=["Sandboxes"], summary="List all active sandboxes", dependencies=[Depends(validate_token)])
def list_sandboxes():
    """Retrieves a list of all currently active sandboxes from the backend."""
    return state.backend.list_sandboxes()


@app.post("/v1/scan-jobs", response_model=ScanJobResponse, tags=["Security Scan Pipeline"])
async def create_scan_job(req: ScanJobRequest, payload: dict = Depends(validate_token)):
    """
    Submits files for unified security scanning.
    Every submission is isolated by a unique UUID in the PVC.
    This endpoint blocks and waits for the entire scan process to complete.
    """
    job_id = str(uuid.uuid4())
    state.latest_job_id = job_id
    
    if req.metadata is None:
        req.metadata = {}
    req.metadata["job_id"] = job_id

    data = await state.backend.create_scan_job(req.dict(exclude_none=True))
    
    # Persistent History: Record the scan metadata in Postgres
    user_id = payload.get("sub")
    history.record_scan(job_id, user_id, "COMPLETED", data.get("report"))
    
    return ScanJobResponse(**data)


@app.get("/v1/scan-jobs/{job_id}/report", tags=["Security Scan Pipeline"], dependencies=[Depends(validate_token)])
async def get_scan_report(job_id: str):
    """
    Retrieves the persistent JSON scan report for a specific job ID.
    Visible even after the sandbox pod has finished.
    """
    return state.backend.get_scan_report(job_id)


@app.get("/v1/scan-status/{job_id}", tags=["Security Scan Pipeline"], dependencies=[Depends(validate_token)])
async def get_scan_status(job_id: str):
    """
    Retrieves the active state of the sandbox handling the given scan job.
    Useful for polling while a long scan is queued or running asynchronously.
    """
    return state.backend.get_scan_status(job_id)


@app.get("/v1/job-id", tags=["Security Scan Pipeline"], dependencies=[Depends(validate_token)])
async def get_latest_job_id():
    """
    Retrieves the job_id of the most recently initiated scan job in the current session.
    Useful when a /v1/scan-jobs request is blocking and you need the job_id from another tab.
    """
    if not state.latest_job_id:
        raise HTTPException(status_code=404, detail="No scan jobs have been initiated yet.")
    return {"job_id": state.latest_job_id}


@app.post("/api/z1sandbox/v1/run", tags=["Cloud Sandbox Pipeline"])
async def create_sandbox_session_full(req: CreateSandboxRequest, payload: dict = Depends(validate_token)):
    """Primary entry point for gVisor-isolated sandbox execution."""
    return await state.backend.create_sandbox(req)


# ─────────────────────────────────────────────
# 6. API Key Generation & Gateway Sync
# ─────────────────────────────────────────────

@app.post("/v1/generate-api", response_model=GenerateAPIResponse, tags=["Security"], dependencies=[Depends(validate_token)])
async def generate_api(user_id: str = "default-user"):
    """
    Generates a secure JWT for multi-user authentication.
    The token is signed with a shared secret and verified by the agentgateway.
    """
    conf = jwt_config()
    private_key = conf["private_key"]
    algorithm = conf["algorithm"]
    expires_delta = conf["expiration_minutes"]
    issuer = conf["issuer"]

    try:
        now = datetime.datetime.now(datetime.UTC)
        payload = {
            "sub": user_id,
            "iat": now,
            "exp": now + datetime.timedelta(minutes=expires_delta),
            "iss": issuer,
            "aud": "code-inspector-api"
        }
        
        token = jwt.encode(payload, private_key, algorithm=algorithm, headers={"kid": "code-inspector-key-01"})
        
        return GenerateAPIResponse(
            api_key=token,
            api_key_id=payload.get("jti", "legacy"),
            status=f"JWT generated successfully for {user_id}. Valid for {expires_delta} minutes."
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate JWT: {str(e)}"
        )


# ─────────────────────────────────────────────
# 7. Management UI Support - API Keys (CRUD)
# ─────────────────────────────────────────────

from models import APIKeyCreateRequest, APIKeyRecord, APIKeyListResponse

@app.get("/v1/api-keys", tags=["Key Management"])
async def list_api_keys(payload: dict = Depends(validate_token)):
    """Retrieves all active and revoked keys for the authenticated user from central store."""
    user_id = payload.get("sub")
    conn = state.get_db_conn()
    
    # Handle dict behavior difference between sqlite3 and psycopg2
    now_iso = datetime.datetime.now(datetime.UTC).isoformat()
    if state.use_postgres:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        query = "SELECT * FROM api_keys WHERE user_id = %s AND expires_at > %s"
        cursor.execute(query, (user_id, now_iso))
    else:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = "SELECT * FROM api_keys WHERE user_id = ? AND expires_at > ?"
        cursor.execute(query, (user_id, now_iso))
    rows = cursor.fetchall()
    conn.close()

    keys = []
    for row in rows:
        keys.append(APIKeyRecord(
            id=row["id"],
            name=row["name"],
            backend=row["backend"],
            user_id=row["user_id"],
            user_email=row.get("user_email"),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
            last_used_at=row["last_used_at"],
            is_revoked=bool(row["is_revoked"]),
            prefix=row["prefix"]
        ))
    return APIKeyListResponse(keys=keys)


@app.post("/v1/api-keys", tags=["Key Management"])
async def create_api_key(req: APIKeyCreateRequest, payload: dict = Depends(validate_token)):
    """
    Generates a new signed API key (JWT) and persists metadata for revocation/management.
    One-time reveal implementation.
    """
    user_id = payload.get("sub")
    
    # 1. Strip all HTML/Script tags using bleach
    clean_name = bleach.clean(req.name, tags=[], strip=True).strip()

    # 2. Strict Whitelist Sanitization: Allow only alphanumeric, spaces, dashes, and underscores
    sanitized_name = re.sub(r'[^a-zA-Z0-9\s\-_]', '', clean_name).strip()
    
    if not sanitized_name:
        sanitized_name = "Untitled Key"
    
    # Check quota (Max 5 keys per user)
    conn = state.get_db_conn()
    cursor = conn.cursor()
    query_count = "SELECT COUNT(*) FROM api_keys WHERE user_id = %s" if state.use_postgres else "SELECT COUNT(*) FROM api_keys WHERE user_id = ?"
    cursor.execute(query_count, (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    
    if count >= 5:
        raise HTTPException(status_code=403, detail="API Key limit reached (Max 5). Please delete an existing key to create a new one.")

    conf = jwt_config()
    jti = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.UTC)
    if req.ttl_hours == -1:
        expires_at = now + datetime.timedelta(days=365 * 100) # Effectively never expires
        status_msg = f"Key '{sanitized_name}' generated successfully. Valid indefinitely."
    elif req.ttl_hours < 1:
        expires_at = now + datetime.timedelta(hours=req.ttl_hours)
        minutes = int(req.ttl_hours * 60)
        status_msg = f"Key '{sanitized_name}' generated successfully. Valid for {minutes} minute(s)."
    else:
        expires_at = now + datetime.timedelta(hours=req.ttl_hours)
        status_msg = f"Key '{sanitized_name}' generated successfully. Valid for {req.ttl_hours} hour(s)."

    
    token_payload = {
        "sub": user_id,
        "iat": now,
        "exp": expires_at,
        "iss": conf["issuer"],
        "aud": "code-inspector-api",
        "jti": jti,
        "backend": req.backend.value
    }
    
    try:
        # Use pre-loaded object if available for better reliability with RS256
        signing_key = conf.get("private_key_obj") or conf["private_key"]
        token = jwt.encode(token_payload, signing_key, algorithm=conf["algorithm"], headers={"kid": "code-inspector-key-01"})
    except Exception as e:
        print(f"[Security] CRITICAL: JWT Encoding Failed: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Authentication setup failed: {str(e)}")
    
    # Persist metadata with User identity
    # Priority: Explicit request field -> Token claim -> Namespaced claim
    user_email = req.user_email or payload.get("email") or payload.get("https://code-inspector.com/email")
    
    conn = state.get_db_conn()
    cursor = conn.cursor()
    query = """
        INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """ if state.use_postgres else "INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    
    cursor.execute(query, (jti, sanitized_name, req.backend.value, user_id, user_email, now.isoformat(), expires_at.isoformat(), f"ci_{jti[:8]}"))
    conn.commit()
    conn.close()
    
    # Sync to Redis for instant cluster-wide activation
    if state.use_redis:
        state.redis_client.sadd("active_api_keys", jti)

    return GenerateAPIResponse(
        api_key=token,
        api_key_id=jti,
        status=status_msg
    )


@app.delete("/v1/api-keys/{jti}", tags=["Key Management"])
async def revoke_api_key(jti: str, payload: dict = Depends(validate_token)):
    """Deletes/Revokes an API key instantly from global registry and cache."""
    user_id = payload.get("sub")
    conn = state.get_db_conn()
    cursor = conn.cursor()
    
    query = "DELETE FROM api_keys WHERE id = %s AND user_id = %s" if state.use_postgres else "DELETE FROM api_keys WHERE id = ? AND user_id = ?"
    cursor.execute(query, (jti, user_id))
    rows_deleted = cursor.rowcount
    conn.commit()
    conn.close()
    
    if rows_deleted == 0:
        raise HTTPException(status_code=404, detail="Key not found or unauthorized")
    
    # Instant revocation across all pods via shared Redis
    if state.use_redis:
        state.redis_client.srem("active_api_keys", jti)
        print(f"[DEBUG SECURITY] Key {jti} removed from shared Redis allowlist")
    
    return {"status": "success", "message": f"Key {jti} has been permanently destroyed across the cluster."}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codeinspectior_api:app", host="0.0.0.0", port=8000, reload=True)
