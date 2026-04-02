"""
sandbox_fastapi.py
==================
FastAPI server that exposes a stable HTTP API for code execution
while allowing the sandbox backend to be switched at any time
without the client changing a single request.

Install:
    pip install fastapi uvicorn

Run:
    uvicorn sandbox_fastapi:app --reload --port 8000

Docs (auto-generated):
    http://localhost:8000/docs
"""

from __future__ import annotations

import abc
import os
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from enum import Enum
from typing import Optional

from fastapi import FastAPI, HTTPException, status, Request, Response
from fastapi.responses import JSONResponse
from fastapi.openapi.docs import get_swagger_ui_html
from pydantic import BaseModel, Field
import httpx


# ─────────────────────────────────────────────
# 1.  Enums & Pydantic schemas  (stable contract)
# ─────────────────────────────────────────────

class Backend(str, Enum):
    MOCK       = "mock"
    SUBPROCESS = "subprocess"
    DOCKER     = "docker"
    E2B        = "e2b"


class Language(str, Enum):
    PYTHON     = "python"
    JAVASCRIPT = "javascript"
    BASH       = "bash"


# ── Request models ───────────────────────────

class RunRequest(BaseModel):
    code:     str            = Field(..., example="print('hello')")
    language: Language       = Field(Language.PYTHON)
    timeout:  int            = Field(30, ge=1, le=120)


class SwitchRequest(BaseModel):
    backend:  Backend        = Field(..., example="docker")
    validate: bool           = Field(True, description="Health-check before switching")


# ── Response models ──────────────────────────

class RunResponse(BaseModel):
    stdout:      str
    stderr:      str
    exit_code:   int
    duration_ms: float
    sandbox_id:  str
    backend:     str


class SessionResponse(BaseModel):
    session_id: str
    backend:    str
    metadata:   dict = {}


class StatusResponse(BaseModel):
    backend:   str
    healthy:   bool


class MessageResponse(BaseModel):
    message:   str
    backend:   str


# ─────────────────────────────────────────────
# 2.  Abstract backend interface
# ─────────────────────────────────────────────

class SandboxBackend(abc.ABC):

    @abc.abstractmethod
    def run(self, code: str, language: str, timeout: int) -> RunResponse: ...

    @abc.abstractmethod
    def open_session(self) -> SessionResponse: ...

    @abc.abstractmethod
    def close_session(self, session_id: str) -> None: ...

    @abc.abstractmethod
    def health_check(self) -> bool: ...

    @property
    @abc.abstractmethod
    def name(self) -> str: ...


# ─────────────────────────────────────────────
# 3.  Concrete backends
# ─────────────────────────────────────────────

class MockBackend(SandboxBackend):
    @property
    def name(self): return Backend.MOCK

    def run(self, code, language, timeout):
        t0 = time.perf_counter()
        return RunResponse(
            stdout=f"[mock] ran {len(code)} chars of {language}",
            stderr="",
            exit_code=0,
            duration_ms=(time.perf_counter() - t0) * 1000,
            sandbox_id=str(uuid.uuid4()),
            backend=self.name,
        )

    def open_session(self):
        return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

    def close_session(self, session_id): pass
    def health_check(self): return True


# ─────────────────────────────────────────────

class SubprocessBackend(SandboxBackend):
    _CMD = {
        "python":     ["python3", "-c"],
        "javascript": ["node",    "-e"],
        "bash":       ["bash",    "-c"],
    }

    @property
    def name(self): return Backend.SUBPROCESS

    def run(self, code, language, timeout):
        prefix = self._CMD.get(language)
        if not prefix:
            return RunResponse(stdout="", stderr=f"Unsupported language: {language}",
                               exit_code=1, duration_ms=0,
                               sandbox_id="", backend=self.name)
        t0 = time.perf_counter()
        try:
            p = subprocess.run(prefix + [code],
                               capture_output=True, text=True, timeout=timeout)
            return RunResponse(
                stdout=p.stdout, stderr=p.stderr, exit_code=p.returncode,
                duration_ms=(time.perf_counter() - t0) * 1000,
                sandbox_id=str(uuid.uuid4()), backend=self.name,
            )
        except subprocess.TimeoutExpired:
            return RunResponse(stdout="", stderr="Timed out", exit_code=124,
                               duration_ms=timeout * 1000,
                               sandbox_id=str(uuid.uuid4()), backend=self.name)

    def open_session(self):
        return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

    def close_session(self, session_id): pass

    def health_check(self):
        try:
            subprocess.run(["python3", "--version"], capture_output=True, timeout=3)
            return True
        except Exception:
            return False


