"""
codeinspector_api.py
====================

A clean, simplified proxy API specifically designed to forward traffic 
to the internal OpenSandbox backend.

It removes all hardcoded sandbox routing (like /sandboxes, /batched),
instead relying completely transparently on `/backend/z1sandbox/{proxy_path}` 
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

# Modular Imports
from models import (
    RunRequest, RunResponse, StatusResponse, 
    CreateSandboxRequest, SandboxResponse,
    ScanJobRequest, ScanJobResponse,
    GenerateAPIResponse
)
from config import opensandbox_base_url, opensandbox_headers, gateway_secret_config, jwt_config
from backends import SandboxBackend, GenericHTTPBackend

import secrets
import base64
import jwt
from starlette.requests import Request
import datetime
from kubernetes import client, config
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
import redis
import asyncio
import uuid
from typing import List, Dict


# ─────────────────────────────────────────────
# 1. Application State
# ─────────────────────────────────────────────

class AppState:
    """Manages active proxy mapping configurations and centralized high-scale persistence."""
    def __init__(self):
        self.backend: SandboxBackend = GenericHTTPBackend(
            "opensandbox",
            opensandbox_base_url()
        )
        self.latest_job_id: str | None = None
        
        # Persistence Config
        self.use_postgres = os.environ.get("PG_HOST") is not None
        self.use_redis = os.environ.get("REDIS_HOST") is not None
        
        self.db_path = os.environ.get("DB_PATH", "/tmp/apikeys.db")
        self.redis_client = None
        
        if self.use_redis:
            try:
                self.redis_client = redis.Redis(
                    host=os.environ.get("REDIS_HOST"),
                    port=int(os.environ.get("REDIS_PORT", 6379)),
                    password=os.environ.get("REDIS_PASSWORD", ""),
                    decode_responses=True
                )
                print(f"[startup] Connected to Redis at {os.environ.get('REDIS_HOST')}")
            except Exception as e:
                print(f"[startup] FAILED to connect to Redis: {str(e)}")
                self.use_redis = False

    def get_db_conn(self):
        if self.use_postgres:
            return psycopg2.connect(
                host=os.environ.get("PG_HOST"),
                port=os.environ.get("PG_PORT"),
                user=os.environ.get("PG_USER"),
                password=os.environ.get("PG_PASSWORD"),
                dbname=os.environ.get("PG_DATABASE")
            )
        return sqlite3.connect(self.db_path)

    def init_db(self):
        conn = self.get_db_conn()
        cursor = conn.cursor()
        
        # Postgres uses slightly different syntax for PRIMARY KEY and types
        if self.use_postgres:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    backend TEXT,
                    user_id TEXT,
                    user_email TEXT,
                    created_at TEXT,
                    expires_at TEXT,
                    last_used_at TEXT,
                    is_revoked INTEGER DEFAULT 0,
                    prefix TEXT
                )
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    backend TEXT,
                    user_id TEXT,
                    user_email TEXT,
                    created_at TEXT,
                    expires_at TEXT,
                    last_used_at TEXT,
                    is_revoked INTEGER DEFAULT 0,
                    prefix TEXT
                )
            """)
        
        # Schema Guard: Ensure user_email exists (Migration)
        try:
            cursor.execute("ALTER TABLE api_keys ADD COLUMN user_email TEXT" if self.use_postgres else "ALTER TABLE api_keys ADD COLUMN user_email TEXT")
            conn.commit()
            print("[startup] Database migration: Added user_email column to api_keys")
        except Exception:
            conn.rollback()
            pass

        # Sync Active Registry to Redis for line-rate validation
        if self.use_redis:
            cursor.execute("SELECT id FROM api_keys WHERE is_revoked = 0")
            active_jtis = cursor.fetchall()
            if active_jtis:
                # Add all active JTIs to a Redis set called 'active_api_keys'
                pipe = self.redis_client.pipeline()
                pipe.delete("active_api_keys") # Refresh
                for (jti,) in active_jtis:
                    pipe.sadd("active_api_keys", jti)
                pipe.execute()
                print(f"[startup] Synced {len(active_jtis)} active keys to Redis registry.")
                
        conn.commit()
        conn.close()

