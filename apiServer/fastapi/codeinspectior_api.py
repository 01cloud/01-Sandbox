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

import abc
import os
import time
import uuid
from contextlib import asynccontextmanager
from enum import Enum
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# 1. Base Schemas & Models
# ─────────────────────────────────────────────

class Language(str, Enum):
    """Supported execution languages."""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    BASH = "bash"


class RunRequest(BaseModel):
    """Payload representing a code execution request structure for the backend."""
    code: str = Field(..., example="print('hello')")
    language: Language = Field(Language.PYTHON)
    timeout: int = Field(30, ge=1, le=120)


class RunResponse(BaseModel):
    """Response defining code execution results dynamically."""
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    sandbox_id: str
    backend: str


class SessionResponse(BaseModel):
    """Identifying metadata correlating linked session context internally."""
    session_id: str
    backend: str
    metadata: dict = {}


class StatusResponse(BaseModel):
    """Reports configuration matching backend instances health correctly."""
    backend: str
    healthy: bool


# ─────────────────────────────────────────────
# 2. OpenSandbox Config Settings (Kubernetes Native)
# ─────────────────────────────────────────────

def opensandbox_base_url() -> str:
    """
    Points directly to the internal Kubernetes service by default.
    Using 'http://opensandbox-server:80' resolves locally inside the cluster.
    """
    return os.environ.get("BACKEND_URL_OPENSANDBOX", "http://opensandbox-server:80")


def opensandbox_headers() -> dict[str, str]:
    """Generates standard HTTP parameter validating authorization."""
    api_key = os.environ.get("OPENSANDBOX_API_KEY", "your-secure-api-key")
    return {
        "Content-Type": "application/json",
        "OPEN-SANDBOX-API-KEY": api_key,
    }


# ─────────────────────────────────────────────
# 3. Abstract Backend Framework
# ─────────────────────────────────────────────

class SandboxBackend(abc.ABC):
    """Abstract interface exposing generic operation mapping routines natively."""
    
    @abc.abstractmethod
    def run(self, code: str, language: str, timeout: int) -> RunResponse:
        pass

    @abc.abstractmethod
    def open_session(self) -> SessionResponse:
        pass

    @abc.abstractmethod
    def close_session(self, session_id: str) -> None:
        pass

    @abc.abstractmethod
    def health_check(self) -> bool:
        pass

    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass


# ─────────────────────────────────────────────
# 4. GenericHTTPBackend specific for OpenSandbox
# ─────────────────────────────────────────────

class GenericHTTPBackend(SandboxBackend):
    """
    Implements standard HTTP interfacing specifically hard-wired explicitly targeting
    REST API backend resources routing dynamically.
    """
    def __init__(self, name: str, url: str):
        self._name = name
        self._url = url

    @property
    def name(self):
        return self._name

    def run(self, code: str, language: str, timeout: int) -> RunResponse:
        """Transmits JSON payload explicitly dictating remote execution instructions."""
        payload = {"code": code, "language": language, "timeout": timeout}
        t0 = time.perf_counter()
        
        try:
            with httpx.Client(timeout=timeout + 5) as client:
                # Typically execution mapping dynamically wraps endpoints. 
                # This explicitly points to the dynamic run/evaluation interface.
                r = client.post(f"{self._url}/run", json=payload, headers=opensandbox_headers())
                r.raise_for_status()
                data = r.json()
                
                return RunResponse(
                    stdout=data.get("stdout", ""),
                    stderr=data.get("stderr", ""),
                    exit_code=data.get("exit_code", 0),
                    duration_ms=(time.perf_counter() - t0) * 1000,
                    sandbox_id=data.get("sandbox_id", str(uuid.uuid4())),
                    backend=self.name,
                )
        except Exception as e:
            return RunResponse(
                stdout="",
                stderr=f"Generic HTTP Backend Error ({self.name}): {e}",
                exit_code=1,
                duration_ms=0,
                sandbox_id="",
                backend=self.name,
            )

    def open_session(self) -> SessionResponse:
        """Simulates dynamic instantiation locally allocating logical tracking contexts."""
        return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

    def close_session(self, session_id: str) -> None:
        pass

    def health_check(self) -> bool:
        """Validates upstream responsiveness mapping explicit endpoint checks dynamically."""
        try:
            with httpx.Client(timeout=3) as client:
                r = client.get(f"{self._url}/health", headers=opensandbox_headers())
                return r.status_code < 500
        except Exception:
            return False


class AppState:
    """Manages active proxy mapping configurations statically holding internal maps."""
    def __init__(self):
        self.backend: SandboxBackend = GenericHTTPBackend(
            "opensandbox",
            opensandbox_base_url()
        )

state = AppState()


# ─────────────────────────────────────────────
# 5. Global API Instantiation
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
    version="2.0.0",
    docs_url="/docs",  # Basic docs path (kept minimal natively)
    lifespan=lifespan,
)


# ─────────────────────────────────────────────
# 6. Global Base Operations natively registered
# ─────────────────────────────────────────────

@app.get("/health", response_model=StatusResponse, summary="Retrieve active connection tracking properties securely safely validating", tags=["System"])
def health():
    """Confirms running state natively mapping logic checks."""
    return StatusResponse(
        backend=state.backend.name,
        healthy=state.backend.health_check(),
    )


@app.post("/run", response_model=RunResponse, summary="Dispatch synchronous script explicitly triggering configured backend safely logically safely", tags=["System"])
def run_code(req: RunRequest):
    """Evaluates payload instructions passing securely logically natively effectively correctly dynamically passing properties."""
    return state.backend.run(req.code, req.language.value, req.timeout)


# ─────────────────────────────────────────────
# 7. OpenSandbox Proxy Forwarding & Docs
# ─────────────────────────────────────────────

@app.get("/backend/opensandbox/docs", include_in_schema=False)
async def get_backend_docs():
    """
    Renders actual upstream OpenSandbox Swagger API actively fetching real properties correctly wrapping references dynamically correctly safely sequentially efficiently.
    """
    return get_swagger_ui_html(
        openapi_url="/backend/opensandbox/openapi.json",
        title="OpenSandbox — Remote API Docs",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )


@app.get("/backend/opensandbox/openapi.json", include_in_schema=False)
async def get_backend_openapi_spec():
    """
    Translates and patches explicitly upstream OpenAPI spec adding authentication tracking dynamically cleanly safely flawlessly transparently.
    """
    base_url = opensandbox_base_url()

    for spec_path in ["/openapi.json", "/v1/openapi.json", "/docs/openapi.json"]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{base_url}{spec_path}")
                if r.status_code == 200:
                    spec = r.json()
                    # Reconfigure server root pointing implicitly locally dynamically natively correctly effectively exclusively globally.
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

    # Notice: If it fails, we return a 404 instead of app.openapi() to ensure it doesn't default to the local docs!
    raise HTTPException(status_code=404, detail=f"Target upstream openapi.json not found securely properly on {base_url}")


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
                timeout=60.0,
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
                detail=f"Proxy routing failed natively targeting upstream explicitly {target_url}: {exc}",
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("codeinspectior_api:app", host="0.0.0.0", port=8000, reload=True)
