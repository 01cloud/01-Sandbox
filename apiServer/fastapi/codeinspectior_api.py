"""
codeinspector_api.py
====================

A clean, simplified proxy API specifically designed to forward traffic 
to the internal OpenSandbox backend.

It removes all hardcoded sandbox routing (like /sandboxes, /batched),
instead relying completely transparently on `/backend/opensandbox/{proxy_path}` 
to communicate with the `opensandbox-server` kubernetes service.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, StreamingResponse

# Modular Imports
from models import (
    RunRequest, RunResponse, StatusResponse, 
    CreateSandboxRequest, SandboxResponse,
    ScanJobRequest, ScanJobResponse
)
from config import opensandbox_base_url, opensandbox_headers
from backends import SandboxBackend, GenericHTTPBackend


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


app = FastAPI(
    title="CodeInspector API Manager",
    description="A centralized proxy relaying connections mapping standard interaction seamlessly to the underlying actual code-evaluation clusters locally natively successfully.",
    version="2.1.0",
    docs_url="/docs",
    lifespan=lifespan,
)


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


@app.post("/run", response_model=RunResponse, summary="Dispatch synchronous script explicitly", tags=["System"])
def run_code(req: RunRequest):
    """Evaluates payload instructions passing securely to the configured backend."""
    return state.backend.run(req.code, req.language.value, req.timeout)


# ─────────────────────────────────────────────
# 4. OpenSandbox Proxy Forwarding & Docs
# ─────────────────────────────────────────────

@app.get("/backend/opensandbox/docs", include_in_schema=False)
async def get_backend_docs():
    """Renders actual upstream OpenSandbox Swagger API."""
    return get_swagger_ui_html(
        openapi_url="/backend/opensandbox/openapi.json",
        title="OpenSandbox — Remote API Docs",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )


@app.get("/backend/opensandbox/openapi.json", include_in_schema=False)
async def get_backend_openapi_spec():
    """Translates and patches explicitly upstream OpenAPI spec."""
    base_url = opensandbox_base_url()

    for spec_path in ["/openapi.json", "/v1/openapi.json", "/docs/openapi.json"]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{base_url}{spec_path}")
                if r.status_code == 200:
                    spec = r.json()
                    spec["servers"] = [{"url": "/backend/opensandbox"}]
                    spec.setdefault("components", {})
                    spec["components"].setdefault("securitySchemes", {})
                    spec["components"]["securitySchemes"]["ApiKeyAuth"] = {
                        "type": "apiKey",
                        "in": "header",
                        "name": "OPEN-SANDBOX-API-KEY",
                    }
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


@app.get("/backend/opensandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="Proxy GET request")
async def proxy_get(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

@app.post("/backend/opensandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="Proxy POST request")
async def proxy_post(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

@app.put("/backend/opensandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="Proxy PUT request")
async def proxy_put(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

@app.delete("/backend/opensandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="Proxy DELETE request")
async def proxy_delete(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)

@app.patch("/backend/opensandbox/{proxy_path:path}", tags=["Proxy Backend"], summary="Proxy PATCH request")
async def proxy_patch(proxy_path: str, request: Request):
    return await _do_proxy(proxy_path, request)


# ─────────────────────────────────────────────
# 5. Native Sandbox Management (V1)
# ─────────────────────────────────────────────

@app.post("/v1/sandboxes", response_model=SandboxResponse, tags=["Sandboxes"], summary="Provision a new isolated sandbox")
def create_sandbox(req: CreateSandboxRequest):
    """Creates a new sandbox environment using the active backend."""
    return state.backend.create_sandbox(req)


@app.get("/v1/sandboxes", response_model=list[SandboxResponse], tags=["Sandboxes"], summary="List all active sandboxes")
def list_sandboxes():
    """Retrieves a list of all currently active sandboxes from the backend."""
    return state.backend.list_sandboxes()


@app.post("/v1/scan-jobs", response_model=ScanJobResponse, tags=["Security Scan Pipeline"])
async def create_scan_job(req: ScanJobRequest):
    """
    Submits files for unified security scanning.
    Every submission is isolated by a unique UUID in the PVC.
    This endpoint blocks and waits for the entire scan process to complete.
    """
    data = await state.backend.create_scan_job(req.dict(exclude_none=True))
    return ScanJobResponse(**data)


@app.get("/v1/scan-jobs/{job_id}/report", tags=["Security Scan Pipeline"])
async def get_scan_report(job_id: str):
    """
    Retrieves the persistent JSON scan report for a specific job ID.
    Visible even after the sandbox pod has finished.
    """
    return state.backend.get_scan_report(job_id)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codeinspectior_api:app", host="0.0.0.0", port=8000, reload=True)
