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
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
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
import datetime
from kubernetes import client, config


# ─────────────────────────────────────────────
# 1. Application State
# ─────────────────────────────────────────────

class AppState:
    """Manages active proxy mapping configurations statically holding internal maps."""
    def __init__(self):
        self.backend: SandboxBackend = GenericHTTPBackend(
            "opensandbox",
            opensandbox_base_url()
        )
        self.latest_job_id: str | None = None

state = AppState()


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


security = HTTPBearer()

app = FastAPI(
    title="CodeInspector API Manager",
    description="A centralized proxy relaying connections mapping standard interaction seamlessly to the underlying actual code-evaluation clusters locally natively successfully.",
    version="2.1.0",
    docs_url="/docs",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)

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

async def validate_token(request: Request, credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    Decodes and validates the RS256 JWT using either the local public JWKS 
    or a remote OIDC provider (Auth0).
    """
    if not credentials:
        raise HTTPException(status_code=403, detail="Not authenticated")
    
    conf = jwt_config()
    token = credentials.credentials
    
    try:
        # 1. Get unverified info to determine issuer
        unverified_payload = jwt.decode(token, options={"verify_signature": False})
        issuer = unverified_payload.get("iss")
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        
        if not kid:
            raise HTTPException(status_code=401, detail="Missing 'kid' in token header")
        
        conf = jwt_config()
        
        # 2. Determine which JWKS to use
        if issuer and issuer.startswith("https://") and conf["auth0_domain"] and conf["auth0_domain"] in issuer:
            # Remote Issuer (Auth0)
            target_jwks = await get_remote_jwks(f"{issuer.rstrip('/')}/.well-known/jwks.json")
            target_audience = conf["auth0_audience"]
            target_issuer = issuer
        else:
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
        
        if not signing_key:
            raise HTTPException(status_code=401, detail=f"No matching key found in JWKS for kid: {kid}")
            
        # 4. Decode and verify
        print(f"[debug] Validating token for issuer: {target_issuer}, audience: {target_audience}, kid: {kid}")
        payload = jwt.decode(
            token, 
            signing_key.key, 
            algorithms=[conf["algorithm"]],
            audience=target_audience,
            issuer=target_issuer
        )
        print(f"[debug] Token validated successfully for sub: {payload.get('sub')}")
        return payload
        
    except jwt.ExpiredSignatureError:
        print("[debug] Token validation failed: ExpiredSignatureError")
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        print(f"[debug] Token validation failed: InvalidTokenError: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    except Exception as e:
        print(f"[debug] Token validation failed: Unexpected error: {str(e)}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=401, detail=f"Authorization failed: {str(e)}")


# ─────────────────────────────────────────────
# 3. Global Base Operations
# ─────────────────────────────────────────────

@app.get("/health", response_model=StatusResponse, summary="Retrieve active connection tracking properties", tags=["System"])
def health():
    """Confirms running state natively mapping logic checks."""
    return StatusResponse(
        backend=state.backend.name,
        healthy=state.backend.health_check(),
    )


@app.post("/run", response_model=RunResponse, summary="Dispatch synchronous script explicitly", tags=["System"], dependencies=[Depends(validate_token)])
def run_code(req: RunRequest):
    """Evaluates payload instructions passing securely to the configured backend."""
    return state.backend.run(req.code, req.language.value, req.timeout)


# ─────────────────────────────────────────────
# 4. OpenSandbox Proxy Forwarding & Docs
# ─────────────────────────────────────────────

@app.get("/backend/z1sandbox/docs", include_in_schema=False)
async def get_backend_docs():
    """Renders actual upstream OpenSandbox Swagger API."""
    return get_swagger_ui_html(
        openapi_url="/backend/z1sandbox/openapi.json",
        title="OpenSandbox — Remote API Docs",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )


@app.get("/backend/z1sandbox/openapi.json", include_in_schema=False)
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
                        "type": "http",
                        "scheme": "bearer",
                        "bearerFormat": "JWT",
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
    import uuid
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


@app.get("/v1/job-id", tags=["Security Scan Pipeline"])
async def get_latest_job_id():
    """
    Retrieves the job_id of the most recently initiated scan job in the current session.
    Useful when a /v1/scan-jobs request is blocking and you need the job_id from another tab.
    """
    if not state.latest_job_id:
        raise HTTPException(status_code=404, detail="No scan jobs have been initiated yet.")
    return {"job_id": state.latest_job_id}


@app.get("/v1/job-status", tags=["Security Scan Pipeline"])
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

@app.post("/v1/generate-api", response_model=GenerateAPIResponse, tags=["Security"])
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
            status=f"JWT generated successfully for {user_id}. Valid for {expires_delta} minutes."
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate JWT: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codeinspectior_api:app", host="0.0.0.0", port=8000, reload=True)