state = AppState()
state.init_db()


# ─────────────────────────────────────────────
# 2. Global API Instantiation & Lifespan
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrapping hook logs startup variables for transparency securely."""
    print(f"[startup] OpenSandbox base URL default defined as → {opensandbox_base_url()}")
    # Ensures connections start without hardcoded port overrides crashing locally.
    yield
    print("[shutdown] Ceasing operations successfully...")




app = FastAPI(
    title="CodeInspector API Manager",
    description="A centralized proxy relaying connections mapping standard interaction seamlessly to the underlying actual code-evaluation clusters locally natively successfully.",
    version="2.1.0",
    docs_url=None, # Overriding with custom route below
    lifespan=lifespan,
)

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
@app.middleware("http")
async def cookie_auth_redirect_middleware(request: Request, call_next):
    if request.url.path in ["/docs", "/backend/z1sandbox/docs"]:
        cookie_val = request.cookies.get("inspector_auth")
        if not cookie_val:
            return RedirectResponse(url="/", status_code=307)
    return await call_next(request)


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

async def validate_token(request: Request):
    """
    Decodes and validates the RS256 JWT produced by the Edge Gateway's 
    cookie transformation.
    """
    # 1. Path-Aware Enforcement: Decide if we allow Cookie Fallbacks
    path = request.url.path
    # Execution routes MUST use a header. No 'Ghost Authorization' via cookies allowed for execution.
    is_execution_route = path.startswith("/v1/run") or ("/backend/z1sandbox/" in path and "/docs" not in path and "/openapi.json" not in path)
    
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    raw_token = None
    source = "header"

    if auth_header:
        # Accept both "Bearer <token>" and raw "<token>"
        raw_token = auth_header.replace("Bearer ", "", 1) if auth_header.startswith("Bearer ") else auth_header
    elif not is_execution_route:
        # ALLOW Cookie Fallback ONLY for Management/Docs/UI routes
        exec_cookie = request.cookies.get("execution_token")
        auth0_cookie = request.cookies.get("inspector_auth")
        
        if exec_cookie:
            raw_token = exec_cookie
            source = "execution_cookie"
        elif auth0_cookie:
            raw_token = auth0_cookie
            source = "management_cookie"

    if not raw_token:
        error_msg = "Execution required an explicit API Key in the Authorization header. Please use the 'Authorize' padlock." if is_execution_route else "Authentication required (API Key or Session missing)"
        print(f"[DEBUG SECURITY] REJECTION: No credentials found for {path} (Is Execution: {is_execution_route})")
        raise HTTPException(status_code=401, detail=error_msg)
    
    token = raw_token
    conf = jwt_config()
    print(f"[DEBUG SECURITY] Validating {source} token: {token[:10]}...{token[-10:]}")
    
    try:
        # 1. Get unverified info to determine issuer
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
        issuer = unverified_payload.get("iss")
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        
        if not kid:
            raise HTTPException(status_code=401, detail="Missing 'kid' in token header")
        
        # 2. Determine which JWKS to use
        if issuer and issuer.startswith("https://") and conf["auth0_domain"] and conf["auth0_domain"] in issuer:
            print(f"[DEBUG SECURITY] Detected Auth0 token from issuer: {issuer}")
            # Remote Issuer (Auth0)
            target_jwks = await get_remote_jwks(f"{issuer.rstrip('/')}/.well-known/jwks.json")
            target_audience = conf["auth0_audience"]
            target_issuer = issuer
        else:
            print(f"[DEBUG SECURITY] Detected Internal token from issuer: {issuer}")
            # Local Issuer
            jwks_data = json.loads(conf["public_jwks"])
            target_jwks = jwt.PyJWKSet.from_dict(jwks_data)
            target_audience = "code-inspector-api"
            target_issuer = conf["issuer"]
        
        # 3. Get matching key
        signing_key = None
        for key in target_jwks.keys:
            if key.key_id == kid:
                signing_key = key
                break
        
        # 4. Decode and verify signature
        try:
            payload = jwt.decode(
                token, 
                signing_key.key, 
                algorithms=[conf["algorithm"]],
                audience=target_audience,
                issuer=target_issuer
            )
        except Exception as e:
            print(f"[DEBUG SECURITY] JWT Decode ERROR: {str(e)}")
            raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
        
        # 5. --- IDENTITY BRIDGE ---
        # If this is an Auth0 token (browser session), map it to the user's REAL Developer Key in Postgres
        jti = payload.get("jti")
        user_id = payload.get("sub")
        
        # --- IDENTITY BRIDGE ---
        is_management_route = any(request.url.path.startswith(p) for p in ["/v1/api-keys", "/v1/generate-api", "/v1/revoke-api-key"])
        
        if issuer != conf["issuer"]:
            if is_management_route:
                print(f"[Security] Allowing management operation for Auth0 user: {user_id}")
                return payload
            
            print(f"[Identity Bridge] Mapping Auth0 user {user_id} to their primary Developer API Key...")
            conn = state.get_db_conn()
            cursor = conn.cursor()
            query = """
                SELECT id FROM api_keys 
                WHERE user_id = %s AND is_revoked = 0 
                ORDER BY created_at DESC LIMIT 1
            """ if state.use_postgres else "SELECT id FROM api_keys WHERE user_id = ? AND is_revoked = 0 ORDER BY created_at DESC LIMIT 1"
            cursor.execute(query, (user_id,))
            row = cursor.fetchone()
            conn.close()
            
            if row:
                jti = row[0]
                print(f"[Identity Bridge] SUCCESS: Auth0 session now acting as Developer Key ID: {jti}")
            else:
                print(f"[Identity Bridge] WARNING: No Developer Key found for {user_id}")
                raise HTTPException(status_code=403, detail="No active Developer API Key found. Please create your FIRST API Key in this tab to enable sandbox operations.")

        if not jti:
            raise HTTPException(status_code=401, detail="Invalid token: Missing JTI/Key ID")

        # --- DISTRIBUTED VALIDATION (Redis -> Postgres) ---
        is_valid = False
            
        # Step A: High-speed check via Redis (hits all pods instantly)
        if state.use_redis:
            is_valid = state.redis_client.sismember("active_api_keys", jti)
        
        # Step B: Fallback/Integrity check via Central Database
        if not is_valid:
            conn = state.get_db_conn()
            cursor = conn.cursor()
            query = "SELECT is_revoked FROM api_keys WHERE id = %s" if state.use_postgres else "SELECT is_revoked FROM api_keys WHERE id = ?"
            cursor.execute(query, (jti,))
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                raise HTTPException(status_code=401, detail="API Key has been deactivated or deleted")
            
            if row[0] == 1:
                raise HTTPException(status_code=401, detail="API Key has been revoked")
            
            # Self-healing Redis cache
            if state.use_redis:
                state.redis_client.sadd("active_api_keys", jti)
            is_valid = True
        
        print(f"[DEBUG SECURITY] SUCCESS: Session Verified (Key ID: {jti})")
        
        # Update last_used_at in background
        asyncio.create_task(update_last_used(jti))

        # 6. Session Identity Lockdown
        # Security Policy: If a browser session exists, the API Key MUST belong to that user.
        auth_header_raw = request.headers.get("authorization") or request.headers.get("Authorization")
        auth0_cookie = request.cookies.get("inspector_auth")
        
        if auth_header_raw and auth0_cookie:
            apikey_sub = payload.get("sub")
            try:
                # We decode the cookie without signature verification just to get the identity (Gateway already verified it)
                cookie_payload = jwt.decode(auth0_cookie, options={"verify_signature": False})
                cookie_sub = cookie_payload.get("sub")
                
                if cookie_sub and apikey_sub and cookie_sub != apikey_sub:
                    print(f"[SECURITY ALERT] IDENTITY MISMATCH: User {cookie_sub} attempted to use API Key belonging to User {apikey_sub}")
                    raise HTTPException(
                        status_code=403, 
                        detail="Identity Lockdown: You cannot use an API key that belongs to another user."
                    )
            except Exception as e:
                if isinstance(e, HTTPException): raise e
                pass

        return payload
        
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        # Provide actionable feedback if the token is opaque or undefined
        hint = "Ensure Auth0 is returning an RS256 JWT, not an opaque token. Check that the API Audience is registered."
        if token == "undefined" or not token:
            hint = "Token was passed as empty or undefined. Please re-login on the dashboard."
        elif "." not in token:
            hint = "Received an opaque token (missing JWT segments). Check Auth0 API Audience configuration."
        
        token_snippet = f"{token[:10]}..." if len(token) > 10 else token
        raise HTTPException(
            status_code=401, 
            detail=f"Invalid token format ({str(e)}). Token snippet: '{token_snippet}'. {hint}"
        )
    except Exception as e:
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=401, detail=f"Authorization failed: {str(e)}")


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

@app.get("/backend/z1sandbox/docs", include_in_schema=False, dependencies=[Depends(validate_token)])
async def get_backend_docs():
    """
    Renders actual upstream OpenSandbox Swagger API with custom authentication logic.
    """
    return render_swagger_ui("/backend/z1sandbox/openapi.json", "Z1 Sandbox — Remote API Docs")


@app.get("/backend/z1sandbox/openapi.json", include_in_schema=False, dependencies=[Depends(validate_token)])
async def get_backend_openapi_spec():
    """Translates and patches explicitly upstream OpenAPI spec."""
    base_url = opensandbox_base_url()

    for spec_path in ["/openapi.json", "/v1/openapi.json", "/docs/openapi.json"]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{base_url}{spec_path}")
                if r.status_code == 200:
                    spec = r.json()
                    spec["servers"] = [{"url": "/backend/z1sandbox"}]
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

    raise HTTPException(status_code=404, detail=f"Target upstream openapi.json not found on {base_url}")


async def _do_proxy(proxy_path: str, request: Request):
    """Internal proxy routing logic forwarding transparently upstream."""
    base_url = opensandbox_base_url()
    target_url = f"{base_url.rstrip('/')}/{proxy_path}"
    
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


@app.get("/backend/z1sandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="Proxy GET request", dependencies=[Depends(validate_token)])
async def proxy_get(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

@app.post("/backend/z1sandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="Proxy POST request", dependencies=[Depends(validate_token)])
async def proxy_post(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

@app.put("/backend/z1sandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="Proxy PUT request", dependencies=[Depends(validate_token)])
async def proxy_put(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

@app.delete("/backend/z1sandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="Proxy DELETE request", dependencies=[Depends(validate_token)])
async def proxy_delete(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

@app.patch("/backend/z1sandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="Proxy PATCH request", dependencies=[Depends(validate_token)])
async def proxy_patch(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

# OpenSandbox Cluster Proxy Routes
@app.get("/backend/opensandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="OpenSandbox Proxy GET", dependencies=[Depends(validate_token)])
async def opensandbox_proxy_get(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

@app.post("/backend/opensandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="OpenSandbox Proxy POST", dependencies=[Depends(validate_token)])
async def opensandbox_proxy_post(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

@app.delete("/backend/opensandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="OpenSandbox Proxy DELETE", dependencies=[Depends(validate_token)])
async def opensandbox_proxy_delete(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)


# ─────────────────────────────────────────────
# 5. Native Sandbox Management (V1)
# ─────────────────────────────────────────────

@app.post("/v1/sandboxes", response_model=SandboxResponse, tags=["Sandboxes"], summary="Provision a new isolated sandbox", dependencies=[Depends(validate_token)])
def create_sandbox(req: CreateSandboxRequest):
    """Creates a new sandbox environment using the active backend."""
    return state.backend.create_sandbox(req)


@app.get("/v1/sandboxes", response_model=list[SandboxResponse], tags=["Sandboxes"], summary="List all active sandboxes", dependencies=[Depends(validate_token)])
def list_sandboxes():
    """Retrieves a list of all currently active sandboxes from the backend."""
    return state.backend.list_sandboxes()


@app.post("/v1/scan-jobs", response_model=ScanJobResponse, tags=["Security Scan Pipeline"], dependencies=[Depends(validate_token)])
async def create_scan_job(req: ScanJobRequest):
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


@app.get("/v1/job-status", tags=["Security Scan Pipeline"], dependencies=[Depends(validate_token)])
async def get_latest_job_status():
    """
    Retrieves the status of the most recently initiated scan job in the current session.
    """
    if not state.latest_job_id:
        raise HTTPException(status_code=404, detail="No scan jobs have been initiated yet.")
    return state.backend.get_scan_status(state.latest_job_id)


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

@app.get("/v1/api-keys", response_model=APIKeyListResponse, tags=["Security"], dependencies=[Depends(validate_token)])
async def list_user_api_keys(payload: dict = Depends(validate_token)):
    """Retrieves all active and revoked keys for the authenticated user from central store."""
    user_id = payload.get("sub")
    conn = state.get_db_conn()
    
    # Handle dict behavior difference between sqlite3 and psycopg2
    if state.use_postgres:
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        query = "SELECT * FROM api_keys WHERE user_id = %s"
    else:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        query = "SELECT * FROM api_keys WHERE user_id = ?"
        
    cursor.execute(query, (user_id,))
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


@app.post("/v1/api-keys", response_model=GenerateAPIResponse, tags=["Security"], dependencies=[Depends(validate_token)])
async def create_api_key(req: APIKeyCreateRequest, payload: dict = Depends(validate_token)):
    """
    Generates a new signed API key (JWT) and persists metadata for revocation/management.
    One-time reveal implementation.
    """
    user_id = payload.get("sub")
    conf = jwt_config()
    jti = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.UTC)
    if req.ttl_hours == -1:
        expires_at = now + datetime.timedelta(days=365 * 100) # Effectively never expires
        status_msg = f"Key '{req.name}' generated successfully. Valid indefinitely."
    else:
        expires_at = now + datetime.timedelta(hours=req.ttl_hours)
        status_msg = f"Key '{req.name}' generated successfully. Valid for {req.ttl_hours} hour(s)."

    
    token_payload = {
        "sub": user_id,
        "iat": now,
        "exp": expires_at,
        "iss": conf["issuer"],
        "aud": "code-inspector-api",
        "jti": jti,
        "backend": req.backend.value
    }
    
    token = jwt.encode(token_payload, conf["private_key"], algorithm=conf["algorithm"], headers={"kid": "code-inspector-key-01"})
    
    # Persist metadata with User identity
    # Priority: Explicit request field -> Token claim -> Namespaced claim
    user_email = req.user_email or payload.get("email") or payload.get("https://code-inspector.com/email")
    
    conn = state.get_db_conn()
    cursor = conn.cursor()
    query = """
        INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """ if state.use_postgres else "INSERT INTO api_keys (id, name, backend, user_id, user_email, created_at, expires_at, prefix) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    
    cursor.execute(query, (jti, req.name, req.backend.value, user_id, user_email, now.isoformat(), expires_at.isoformat(), f"ci_{jti[:8]}"))
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


@app.delete("/v1/api-keys/{jti}", tags=["Security"], dependencies=[Depends(validate_token)])
async def delete_api_key(jti: str, payload: dict = Depends(validate_token)):
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