# ─────────────────────────────────────────────

class DockerBackend(SandboxBackend):
    _IMAGES = {
        "python":     "python:3.12-slim",
        "javascript": "node:20-slim",
        "bash":       "bash:5",
    }
    _CMDS = {
        "python":     "python3 -c",
        "javascript": "node -e",
        "bash":       "bash -c",
    }

    @property
    def name(self): return Backend.DOCKER

    def run(self, code, language, timeout):
        image = self._IMAGES.get(language)
        if not image:
            return RunResponse(stdout="", stderr=f"Unsupported language: {language}",
                               exit_code=1, duration_ms=0,
                               sandbox_id="", backend=self.name)
        t0 = time.perf_counter()
        cmd = (
            f'docker run --rm --network none --memory 128m --cpus 0.5 '
            f'{image} {self._CMDS[language]} "{code}"'
        )
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True,
                               text=True, timeout=timeout + 5)
            return RunResponse(
                stdout=p.stdout, stderr=p.stderr, exit_code=p.returncode,
                duration_ms=(time.perf_counter() - t0) * 1000,
                sandbox_id=str(uuid.uuid4()), backend=self.name,
            )
        except subprocess.TimeoutExpired:
            return RunResponse(stdout="", stderr="Container timed out", exit_code=124,
                               duration_ms=timeout * 1000,
                               sandbox_id=str(uuid.uuid4()), backend=self.name)

    def open_session(self):
        cid = f"sbx-{uuid.uuid4().hex[:8]}"
        return SessionResponse(session_id=cid, backend=self.name,
                               metadata={"container_id": cid})

    def close_session(self, session_id):
        subprocess.run(["docker", "rm", "-f", session_id], capture_output=True)

    def health_check(self):
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False


# ─────────────────────────────────────────────

class E2BBackend(SandboxBackend):
    @property
    def name(self): return Backend.E2B

    def run(self, code, language, timeout):
        try:
            from e2b_code_interpreter import Sandbox  # type: ignore
        except ImportError:
            return RunResponse(stdout="",
                               stderr="Install: pip install e2b-code-interpreter",
                               exit_code=1, duration_ms=0,
                               sandbox_id="", backend=self.name)
        key = os.environ.get("E2B_API_KEY")
        if not key:
            return RunResponse(stdout="", stderr="E2B_API_KEY not set",
                               exit_code=1, duration_ms=0,
                               sandbox_id="", backend=self.name)
        t0 = time.perf_counter()
        with Sandbox(api_key=key) as sbx:
            r = sbx.run_code(code)
            return RunResponse(
                stdout="\n".join(r.logs.stdout),
                stderr="\n".join(r.logs.stderr),
                exit_code=0 if not r.error else 1,
                duration_ms=(time.perf_counter() - t0) * 1000,
                sandbox_id=sbx.sandbox_id, backend=self.name,
            )

    def open_session(self):
        return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

    def close_session(self, session_id): pass

    def health_check(self):
        return bool(os.environ.get("E2B_API_KEY"))


# ─────────────────────────────────────────────

class GenericHTTPBackend(SandboxBackend):
    def __init__(self, name: str, url: str):
        self._name = name
        self._url = url

    @property
    def name(self): return self._name

    def run(self, code, language, timeout):
        # Generic payload for remote execution
        payload = {
            "code": code,
            "language": language,
            "timeout": timeout
        }
        t0 = time.perf_counter()
        try:
            # Using synchronous httpx client for simplicity within this backend's structure
            with httpx.Client(timeout=timeout + 5) as client:
                r = client.post(self._url, json=payload)
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
            return RunResponse(stdout="", stderr=f"Generic HTTP Backend Error ({self.name}): {str(e)}",
                               exit_code=1, duration_ms=0,
                               sandbox_id="", backend=self.name)

    def open_session(self):
        return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

    def close_session(self, session_id):
        pass

    def health_check(self):
        try:
            # Try to hit the endpoint with a GET (standard health check pattern)
            with httpx.Client(timeout=3) as client:
                r = client.get(self._url)
                return r.status_code < 500
        except Exception:
            return False


# ─────────────────────────────────────────────
# 4.  Registry + factory
# ─────────────────────────────────────────────

_REGISTRY: dict[str, type[SandboxBackend]] = {
    Backend.MOCK:       MockBackend,
    Backend.SUBPROCESS: SubprocessBackend,
    Backend.DOCKER:     DockerBackend,
    Backend.E2B:        E2BBackend,
}


def register_backend(name: str, cls: type[SandboxBackend]) -> None:
    _REGISTRY[name] = cls


def create_backend(name: str) -> SandboxBackend:
    # 1. Check registry first
    cls = _REGISTRY.get(name)
    if cls:
        return cls()
    
    # 2. Check environment variables for dynamic GenericHTTPBackend
    # Lookup BACKEND_URL_OPENSANDBOX, BACKEND_URL_PYTHON, etc.
    env_key = f"BACKEND_URL_{name.upper()}"
    url = os.environ.get(env_key)
    if url:
        return GenericHTTPBackend(name, url)

    raise ValueError(f"Unknown backend '{name}'. Available: {list(_REGISTRY.keys()) + [k.replace('BACKEND_URL_', '').lower() for k in os.environ if k.startswith('BACKEND_URL_')]}")


# ─────────────────────────────────────────────
# 5.  Shared app state
# ─────────────────────────────────────────────

class AppState:
    def __init__(self):
        self.backend: SandboxBackend = MockBackend()
        self._sessions: dict[str, SessionResponse] = {}

    def switch(self, name: str, validate: bool = True) -> None:
        new = create_backend(name)
        if validate and not new.health_check():
            raise RuntimeError(f"Backend '{name}' failed health check.")
        self.backend = new

    def add_session(self, s: SessionResponse) -> None:
        self._sessions[s.session_id] = s

    def get_session(self, sid: str) -> Optional[SessionResponse]:
        return self._sessions.get(sid)

    def remove_session(self, sid: str) -> None:
        self._sessions.pop(sid, None)


state = AppState()


# ─────────────────────────────────────────────
# 6.  FastAPI app
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[startup] active backend → {state.backend.name}")
    yield
    print("[shutdown] cleaning up")


app = FastAPI(
    title="Sandbox API",
    description="Execute code in swappable sandbox backends. "
                "Switch backends without changing client code.",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Core endpoints ───────────────────────────

@app.post(
    "/run",
    response_model=RunResponse,
    summary="Execute code",
    tags=["Execution"],      
)
def run_code(req: RunRequest):
    """
    Execute code in the currently active sandbox backend.
    The client never needs to know which backend is running.
    """
    return state.backend.run(req.code, req.language.value, req.timeout)


@app.get(
    "/health",
    response_model=StatusResponse,
    summary="Backend health check",
    tags=["Management"],
)
def health():
    """Returns whether the active backend is reachable."""
    return StatusResponse(
        backend=state.backend.name,
        healthy=state.backend.health_check(),
    )


@app.post(
    "/backend/switch",
    response_model=MessageResponse,
    summary="Hot-swap the sandbox backend",
    tags=["Management"],
)
def switch_backend(req: SwitchRequest):
    """
    Switch to a different sandbox backend at runtime.
    All subsequent /run calls will use the new backend transparently.
    """
    try:
        state.switch(req.backend, validate=req.validate)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return MessageResponse(
        message=f"Switched to '{req.backend}' backend successfully.",
        backend=state.backend.name,
    )


@app.get(
    "/backend",
    response_model=StatusResponse,
    summary="Current backend info",
    tags=["Management"],
)
def current_backend():
    """Returns the name and health of the currently active backend."""
    return StatusResponse(
        backend=state.backend.name,
        healthy=state.backend.health_check(),
    )


@app.get(
    "/backends",
    summary="List all registered backends",
    tags=["Management"],
)
def list_backends():
    """Returns all available backend names."""
    dynamic = [k.replace('BACKEND_URL_', '').lower() for k in os.environ if k.startswith('BACKEND_URL_')]
    return {"backends": list(_REGISTRY.keys()) + dynamic}


@app.post(
    "/backend/{backend_name}",
    response_model=RunResponse,
    summary="Execute code in a specific backend directly",
    tags=["Execution"],
)
def run_in_backend(backend_name: str, req: RunRequest):
    """
    Directly trigger a specific sandbox backend (e.g. /backend/opensandbox).
    Useful when you want to bypass the globally selected backend.
    """
    try:
        backend = create_backend(backend_name)
        return backend.run(req.code, req.language.value, req.timeout)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.get(
    "/backend/{backend_name}/docs",
    include_in_schema=False
)
def get_backend_docs(backend_name: str):
    """
    Returns a custom Swagger UI that correctly points to the backend's OpenAPI spec.
    """
    # Verify the backend exists
    if f"BACKEND_URL_{backend_name.upper()}" not in os.environ and backend_name not in _REGISTRY:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_name}' not found")
    
    return get_swagger_ui_html(
        openapi_url=f"/backend/{backend_name}/openapi.json",
        title=f"{backend_name.capitalize()} API Documentation",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )


@app.get(
    "/backend/{backend_name}",
    summary="Get info about a specific backend",
    tags=["Management"],
)
def get_backend_info(backend_name: str):
    """
    Returns the registration info and health status of a specific backend.
    Useful for verifying connectivity without executing code.
    """
    try:
        backend = create_backend(backend_name)
        return {
            "backend": backend_name,
            "healthy": backend.health_check(),
            "type": type(backend).__name__,
            "message": "Use POST to this endpoint to execute code.",
            "api_proxy": f"/backend/{backend_name}/..."
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@app.api_route(
    "/backend/{backend_name}/{proxy_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    summary="Transparently proxy requests to the backend",
    tags=["Management"],
)
async def proxy_backend(backend_name: str, proxy_path: str, request: Request):
    """
    Proxies all HTTP methods and paths to the target backend service.
    Example: GET /backend/opensandbox/sandboxes -> GET http://opensandbox-server/sandboxes
    """
    # 1. Lookup the backend URL from environment
    env_key = f"BACKEND_URL_{backend_name.upper()}"
    base_url = os.environ.get(env_key)
    if not base_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Backend '{backend_name}' not configured")
    
    # 2. Construct the target URL
    target_url = f"{base_url.rstrip('/')}/{proxy_path}"
    
    # 3. Forward the request using httpx
    async with httpx.AsyncClient() as client:
        # Get query params, body and filtered headers
        params = dict(request.query_params)
        body = await request.body()
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}
        
        try:
            resp = await client.request(
                method=request.method,
                url=target_url,
                params=params,
                content=body,
                headers=headers,
                timeout=60.0
            )
            
            # 4. Return the response from the backend
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers={k: v for k, v in resp.headers.items() if k.lower() not in ["content-encoding", "transfer-encoding"]}
            )
        except Exception as e:
             raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Proxy error to {target_url}: {str(e)}")


# ── Session endpoints ────────────────────────

@app.post(
    "/session",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a sandbox session",
    tags=["Sessions"],
)
def open_session():
    """Open a persistent session in the active backend."""
    session = state.backend.open_session()
    state.add_session(session)
    return session


@app.delete(
    "/session/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Close a sandbox session",
    tags=["Sessions"],
)
def close_session(session_id: str):
    """Release all resources held by a session."""
    session = state.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    state.backend.close_session(session_id)
    state.remove_session(session_id)


@app.get(
    "/session/{session_id}",
    response_model=SessionResponse,
    summary="Get session info",
    tags=["Sessions"],
)
def get_session(session_id: str):
    session = state.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return session


# ─────────────────────────────────────────────
# 7.  Run directly
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("sandbox_fastapi:app", host="0.0.0.0", port=8000, reload=True)


