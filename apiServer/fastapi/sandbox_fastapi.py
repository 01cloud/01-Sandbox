# # """
# # sandbox_fastapi.py
# # ==================
# # FastAPI server that exposes a stable HTTP API for code execution
# # while allowing the sandbox backend to be switched at any time
# # without the client changing a single request.

# # Install:
# #     pip install fastapi uvicorn

# # Run:
# #     uvicorn sandbox_fastapi:app --reload --port 8000

# # Docs (auto-generated):
# #     http://localhost:8000/docs
# # """

# # from __future__ import annotations

# # import abc
# # import os
# # import subprocess
# # import time
# # import uuid
# # from contextlib import asynccontextmanager
# # from enum import Enum
# # from typing import Optional

# # from fastapi import FastAPI, HTTPException, status, Request, Response
# # from fastapi.responses import JSONResponse
# # from fastapi.openapi.docs import get_swagger_ui_html
# # from pydantic import BaseModel, Field
# # import httpx


# # # ─────────────────────────────────────────────
# # # 1.  Enums & Pydantic schemas  (stable contract)
# # # ─────────────────────────────────────────────

# # class Backend(str, Enum):
# #     MOCK       = "mock"
# #     SUBPROCESS = "subprocess"
# #     DOCKER     = "docker"
# #     E2B        = "e2b"


# # class Language(str, Enum):
# #     PYTHON     = "python"
# #     JAVASCRIPT = "javascript"
# #     BASH       = "bash"


# # # ── Request models ───────────────────────────

# # class RunRequest(BaseModel):
# #     code:     str            = Field(..., example="print('hello')")
# #     language: Language       = Field(Language.PYTHON)
# #     timeout:  int            = Field(30, ge=1, le=120)


# # class SwitchRequest(BaseModel):
# #     backend:  Backend        = Field(..., example="docker")
# #     validate: bool           = Field(True, description="Health-check before switching")


# # # ── Response models ──────────────────────────

# # class RunResponse(BaseModel):
# #     stdout:      str
# #     stderr:      str
# #     exit_code:   int
# #     duration_ms: float
# #     sandbox_id:  str
# #     backend:     str


# # class SessionResponse(BaseModel):
# #     session_id: str
# #     backend:    str
# #     metadata:   dict = {}


# # class StatusResponse(BaseModel):
# #     backend:   str
# #     healthy:   bool


# # class MessageResponse(BaseModel):
# #     message:   str
# #     backend:   str


# # # ─────────────────────────────────────────────
# # # 2.  Abstract backend interface
# # # ─────────────────────────────────────────────

# # class SandboxBackend(abc.ABC):

# #     @abc.abstractmethod
# #     def run(self, code: str, language: str, timeout: int) -> RunResponse: ...

# #     @abc.abstractmethod
# #     def open_session(self) -> SessionResponse: ...

# #     @abc.abstractmethod
# #     def close_session(self, session_id: str) -> None: ...

# #     @abc.abstractmethod
# #     def health_check(self) -> bool: ...

# #     @property
# #     @abc.abstractmethod
# #     def name(self) -> str: ...


# # # ─────────────────────────────────────────────
# # # 3.  Concrete backends
# # # ─────────────────────────────────────────────

# # class MockBackend(SandboxBackend):
# #     @property
# #     def name(self): return Backend.MOCK

# #     def run(self, code, language, timeout):
# #         t0 = time.perf_counter()
# #         return RunResponse(
# #             stdout=f"[mock] ran {len(code)} chars of {language}",
# #             stderr="",
# #             exit_code=0,
# #             duration_ms=(time.perf_counter() - t0) * 1000,
# #             sandbox_id=str(uuid.uuid4()),
# #             backend=self.name,
# #         )

# #     def open_session(self):
# #         return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

# #     def close_session(self, session_id): pass
# #     def health_check(self): return True


# # # ─────────────────────────────────────────────

# # class SubprocessBackend(SandboxBackend):
# #     _CMD = {
# #         "python":     ["python3", "-c"],
# #         "javascript": ["node",    "-e"],
# #         "bash":       ["bash",    "-c"],
# #     }

# #     @property
# #     def name(self): return Backend.SUBPROCESS

# #     def run(self, code, language, timeout):
# #         prefix = self._CMD.get(language)
# #         if not prefix:
# #             return RunResponse(stdout="", stderr=f"Unsupported language: {language}",
# #                                exit_code=1, duration_ms=0,
# #                                sandbox_id="", backend=self.name)
# #         t0 = time.perf_counter()
# #         try:
# #             p = subprocess.run(prefix + [code],
# #                                capture_output=True, text=True, timeout=timeout)
# #             return RunResponse(
# #                 stdout=p.stdout, stderr=p.stderr, exit_code=p.returncode,
# #                 duration_ms=(time.perf_counter() - t0) * 1000,
# #                 sandbox_id=str(uuid.uuid4()), backend=self.name,
# #             )
# #         except subprocess.TimeoutExpired:
# #             return RunResponse(stdout="", stderr="Timed out", exit_code=124,
# #                                duration_ms=timeout * 1000,
# #                                sandbox_id=str(uuid.uuid4()), backend=self.name)

# #     def open_session(self):
# #         return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

# #     def close_session(self, session_id): pass

# #     def health_check(self):
# #         try:
# #             subprocess.run(["python3", "--version"], capture_output=True, timeout=3)
# #             return True
# #         except Exception:
# #             return False


# # # ─────────────────────────────────────────────

# # class DockerBackend(SandboxBackend):
# #     _IMAGES = {
# #         "python":     "python:3.12-slim",
# #         "javascript": "node:20-slim",
# #         "bash":       "bash:5",
# #     }
# #     _CMDS = {
# #         "python":     "python3 -c",
# #         "javascript": "node -e",
# #         "bash":       "bash -c",
# #     }

# #     @property
# #     def name(self): return Backend.DOCKER

# #     def run(self, code, language, timeout):
# #         image = self._IMAGES.get(language)
# #         if not image:
# #             return RunResponse(stdout="", stderr=f"Unsupported language: {language}",
# #                                exit_code=1, duration_ms=0,
# #                                sandbox_id="", backend=self.name)
# #         t0 = time.perf_counter()
# #         cmd = (
# #             f'docker run --rm --network none --memory 128m --cpus 0.5 '
# #             f'{image} {self._CMDS[language]} "{code}"'
# #         )
# #         try:
# #             p = subprocess.run(cmd, shell=True, capture_output=True,
# #                                text=True, timeout=timeout + 5)
# #             return RunResponse(
# #                 stdout=p.stdout, stderr=p.stderr, exit_code=p.returncode,
# #                 duration_ms=(time.perf_counter() - t0) * 1000,
# #                 sandbox_id=str(uuid.uuid4()), backend=self.name,
# #             )
# #         except subprocess.TimeoutExpired:
# #             return RunResponse(stdout="", stderr="Container timed out", exit_code=124,
# #                                duration_ms=timeout * 1000,
# #                                sandbox_id=str(uuid.uuid4()), backend=self.name)

# #     def open_session(self):
# #         cid = f"sbx-{uuid.uuid4().hex[:8]}"
# #         return SessionResponse(session_id=cid, backend=self.name,
# #                                metadata={"container_id": cid})

# #     def close_session(self, session_id):
# #         subprocess.run(["docker", "rm", "-f", session_id], capture_output=True)

# #     def health_check(self):
# #         try:
# #             r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
# #             return r.returncode == 0
# #         except Exception:
# #             return False


# # # ─────────────────────────────────────────────

# # class E2BBackend(SandboxBackend):
# #     @property
# #     def name(self): return Backend.E2B

# #     def run(self, code, language, timeout):
# #         try:
# #             from e2b_code_interpreter import Sandbox  # type: ignore
# #         except ImportError:
# #             return RunResponse(stdout="",
# #                                stderr="Install: pip install e2b-code-interpreter",
# #                                exit_code=1, duration_ms=0,
# #                                sandbox_id="", backend=self.name)
# #         key = os.environ.get("E2B_API_KEY")
# #         if not key:
# #             return RunResponse(stdout="", stderr="E2B_API_KEY not set",
# #                                exit_code=1, duration_ms=0,
# #                                sandbox_id="", backend=self.name)
# #         t0 = time.perf_counter()
# #         with Sandbox(api_key=key) as sbx:
# #             r = sbx.run_code(code)
# #             return RunResponse(
# #                 stdout="\n".join(r.logs.stdout),
# #                 stderr="\n".join(r.logs.stderr),
# #                 exit_code=0 if not r.error else 1,
# #                 duration_ms=(time.perf_counter() - t0) * 1000,
# #                 sandbox_id=sbx.sandbox_id, backend=self.name,
# #             )

# #     def open_session(self):
# #         return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

# #     def close_session(self, session_id): pass

# #     def health_check(self):
# #         return bool(os.environ.get("E2B_API_KEY"))


# # # ─────────────────────────────────────────────

# # class GenericHTTPBackend(SandboxBackend):
# #     def __init__(self, name: str, url: str):
# #         self._name = name
# #         self._url = url

# #     @property
# #     def name(self): return self._name

# #     def run(self, code, language, timeout):
# #         # Generic payload for remote execution
# #         payload = {
# #             "code": code,
# #             "language": language,
# #             "timeout": timeout
# #         }
# #         t0 = time.perf_counter()
# #         try:
# #             # Using synchronous httpx client for simplicity within this backend's structure
# #             with httpx.Client(timeout=timeout + 5) as client:
# #                 r = client.post(self._url, json=payload)
# #                 r.raise_for_status()
# #                 data = r.json()
# #                 return RunResponse(
# #                     stdout=data.get("stdout", ""),
# #                     stderr=data.get("stderr", ""),
# #                     exit_code=data.get("exit_code", 0),
# #                     duration_ms=(time.perf_counter() - t0) * 1000,
# #                     sandbox_id=data.get("sandbox_id", str(uuid.uuid4())),
# #                     backend=self.name,
# #                 )
# #         except Exception as e:
# #             return RunResponse(stdout="", stderr=f"Generic HTTP Backend Error ({self.name}): {str(e)}",
# #                                exit_code=1, duration_ms=0,
# #                                sandbox_id="", backend=self.name)

# #     def open_session(self):
# #         return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

# #     def close_session(self, session_id):
# #         pass

# #     def health_check(self):
# #         try:
# #             # Try to hit the endpoint with a GET (standard health check pattern)
# #             with httpx.Client(timeout=3) as client:
# #                 r = client.get(self._url)
# #                 return r.status_code < 500
# #         except Exception:
# #             return False


# # # ─────────────────────────────────────────────
# # # 4.  Registry + factory
# # # ─────────────────────────────────────────────

# # _REGISTRY: dict[str, type[SandboxBackend]] = {
# #     Backend.MOCK:       MockBackend,
# #     Backend.SUBPROCESS: SubprocessBackend,
# #     Backend.DOCKER:     DockerBackend,
# #     Backend.E2B:        E2BBackend,
# # }


# # def register_backend(name: str, cls: type[SandboxBackend]) -> None:
# #     _REGISTRY[name] = cls


# # def create_backend(name: str) -> SandboxBackend:
# #     # 1. Check registry first
# #     cls = _REGISTRY.get(name)
# #     if cls:
# #         return cls()
    
# #     # 2. Check environment variables for dynamic GenericHTTPBackend
# #     # Lookup BACKEND_URL_OPENSANDBOX, BACKEND_URL_PYTHON, etc.
# #     env_key = f"BACKEND_URL_{name.upper()}"
# #     url = os.environ.get(env_key)
# #     if url:
# #         return GenericHTTPBackend(name, url)

# #     raise ValueError(f"Unknown backend '{name}'. Available: {list(_REGISTRY.keys()) + [k.replace('BACKEND_URL_', '').lower() for k in os.environ if k.startswith('BACKEND_URL_')]}")


# # # ─────────────────────────────────────────────
# # # 5.  Shared app state
# # # ─────────────────────────────────────────────

# # class AppState:
# #     def __init__(self):
# #         self.backend: SandboxBackend = MockBackend()
# #         self._sessions: dict[str, SessionResponse] = {}

# #     def switch(self, name: str, validate: bool = True) -> None:
# #         new = create_backend(name)
# #         if validate and not new.health_check():
# #             raise RuntimeError(f"Backend '{name}' failed health check.")
# #         self.backend = new

# #     def add_session(self, s: SessionResponse) -> None:
# #         self._sessions[s.session_id] = s

# #     def get_session(self, sid: str) -> Optional[SessionResponse]:
# #         return self._sessions.get(sid)

# #     def remove_session(self, sid: str) -> None:
# #         self._sessions.pop(sid, None)


# # state = AppState()


# # # ─────────────────────────────────────────────
# # # 6.  FastAPI app
# # # ─────────────────────────────────────────────

# # @asynccontextmanager
# # async def lifespan(app: FastAPI):
# #     print(f"[startup] active backend → {state.backend.name}")
# #     yield
# #     print("[shutdown] cleaning up")


# # app = FastAPI(
# #     title="Sandbox API",
# #     description="Execute code in swappable sandbox backends. "
# #                 "Switch backends without changing client code.",
# #     version="1.0.0",
# #     lifespan=lifespan,
# # )


# # # ── Core endpoints ───────────────────────────

# # @app.post(
# #     "/run",
# #     response_model=RunResponse,
# #     summary="Execute code",
# #     tags=["Execution"],      
# # )
# # def run_code(req: RunRequest):
# #     """
# #     Execute code in the currently active sandbox backend.
# #     The client never needs to know which backend is running.
# #     """
# #     return state.backend.run(req.code, req.language.value, req.timeout)


# # @app.get(
# #     "/health",
# #     response_model=StatusResponse,
# #     summary="Backend health check",
# #     tags=["Management"],
# # )
# # def health():
# #     """Returns whether the active backend is reachable."""
# #     return StatusResponse(
# #         backend=state.backend.name,
# #         healthy=state.backend.health_check(),
# #     )


# # @app.post(
# #     "/backend/switch",
# #     response_model=MessageResponse,
# #     summary="Hot-swap the sandbox backend",
# #     tags=["Management"],
# # )
# # def switch_backend(req: SwitchRequest):
# #     """
# #     Switch to a different sandbox backend at runtime.
# #     All subsequent /run calls will use the new backend transparently.
# #     """
# #     try:
# #         state.switch(req.backend, validate=req.validate)
# #     except (ValueError, RuntimeError) as e:
# #         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
# #     return MessageResponse(
# #         message=f"Switched to '{req.backend}' backend successfully.",
# #         backend=state.backend.name,
# #     )


# # @app.get(
# #     "/backend",
# #     response_model=StatusResponse,
# #     summary="Current backend info",
# #     tags=["Management"],
# # )
# # def current_backend():
# #     """Returns the name and health of the currently active backend."""
# #     return StatusResponse(
# #         backend=state.backend.name,
# #         healthy=state.backend.health_check(),
# #     )


# # @app.get(
# #     "/backends",
# #     summary="List all registered backends",
# #     tags=["Management"],
# # )
# # def list_backends():
# #     """Returns all available backend names."""
# #     dynamic = [k.replace('BACKEND_URL_', '').lower() for k in os.environ if k.startswith('BACKEND_URL_')]
# #     return {"backends": list(_REGISTRY.keys()) + dynamic}


# # @app.post(
# #     "/backend/{backend_name}",
# #     response_model=RunResponse,
# #     summary="Execute code in a specific backend directly",
# #     tags=["Execution"],
# # )
# # def run_in_backend(backend_name: str, req: RunRequest):
# #     """
# #     Directly trigger a specific sandbox backend (e.g. /backend/opensandbox).
# #     Useful when you want to bypass the globally selected backend.
# #     """
# #     try:
# #         backend = create_backend(backend_name)
# #         return backend.run(req.code, req.language.value, req.timeout)
# #     except ValueError as e:
# #         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# # @app.get(
# #     "/backend/{backend_name}/docs",
# #     include_in_schema=False
# # )
# # def get_backend_docs(backend_name: str):
# #     """
# #     Returns a custom Swagger UI that correctly points to the backend's OpenAPI spec.
# #     """
# #     # Verify the backend exists
# #     if f"BACKEND_URL_{backend_name.upper()}" not in os.environ and backend_name not in _REGISTRY:
# #         raise HTTPException(status_code=404, detail=f"Backend '{backend_name}' not found")
    
# #     return get_swagger_ui_html(
# #         openapi_url=f"/backend/{backend_name}/openapi.json",
# #         title=f"{backend_name.capitalize()} API Documentation",
# #         swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
# #         swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
# #     )


# # @app.get(
# #     "/backend/{backend_name}",
# #     summary="Get info about a specific backend",
# #     tags=["Management"],
# # )
# # def get_backend_info(backend_name: str):
# #     """
# #     Returns the registration info and health status of a specific backend.
# #     Useful for verifying connectivity without executing code.
# #     """
# #     try:
# #         backend = create_backend(backend_name)
# #         return {
# #             "backend": backend_name,
# #             "healthy": backend.health_check(),
# #             "type": type(backend).__name__,
# #             "message": "Use POST to this endpoint to execute code.",
# #             "api_proxy": f"/backend/{backend_name}/..."
# #         }
# #     except ValueError as e:
# #         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# # @app.api_route(
# #     "/backend/{backend_name}/{proxy_path:path}",
# #     methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
# #     summary="Transparently proxy requests to the backend",
# #     tags=["Management"],
# # )
# # async def proxy_backend(backend_name: str, proxy_path: str, request: Request):
# #     """
# #     Proxies all HTTP methods and paths to the target backend service.
# #     Example: GET /backend/opensandbox/sandboxes -> GET http://opensandbox-server/sandboxes
# #     """
# #     # 1. Lookup the backend URL from environment
# #     env_key = f"BACKEND_URL_{backend_name.upper()}"
# #     base_url = os.environ.get(env_key)
# #     if not base_url:
# #         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Backend '{backend_name}' not configured")
    
# #     # 2. Construct the target URL
# #     target_url = f"{base_url.rstrip('/')}/{proxy_path}"
    
# #     # 3. Forward the request using httpx
# #     async with httpx.AsyncClient() as client:
# #         # Get query params, body and filtered headers
# #         params = dict(request.query_params)
# #         body = await request.body()
# #         headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}
        
# #         try:
# #             resp = await client.request(
# #                 method=request.method,
# #                 url=target_url,
# #                 params=params,
# #                 content=body,
# #                 headers=headers,
# #                 timeout=60.0
# #             )
            
# #             # 4. Return the response from the backend
# #             return Response(
# #                 content=resp.content,
# #                 status_code=resp.status_code,
# #                 headers={k: v for k, v in resp.headers.items() if k.lower() not in ["content-encoding", "transfer-encoding"]}
# #             )
# #         except Exception as e:
# #              raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Proxy error to {target_url}: {str(e)}")


# # # ── Session endpoints ────────────────────────

# # @app.post(
# #     "/session",
# #     response_model=SessionResponse,
# #     status_code=status.HTTP_201_CREATED,
# #     summary="Open a sandbox session",
# #     tags=["Sessions"],
# # )
# # def open_session():
# #     """Open a persistent session in the active backend."""
# #     session = state.backend.open_session()
# #     state.add_session(session)
# #     return session


# # @app.delete(
# #     "/session/{session_id}",
# #     status_code=status.HTTP_204_NO_CONTENT,
# #     summary="Close a sandbox session",
# #     tags=["Sessions"],
# # )
# # def close_session(session_id: str):
# #     """Release all resources held by a session."""
# #     session = state.get_session(session_id)
# #     if not session:
# #         raise HTTPException(
# #             status_code=status.HTTP_404_NOT_FOUND,
# #             detail=f"Session '{session_id}' not found.",
# #         )
# #     state.backend.close_session(session_id)
# #     state.remove_session(session_id)


# # @app.get(
# #     "/session/{session_id}",
# #     response_model=SessionResponse,
# #     summary="Get session info",
# #     tags=["Sessions"],
# # )
# # def get_session(session_id: str):
# #     session = state.get_session(session_id)
# #     if not session:
# #         raise HTTPException(
# #             status_code=status.HTTP_404_NOT_FOUND,
# #             detail=f"Session '{session_id}' not found.",
# #         )
# #     return session


# # # ─────────────────────────────────────────────
# # # 7.  Run directly
# # # ─────────────────────────────────────────────

# # if __name__ == "__main__":
# #     import uvicorn
# #     uvicorn.run("sandbox_fastapi:app", host="0.0.0.0", port=8000, reload=True)


# """
# sandbox_fastapi.py
# ==================
# FastAPI server that exposes a stable HTTP API for code execution
# while allowing the sandbox backend to be switched at any time
# without the client changing a single request.

# Install:
#     pip install fastapi uvicorn httpx

# Run:
#     uvicorn sandbox_fastapi:app --reload --port 8000

# Environment Variables:
#     BACKEND_URL_OPENSANDBOX=http://localhost:8080   (URL of your OpenSandbox server)
#     OPENSANDBOX_API_KEY=your-secure-api-key         (API key for OpenSandbox)

# Docs (auto-generated):
#     http://localhost:8000/docs
#     http://localhost:8000/backend/opensandbox/docs   (proxied OpenSandbox docs)
# """

# from __future__ import annotations

# import abc
# import asyncio
# import os
# import subprocess
# import time
# import uuid
# from contextlib import asynccontextmanager
# from enum import Enum
# from typing import Any, Optional

# from fastapi import FastAPI, HTTPException, status, Request, Response
# from fastapi.responses import JSONResponse
# from fastapi.openapi.docs import get_swagger_ui_html
# from pydantic import BaseModel, Field
# import httpx


# # ─────────────────────────────────────────────
# # 1.  Enums & Pydantic schemas  (stable contract)
# # ─────────────────────────────────────────────

# class Backend(str, Enum):
#     MOCK       = "mock"
#     SUBPROCESS = "subprocess"
#     DOCKER     = "docker"
#     E2B        = "e2b"


# class Language(str, Enum):
#     PYTHON     = "python"
#     JAVASCRIPT = "javascript"
#     BASH       = "bash"


# # ── Request models ───────────────────────────

# class RunRequest(BaseModel):
#     code:     str            = Field(..., example="print('hello')")
#     language: Language       = Field(Language.PYTHON)
#     timeout:  int            = Field(30, ge=1, le=120)


# class SwitchRequest(BaseModel):
#     backend:  Backend        = Field(..., example="docker")
#     validate: bool           = Field(True, description="Health-check before switching")


# # ── OpenSandbox-specific request models ──────

# class SandboxImageModel(BaseModel):
#     uri: str = Field("codeintepreter:1.0.0", description="Docker image URI for the sandbox")


# class ResourceLimitsModel(BaseModel):
#     cpu:    str = Field("1",    description="CPU limit (e.g. '1', '0.5')")
#     memory: str = Field("2Gi", description="Memory limit (e.g. '2Gi', '512Mi')")


# class SandboxCreateRequest(BaseModel):
#     image:          SandboxImageModel  = Field(default_factory=SandboxImageModel)
#     entrypoint:     list[str]          = Field(
#         default=["/opt/opensandbox/code-interpreter.sh"],
#         description="Container entrypoint command"
#     )
#     timeout:        int                = Field(600, ge=1, le=3600, description="Sandbox timeout in seconds")
#     env:            dict[str, str]     = Field(
#         default={"PYTHON_VERSION": "3.11"},
#         description="Environment variables"
#     )
#     resourceLimits: ResourceLimitsModel = Field(default_factory=ResourceLimitsModel)
#     metadata:       dict[str, str]     = Field(
#         default={"project": "my-ai-agent", "environment": "production"},
#         description="Arbitrary metadata labels"
#     )


# class BatchSandboxCreateRequest(BaseModel):
#     count:          int                = Field(1, ge=1, le=10, description="Number of sandboxes to create (max 10)")
#     image:          SandboxImageModel  = Field(default_factory=SandboxImageModel)
#     entrypoint:     list[str]          = Field(default=["/opt/opensandbox/code-interpreter.sh"])
#     timeout:        int                = Field(600, ge=1, le=3600)
#     env:            dict[str, str]     = Field(default={"PYTHON_VERSION": "3.11"})
#     resourceLimits: ResourceLimitsModel = Field(default_factory=ResourceLimitsModel)
#     metadata:       dict[str, str]     = Field(default={"project": "my-ai-agent", "environment": "production"})


# # ── Response models ──────────────────────────

# class RunResponse(BaseModel):
#     stdout:      str
#     stderr:      str
#     exit_code:   int
#     duration_ms: float
#     sandbox_id:  str
#     backend:     str


# class SessionResponse(BaseModel):
#     session_id: str
#     backend:    str
#     metadata:   dict = {}


# class StatusResponse(BaseModel):
#     backend:   str
#     healthy:   bool


# class MessageResponse(BaseModel):
#     message:   str
#     backend:   str


# class BatchSandboxResponse(BaseModel):
#     created:    int
#     failed:     int
#     sandboxes:  list[dict]
#     errors:     list[str]


# # ─────────────────────────────────────────────
# # 2.  Abstract backend interface
# # ─────────────────────────────────────────────

# class SandboxBackend(abc.ABC):

#     @abc.abstractmethod
#     def run(self, code: str, language: str, timeout: int) -> RunResponse: ...

#     @abc.abstractmethod
#     def open_session(self) -> SessionResponse: ...

#     @abc.abstractmethod
#     def close_session(self, session_id: str) -> None: ...

#     @abc.abstractmethod
#     def health_check(self) -> bool: ...

#     @property
#     @abc.abstractmethod
#     def name(self) -> str: ...


# # ─────────────────────────────────────────────
# # 3.  Concrete backends
# # ─────────────────────────────────────────────

# class MockBackend(SandboxBackend):
#     @property
#     def name(self): return Backend.MOCK

#     def run(self, code, language, timeout):
#         t0 = time.perf_counter()
#         return RunResponse(
#             stdout=f"[mock] ran {len(code)} chars of {language}",
#             stderr="",
#             exit_code=0,
#             duration_ms=(time.perf_counter() - t0) * 1000,
#             sandbox_id=str(uuid.uuid4()),
#             backend=self.name,
#         )

#     def open_session(self):
#         return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

#     def close_session(self, session_id): pass
#     def health_check(self): return True


# # ─────────────────────────────────────────────

# class SubprocessBackend(SandboxBackend):
#     _CMD = {
#         "python":     ["python3", "-c"],
#         "javascript": ["node",    "-e"],
#         "bash":       ["bash",    "-c"],
#     }

#     @property
#     def name(self): return Backend.SUBPROCESS

#     def run(self, code, language, timeout):
#         prefix = self._CMD.get(language)
#         if not prefix:
#             return RunResponse(stdout="", stderr=f"Unsupported language: {language}",
#                                exit_code=1, duration_ms=0,
#                                sandbox_id="", backend=self.name)
#         t0 = time.perf_counter()
#         try:
#             p = subprocess.run(prefix + [code],
#                                capture_output=True, text=True, timeout=timeout)
#             return RunResponse(
#                 stdout=p.stdout, stderr=p.stderr, exit_code=p.returncode,
#                 duration_ms=(time.perf_counter() - t0) * 1000,
#                 sandbox_id=str(uuid.uuid4()), backend=self.name,
#             )
#         except subprocess.TimeoutExpired:
#             return RunResponse(stdout="", stderr="Timed out", exit_code=124,
#                                duration_ms=timeout * 1000,
#                                sandbox_id=str(uuid.uuid4()), backend=self.name)

#     def open_session(self):
#         return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

#     def close_session(self, session_id): pass

#     def health_check(self):
#         try:
#             subprocess.run(["python3", "--version"], capture_output=True, timeout=3)
#             return True
#         except Exception:
#             return False


# # ─────────────────────────────────────────────

# class DockerBackend(SandboxBackend):
#     _IMAGES = {
#         "python":     "python:3.12-slim",
#         "javascript": "node:20-slim",
#         "bash":       "bash:5",
#     }
#     _CMDS = {
#         "python":     "python3 -c",
#         "javascript": "node -e",
#         "bash":       "bash -c",
#     }

#     @property
#     def name(self): return Backend.DOCKER

#     def run(self, code, language, timeout):
#         image = self._IMAGES.get(language)
#         if not image:
#             return RunResponse(stdout="", stderr=f"Unsupported language: {language}",
#                                exit_code=1, duration_ms=0,
#                                sandbox_id="", backend=self.name)
#         t0 = time.perf_counter()
#         cmd = (
#             f'docker run --rm --network none --memory 128m --cpus 0.5 '
#             f'{image} {self._CMDS[language]} "{code}"'
#         )
#         try:
#             p = subprocess.run(cmd, shell=True, capture_output=True,
#                                text=True, timeout=timeout + 5)
#             return RunResponse(
#                 stdout=p.stdout, stderr=p.stderr, exit_code=p.returncode,
#                 duration_ms=(time.perf_counter() - t0) * 1000,
#                 sandbox_id=str(uuid.uuid4()), backend=self.name,
#             )
#         except subprocess.TimeoutExpired:
#             return RunResponse(stdout="", stderr="Container timed out", exit_code=124,
#                                duration_ms=timeout * 1000,
#                                sandbox_id=str(uuid.uuid4()), backend=self.name)

#     def open_session(self):
#         cid = f"sbx-{uuid.uuid4().hex[:8]}"
#         return SessionResponse(session_id=cid, backend=self.name,
#                                metadata={"container_id": cid})

#     def close_session(self, session_id):
#         subprocess.run(["docker", "rm", "-f", session_id], capture_output=True)

#     def health_check(self):
#         try:
#             r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
#             return r.returncode == 0
#         except Exception:
#             return False


# # ─────────────────────────────────────────────

# class E2BBackend(SandboxBackend):
#     @property
#     def name(self): return Backend.E2B

#     def run(self, code, language, timeout):
#         try:
#             from e2b_code_interpreter import Sandbox  # type: ignore
#         except ImportError:
#             return RunResponse(stdout="",
#                                stderr="Install: pip install e2b-code-interpreter",
#                                exit_code=1, duration_ms=0,
#                                sandbox_id="", backend=self.name)
#         key = os.environ.get("E2B_API_KEY")
#         if not key:
#             return RunResponse(stdout="", stderr="E2B_API_KEY not set",
#                                exit_code=1, duration_ms=0,
#                                sandbox_id="", backend=self.name)
#         t0 = time.perf_counter()
#         with Sandbox(api_key=key) as sbx:
#             r = sbx.run_code(code)
#             return RunResponse(
#                 stdout="\n".join(r.logs.stdout),
#                 stderr="\n".join(r.logs.stderr),
#                 exit_code=0 if not r.error else 1,
#                 duration_ms=(time.perf_counter() - t0) * 1000,
#                 sandbox_id=sbx.sandbox_id, backend=self.name,
#             )

#     def open_session(self):
#         return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

#     def close_session(self, session_id): pass

#     def health_check(self):
#         return bool(os.environ.get("E2B_API_KEY"))


# # ─────────────────────────────────────────────

# class GenericHTTPBackend(SandboxBackend):
#     def __init__(self, name: str, url: str):
#         self._name = name
#         self._url = url

#     @property
#     def name(self): return self._name

#     def run(self, code, language, timeout):
#         payload = {
#             "code": code,
#             "language": language,
#             "timeout": timeout
#         }
#         t0 = time.perf_counter()
#         try:
#             with httpx.Client(timeout=timeout + 5) as client:
#                 r = client.post(self._url, json=payload)
#                 r.raise_for_status()
#                 data = r.json()
#                 return RunResponse(
#                     stdout=data.get("stdout", ""),
#                     stderr=data.get("stderr", ""),
#                     exit_code=data.get("exit_code", 0),
#                     duration_ms=(time.perf_counter() - t0) * 1000,
#                     sandbox_id=data.get("sandbox_id", str(uuid.uuid4())),
#                     backend=self.name,
#                 )
#         except Exception as e:
#             return RunResponse(stdout="", stderr=f"Generic HTTP Backend Error ({self.name}): {str(e)}",
#                                exit_code=1, duration_ms=0,
#                                sandbox_id="", backend=self.name)

#     def open_session(self):
#         return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

#     def close_session(self, session_id):
#         pass

#     def health_check(self):
#         try:
#             with httpx.Client(timeout=3) as client:
#                 r = client.get(self._url)
#                 return r.status_code < 500
#         except Exception:
#             return False


# # ─────────────────────────────────────────────
# # 4.  Registry + factory
# # ─────────────────────────────────────────────

# _REGISTRY: dict[str, type[SandboxBackend]] = {
#     Backend.MOCK:       MockBackend,
#     Backend.SUBPROCESS: SubprocessBackend,
#     Backend.DOCKER:     DockerBackend,
#     Backend.E2B:        E2BBackend,
# }


# def register_backend(name: str, cls: type[SandboxBackend]) -> None:
#     _REGISTRY[name] = cls


# def create_backend(name: str) -> SandboxBackend:
#     cls = _REGISTRY.get(name)
#     if cls:
#         return cls()

#     env_key = f"BACKEND_URL_{name.upper()}"
#     url = os.environ.get(env_key)
#     if url:
#         return GenericHTTPBackend(name, url)

#     raise ValueError(
#         f"Unknown backend '{name}'. "
#         f"Available: {list(_REGISTRY.keys()) + [k.replace('BACKEND_URL_', '').lower() for k in os.environ if k.startswith('BACKEND_URL_')]}"
#     )


# # ─────────────────────────────────────────────
# # 5.  Shared app state
# # ─────────────────────────────────────────────

# class AppState:
#     def __init__(self):
#         self.backend: SandboxBackend = MockBackend()
#         self._sessions: dict[str, SessionResponse] = {}

#     def switch(self, name: str, validate: bool = True) -> None:
#         new = create_backend(name)
#         if validate and not new.health_check():
#             raise RuntimeError(f"Backend '{name}' failed health check.")
#         self.backend = new

#     def add_session(self, s: SessionResponse) -> None:
#         self._sessions[s.session_id] = s

#     def get_session(self, sid: str) -> Optional[SessionResponse]:
#         return self._sessions.get(sid)

#     def remove_session(self, sid: str) -> None:
#         self._sessions.pop(sid, None)


# state = AppState()


# # ─────────────────────────────────────────────
# # 6.  Helper: build OpenSandbox headers
# # ─────────────────────────────────────────────

# def _opensandbox_headers() -> dict[str, str]:
#     """
#     Returns headers required by OpenSandbox.
#     API key is read from the OPENSANDBOX_API_KEY environment variable.
#     Set it with:  export OPENSANDBOX_API_KEY=your-secure-api-key
#     """
#     api_key = os.environ.get("OPENSANDBOX_API_KEY", "your-secure-api-key")
#     return {
#         "Content-Type":        "application/json",
#         "OPEN-SANDBOX-API-KEY": api_key,
#     }


# def _opensandbox_base_url() -> str:
#     return os.environ.get("BACKEND_URL_OPENSANDBOX", "http://localhost:8080")


# # ─────────────────────────────────────────────
# # 7.  FastAPI app
# # ─────────────────────────────────────────────

# @asynccontextmanager
# async def lifespan(app: FastAPI):
#     print(f"[startup] active backend → {state.backend.name}")
#     print(f"[startup] OpenSandbox base URL → {_opensandbox_base_url()}")
#     yield
#     print("[shutdown] cleaning up")


# app = FastAPI(
#     title="Sandbox API",
#     description=(
#         "Execute code in swappable sandbox backends. "
#         "Switch backends without changing client code.\n\n"
#         "**OpenSandbox Docs**: [/backend/opensandbox/docs](/backend/opensandbox/docs)"
#     ),
#     version="2.0.0",
#     lifespan=lifespan,
# )


# # ─────────────────────────────────────────────
# # 8.  Core execution endpoints
# # ─────────────────────────────────────────────

# @app.post(
#     "/run",
#     response_model=RunResponse,
#     summary="Execute code",
#     tags=["Execution"],
# )
# def run_code(req: RunRequest):
#     """Execute code in the currently active sandbox backend."""
#     return state.backend.run(req.code, req.language.value, req.timeout)


# @app.post(
#     "/backend/{backend_name}",
#     response_model=RunResponse,
#     summary="Execute code in a specific backend directly",
#     tags=["Execution"],
# )
# def run_in_backend(backend_name: str, req: RunRequest):
#     """Directly trigger a specific sandbox backend."""
#     try:
#         backend = create_backend(backend_name)
#         return backend.run(req.code, req.language.value, req.timeout)
#     except ValueError as e:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# # ─────────────────────────────────────────────
# # 9.  OpenSandbox — single sandbox creation
# # ─────────────────────────────────────────────

# @app.post(
#     "/sandboxes",
#     summary="Create a single OpenSandbox sandbox",
#     tags=["OpenSandbox"],
#     status_code=status.HTTP_201_CREATED,
# )
# async def create_sandbox(req: SandboxCreateRequest):
#     """
#     Creates a single sandbox via OpenSandbox.

#     Equivalent curl:
#     ```
#     curl -X POST http://localhost:8080/v1/sandboxes \\
#       -H "OPEN-SANDBOX-API-KEY: your-key" \\
#       -d '{"image": {"uri": "codeintepreter:1.0.0"}, ...}'
#     ```
#     """
#     base_url = _opensandbox_base_url()
#     payload  = req.model_dump()

#     async with httpx.AsyncClient(timeout=30) as client:
#         try:
#             r = await client.post(
#                 f"{base_url}/v1/sandboxes",
#                 json=payload,
#                 headers=_opensandbox_headers(),
#             )
#             r.raise_for_status()
#             return r.json()
#         except httpx.HTTPStatusError as e:
#             raise HTTPException(
#                 status_code=e.response.status_code,
#                 detail=f"OpenSandbox error: {e.response.text}",
#             )
#         except httpx.RequestError as e:
#             raise HTTPException(
#                 status_code=status.HTTP_502_BAD_GATEWAY,
#                 detail=f"Could not reach OpenSandbox at {base_url}: {e}",
#             )


# # ─────────────────────────────────────────────
# # 10. OpenSandbox — batch sandbox creation  ✨ NEW
# # ─────────────────────────────────────────────

# @app.post(
#     "/sandboxes/batch",
#     response_model=BatchSandboxResponse,
#     summary="Create multiple OpenSandbox sandboxes at once",
#     tags=["OpenSandbox"],
#     status_code=status.HTTP_201_CREATED,
# )
# async def create_sandboxes_batch(req: BatchSandboxCreateRequest):
#     """
#     Creates **1–10 sandboxes concurrently** via OpenSandbox.

#     All sandboxes are created with the same configuration.
#     Results include both successful and failed creations.

#     Example — create 3 sandboxes at once:
#     ```json
#     {
#       "count": 3,
#       "image": {"uri": "codeintepreter:1.0.0"},
#       "timeout": 600,
#       "env": {"PYTHON_VERSION": "3.11"},
#       "resourceLimits": {"cpu": "1", "memory": "2Gi"},
#       "metadata": {"project": "my-ai-agent", "environment": "production"}
#     }
#     ```
#     """
#     base_url = _opensandbox_base_url()
#     headers  = _opensandbox_headers()

#     # Build the payload once (same for every sandbox)
#     payload = {
#         "image":          req.image.model_dump(),
#         "entrypoint":     req.entrypoint,
#         "timeout":        req.timeout,
#         "env":            req.env,
#         "resourceLimits": req.resourceLimits.model_dump(),
#         "metadata":       req.metadata,
#     }

#     async def _create_one(index: int) -> tuple[dict | None, str | None]:
#         """Returns (result_dict, error_string)."""
#         async with httpx.AsyncClient(timeout=30) as client:
#             try:
#                 # Tag each sandbox with its batch index so they're distinguishable
#                 tagged_payload = {
#                     **payload,
#                     "metadata": {**payload["metadata"], "batch_index": str(index)},
#                 }
#                 r = await client.post(
#                     f"{base_url}/v1/sandboxes",
#                     json=tagged_payload,
#                     headers=headers,
#                 )
#                 r.raise_for_status()
#                 return r.json(), None
#             except httpx.HTTPStatusError as e:
#                 return None, f"[{index}] HTTP {e.response.status_code}: {e.response.text}"
#             except Exception as e:
#                 return None, f"[{index}] {type(e).__name__}: {e}"

#     # Fire all requests concurrently
#     results = await asyncio.gather(*[_create_one(i) for i in range(req.count)])

#     sandboxes: list[dict] = []
#     errors:    list[str]  = []

#     for result, error in results:
#         if result is not None:
#             sandboxes.append(result)
#         if error is not None:
#             errors.append(error)

#     return BatchSandboxResponse(
#         created=len(sandboxes),
#         failed=len(errors),
#         sandboxes=sandboxes,
#         errors=errors,
#     )


# # ─────────────────────────────────────────────
# # 11. OpenSandbox — list, get, delete, pause, resume
# # ─────────────────────────────────────────────

# @app.get(
#     "/sandboxes",
#     summary="List all OpenSandbox sandboxes",
#     tags=["OpenSandbox"],
# )
# async def list_sandboxes():
#     """Returns all active sandboxes from OpenSandbox."""
#     base_url = _opensandbox_base_url()
#     async with httpx.AsyncClient(timeout=15) as client:
#         try:
#             r = await client.get(
#                 f"{base_url}/v1/sandboxes",
#                 headers=_opensandbox_headers(),
#             )
#             r.raise_for_status()
#             return r.json()
#         except httpx.HTTPStatusError as e:
#             raise HTTPException(status_code=e.response.status_code,
#                                 detail=e.response.text)
#         except httpx.RequestError as e:
#             raise HTTPException(status_code=502, detail=str(e))


# @app.get(
#     "/sandboxes/{sandbox_id}",
#     summary="Get a specific OpenSandbox sandbox",
#     tags=["OpenSandbox"],
# )
# async def get_sandbox(sandbox_id: str):
#     """Fetch details for a single sandbox by its ID."""
#     base_url = _opensandbox_base_url()
#     async with httpx.AsyncClient(timeout=15) as client:
#         try:
#             r = await client.get(
#                 f"{base_url}/v1/sandboxes/{sandbox_id}",
#                 headers=_opensandbox_headers(),
#             )
#             r.raise_for_status()
#             return r.json()
#         except httpx.HTTPStatusError as e:
#             raise HTTPException(status_code=e.response.status_code,
#                                 detail=e.response.text)
#         except httpx.RequestError as e:
#             raise HTTPException(status_code=502, detail=str(e))


# @app.delete(
#     "/sandboxes/{sandbox_id}",
#     summary="Delete an OpenSandbox sandbox",
#     tags=["OpenSandbox"],
#     status_code=status.HTTP_204_NO_CONTENT,
# )
# async def delete_sandbox(sandbox_id: str):
#     """Permanently delete a sandbox and free its resources."""
#     base_url = _opensandbox_base_url()
#     async with httpx.AsyncClient(timeout=15) as client:
#         try:
#             r = await client.delete(
#                 f"{base_url}/v1/sandboxes/{sandbox_id}",
#                 headers=_opensandbox_headers(),
#             )
#             r.raise_for_status()
#         except httpx.HTTPStatusError as e:
#             raise HTTPException(status_code=e.response.status_code,
#                                 detail=e.response.text)
#         except httpx.RequestError as e:
#             raise HTTPException(status_code=502, detail=str(e))


# @app.post(
#     "/sandboxes/{sandbox_id}/pause",
#     summary="Pause an OpenSandbox sandbox",
#     tags=["OpenSandbox"],
# )
# async def pause_sandbox(sandbox_id: str):
#     """Pause a running sandbox (preserves state)."""
#     base_url = _opensandbox_base_url()
#     async with httpx.AsyncClient(timeout=15) as client:
#         try:
#             r = await client.post(
#                 f"{base_url}/v1/sandboxes/{sandbox_id}/pause",
#                 headers=_opensandbox_headers(),
#             )
#             r.raise_for_status()
#             return r.json()
#         except httpx.HTTPStatusError as e:
#             raise HTTPException(status_code=e.response.status_code,
#                                 detail=e.response.text)
#         except httpx.RequestError as e:
#             raise HTTPException(status_code=502, detail=str(e))


# @app.post(
#     "/sandboxes/{sandbox_id}/resume",
#     summary="Resume a paused OpenSandbox sandbox",
#     tags=["OpenSandbox"],
# )
# async def resume_sandbox(sandbox_id: str):
#     """Resume a previously paused sandbox."""
#     base_url = _opensandbox_base_url()
#     async with httpx.AsyncClient(timeout=15) as client:
#         try:
#             r = await client.post(
#                 f"{base_url}/v1/sandboxes/{sandbox_id}/resume",
#                 headers=_opensandbox_headers(),
#             )
#             r.raise_for_status()
#             return r.json()
#         except httpx.HTTPStatusError as e:
#             raise HTTPException(status_code=e.response.status_code,
#                                 detail=e.response.text)
#         except httpx.RequestError as e:
#             raise HTTPException(status_code=502, detail=str(e))


# @app.post(
#     "/sandboxes/{sandbox_id}/renew-expiration",
#     summary="Renew sandbox expiration",
#     tags=["OpenSandbox"],
# )
# async def renew_sandbox_expiration(sandbox_id: str):
#     """Extend the expiration/timeout of a running sandbox."""
#     base_url = _opensandbox_base_url()
#     async with httpx.AsyncClient(timeout=15) as client:
#         try:
#             r = await client.post(
#                 f"{base_url}/v1/sandboxes/{sandbox_id}/renew-expiration",
#                 headers=_opensandbox_headers(),
#             )
#             r.raise_for_status()
#             return r.json()
#         except httpx.HTTPStatusError as e:
#             raise HTTPException(status_code=e.response.status_code,
#                                 detail=e.response.text)
#         except httpx.RequestError as e:
#             raise HTTPException(status_code=502, detail=str(e))


# @app.get(
#     "/sandboxes/{sandbox_id}/endpoints/{port}",
#     summary="Get sandbox endpoint for a specific port",
#     tags=["OpenSandbox"],
# )
# async def get_sandbox_endpoint(sandbox_id: str, port: int):
#     """Retrieve the public endpoint URL for a port exposed by the sandbox."""
#     base_url = _opensandbox_base_url()
#     async with httpx.AsyncClient(timeout=15) as client:
#         try:
#             r = await client.get(
#                 f"{base_url}/v1/sandboxes/{sandbox_id}/endpoints/{port}",
#                 headers=_opensandbox_headers(),
#             )
#             r.raise_for_status()
#             return r.json()
#         except httpx.HTTPStatusError as e:
#             raise HTTPException(status_code=e.response.status_code,
#                                 detail=e.response.text)
#         except httpx.RequestError as e:
#             raise HTTPException(status_code=502, detail=str(e))


# # ─────────────────────────────────────────────
# # 12. Management endpoints
# # ─────────────────────────────────────────────

# @app.get(
#     "/health",
#     response_model=StatusResponse,
#     summary="Backend health check",
#     tags=["Management"],
# )
# def health():
#     """Returns whether the active backend is reachable."""
#     return StatusResponse(
#         backend=state.backend.name,
#         healthy=state.backend.health_check(),
#     )


# @app.post(
#     "/backend/switch",
#     response_model=MessageResponse,
#     summary="Hot-swap the sandbox backend",
#     tags=["Management"],
# )
# def switch_backend(req: SwitchRequest):
#     """Switch to a different sandbox backend at runtime."""
#     try:
#         state.switch(req.backend, validate=req.validate)
#     except (ValueError, RuntimeError) as e:
#         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
#     return MessageResponse(
#         message=f"Switched to '{req.backend}' backend successfully.",
#         backend=state.backend.name,
#     )


# @app.get(
#     "/backend",
#     response_model=StatusResponse,
#     summary="Current backend info",
#     tags=["Management"],
# )
# def current_backend():
#     """Returns the name and health of the currently active backend."""
#     return StatusResponse(
#         backend=state.backend.name,
#         healthy=state.backend.health_check(),
#     )


# @app.get(
#     "/backends",
#     summary="List all registered backends",
#     tags=["Management"],
# )
# def list_backends():
#     """Returns all available backend names."""
#     dynamic = [k.replace('BACKEND_URL_', '').lower() for k in os.environ if k.startswith('BACKEND_URL_')]
#     return {"backends": list(_REGISTRY.keys()) + dynamic}


# # ─────────────────────────────────────────────
# # 13. Per-backend docs + OpenAPI spec proxy  ✨ FIXED
# # ─────────────────────────────────────────────

# @app.get(
#     "/backend/{backend_name}/openapi.json",
#     include_in_schema=False,
# )
# async def get_backend_openapi_spec(backend_name: str):
#     """
#     Fetches the real OpenAPI spec from the backend service and rewrites
#     server URLs so that Swagger's 'Try it out' sends requests through
#     THIS proxy — meaning the API key is injected automatically.
#     """
#     env_key  = f"BACKEND_URL_{backend_name.upper()}"
#     base_url = os.environ.get(env_key)

#     if not base_url and backend_name not in _REGISTRY:
#         raise HTTPException(status_code=404, detail=f"Backend '{backend_name}' not found")

#     if base_url:
#         # Try to fetch the real spec from the upstream service
#         for spec_path in ["/openapi.json", "/v1/openapi.json", "/docs/openapi.json"]:
#             try:
#                 async with httpx.AsyncClient(timeout=5) as client:
#                     r = await client.get(f"{base_url}{spec_path}")
#                     if r.status_code == 200:
#                         spec = r.json()
#                         # Rewrite servers so Swagger UI uses our proxy
#                         spec["servers"] = [{"url": f"/backend/{backend_name}"}]
#                         # Add security scheme info to the spec
#                         spec.setdefault("components", {})
#                         spec["components"].setdefault("securitySchemes", {})
#                         spec["components"]["securitySchemes"]["ApiKeyAuth"] = {
#                             "type": "apiKey",
#                             "in":   "header",
#                             "name": "OPEN-SANDBOX-API-KEY",
#                         }
#                         return JSONResponse(content=spec)
#             except Exception:
#                 continue

#     # Fallback: return THIS app's own spec filtered to sandbox routes
#     return JSONResponse(content=app.openapi())


# @app.get(
#     "/backend/{backend_name}/docs",
#     include_in_schema=False,
# )
# async def get_backend_docs(backend_name: str):
#     """
#     Serves a Swagger UI page pointing at the backend's (proxied) OpenAPI spec.
#     Visiting /backend/opensandbox/docs gives you a fully functional UI for
#     OpenSandbox where 'Try it out' works transparently through the proxy.
#     """
#     env_key = f"BACKEND_URL_{backend_name.upper()}"
#     if env_key not in os.environ and backend_name not in _REGISTRY:
#         raise HTTPException(status_code=404,
#                             detail=f"Backend '{backend_name}' not found")

#     return get_swagger_ui_html(
#         openapi_url=f"/backend/{backend_name}/openapi.json",
#         title=f"{backend_name.capitalize()} — Sandbox API Docs",
#         swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
#         swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
#     )


# @app.get(
#     "/backend/{backend_name}",
#     summary="Get info about a specific backend",
#     tags=["Management"],
# )
# def get_backend_info(backend_name: str):
#     """Returns the registration info and health status of a specific backend."""
#     try:
#         backend = create_backend(backend_name)
#         return {
#             "backend":   backend_name,
#             "healthy":   backend.health_check(),
#             "type":      type(backend).__name__,
#             "message":   "Use POST to this endpoint to execute code.",
#             "docs_url":  f"/backend/{backend_name}/docs",
#             "api_proxy": f"/backend/{backend_name}/...",
#         }
#     except ValueError as e:
#         raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# # ─────────────────────────────────────────────
# # 14. Transparent proxy  (catches everything else)
# # ─────────────────────────────────────────────

# @app.api_route(
#     "/backend/{backend_name}/{proxy_path:path}",
#     methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
#     summary="Transparently proxy requests to the backend",
#     tags=["Management"],
# )
# async def proxy_backend(backend_name: str, proxy_path: str, request: Request):
#     """
#     Proxies all HTTP methods and paths to the target backend service.
#     Automatically injects the OPEN-SANDBOX-API-KEY header for opensandbox.

#     Example: GET /backend/opensandbox/v1/sandboxes
#           → GET http://localhost:8080/v1/sandboxes
#     """
#     env_key  = f"BACKEND_URL_{backend_name.upper()}"
#     base_url = os.environ.get(env_key)
#     if not base_url:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail=f"Backend '{backend_name}' not configured. "
#                    f"Set {env_key} environment variable."
#         )

#     target_url = f"{base_url.rstrip('/')}/{proxy_path}"
#     params     = dict(request.query_params)
#     body       = await request.body()
#     headers    = {
#         k: v for k, v in request.headers.items()
#         if k.lower() not in ["host", "content-length"]
#     }

#     # ── Inject API key automatically for opensandbox ──
#     if backend_name.lower() == "opensandbox":
#         api_key = os.environ.get("OPENSANDBOX_API_KEY", "your-secure-api-key")
#         headers["OPEN-SANDBOX-API-KEY"] = api_key

#     async with httpx.AsyncClient() as client:
#         try:
#             resp = await client.request(
#                 method=request.method,
#                 url=target_url,
#                 params=params,
#                 content=body,
#                 headers=headers,
#                 timeout=60.0,
#             )
#             return Response(
#                 content=resp.content,
#                 status_code=resp.status_code,
#                 headers={
#                     k: v for k, v in resp.headers.items()
#                     if k.lower() not in ["content-encoding", "transfer-encoding"]
#                 },
#             )
#         except Exception as e:
#             raise HTTPException(
#                 status_code=status.HTTP_502_BAD_GATEWAY,
#                 detail=f"Proxy error to {target_url}: {e}",
#             )


# # ─────────────────────────────────────────────
# # 15. Session endpoints
# # ─────────────────────────────────────────────

# @app.post(
#     "/session",
#     response_model=SessionResponse,
#     status_code=status.HTTP_201_CREATED,
#     summary="Open a sandbox session",
#     tags=["Sessions"],
# )
# def open_session():
#     """Open a persistent session in the active backend."""
#     session = state.backend.open_session()
#     state.add_session(session)
#     return session


# @app.delete(
#     "/session/{session_id}",
#     status_code=status.HTTP_204_NO_CONTENT,
#     summary="Close a sandbox session",
#     tags=["Sessions"],
# )
# def close_session(session_id: str):
#     """Release all resources held by a session."""
#     session = state.get_session(session_id)
#     if not session:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail=f"Session '{session_id}' not found.",
#         )
#     state.backend.close_session(session_id)
#     state.remove_session(session_id)


# @app.get(
#     "/session/{session_id}",
#     response_model=SessionResponse,
#     summary="Get session info",
#     tags=["Sessions"],
# )
# def get_session(session_id: str):
#     session = state.get_session(session_id)
#     if not session:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND,
#             detail=f"Session '{session_id}' not found.",
#         )
#     return session


# # ─────────────────────────────────────────────
# # 16. Run directly
# # ─────────────────────────────────────────────

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run("sandbox_fastapi:app", host="0.0.0.0", port=8000, reload=True)



"""
sandbox_fastapi.py
==================
Main entry point for the Sandbox API.

Responsibilities
----------------
* Define the swappable-backend abstraction (``SandboxBackend`` ABC +
  concrete implementations: Mock, Subprocess, Docker, E2B, GenericHTTP).
* Manage global ``AppState`` (active backend, open sessions).
* Own every route that creates, lists, inspects, or deletes sandboxes,
  as well as the transparent proxy that forwards arbitrary HTTP traffic
  to a configured backend.
* Mount the ``codeinterpreter`` router, which handles all *execution*
  logic (running code, pausing, resuming, renewing expiration, port
  endpoints).

Install:
    pip install fastapi uvicorn httpx

Run:
    uvicorn sandbox_fastapi:app --reload --port 8000

Environment variables:
    BACKEND_URL_OPENSANDBOX=http://localhost:8080
    OPENSANDBOX_API_KEY=your-secure-api-key

Auto-generated docs:
    http://localhost:8000/docs
    http://localhost:8000/backend/opensandbox/docs   (proxied OpenSandbox UI)
"""

from __future__ import annotations

import abc
import asyncio
import os
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from enum import Enum
from typing import Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ── Import execution logic and OpenSandbox helpers from the sub-module ──
from codeinterpreter import (
    SandboxCreateRequest,
    opensandbox_base_url,
    opensandbox_headers,
    router as codeinterpreter_router,
)


# ─────────────────────────────────────────────
# 1.  Enums & Pydantic schemas  (stable public contract)
# ─────────────────────────────────────────────


class Backend(str, Enum):
    MOCK = "mock"
    SUBPROCESS = "subprocess"
    DOCKER = "docker"
    E2B = "e2b"


class Language(str, Enum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    BASH = "bash"


# ── Request models ───────────────────────────


class RunRequest(BaseModel):
    code: str = Field(..., example="print('hello')")
    language: Language = Field(Language.PYTHON)
    timeout: int = Field(30, ge=1, le=120)


class SwitchRequest(BaseModel):
    backend: Backend = Field(..., example="docker")
    validate: bool = Field(True, description="Health-check before switching")


class BatchSandboxCreateRequest(BaseModel):
    count: int = Field(1, ge=1, le=10, description="Number of sandboxes to create (max 10)")
    # Re-use the sub-fields of SandboxCreateRequest rather than duplicating them
    image: Any = Field(default=None)
    entrypoint: list[str] = Field(default=["/opt/opensandbox/code-interpreter.sh"])
    timeout: int = Field(600, ge=1, le=3600)
    env: dict[str, str] = Field(default={"PYTHON_VERSION": "3.11"})
    resourceLimits: Any = Field(default=None)
    metadata: dict[str, str] = Field(
        default={"project": "my-ai-agent", "environment": "production"}
    )


# ── Response models ──────────────────────────


class RunResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    sandbox_id: str
    backend: str


class SessionResponse(BaseModel):
    session_id: str
    backend: str
    metadata: dict = {}


class StatusResponse(BaseModel):
    backend: str
    healthy: bool


class MessageResponse(BaseModel):
    message: str
    backend: str


class BatchSandboxResponse(BaseModel):
    created: int
    failed: int
    sandboxes: list[dict]
    errors: list[str]


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
    def name(self):
        return Backend.MOCK

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

    def close_session(self, session_id):
        pass

    def health_check(self):
        return True


class SubprocessBackend(SandboxBackend):
    _CMD = {
        "python": ["python3", "-c"],
        "javascript": ["node", "-e"],
        "bash": ["bash", "-c"],
    }

    @property
    def name(self):
        return Backend.SUBPROCESS

    def run(self, code, language, timeout):
        prefix = self._CMD.get(language)
        if not prefix:
            return RunResponse(
                stdout="",
                stderr=f"Unsupported language: {language}",
                exit_code=1,
                duration_ms=0,
                sandbox_id="",
                backend=self.name,
            )
        t0 = time.perf_counter()
        try:
            p = subprocess.run(
                prefix + [code], capture_output=True, text=True, timeout=timeout
            )
            return RunResponse(
                stdout=p.stdout,
                stderr=p.stderr,
                exit_code=p.returncode,
                duration_ms=(time.perf_counter() - t0) * 1000,
                sandbox_id=str(uuid.uuid4()),
                backend=self.name,
            )
        except subprocess.TimeoutExpired:
            return RunResponse(
                stdout="",
                stderr="Timed out",
                exit_code=124,
                duration_ms=timeout * 1000,
                sandbox_id=str(uuid.uuid4()),
                backend=self.name,
            )

    def open_session(self):
        return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

    def close_session(self, session_id):
        pass

    def health_check(self):
        try:
            subprocess.run(["python3", "--version"], capture_output=True, timeout=3)
            return True
        except Exception:
            return False


class DockerBackend(SandboxBackend):
    _IMAGES = {
        "python": "python:3.12-slim",
        "javascript": "node:20-slim",
        "bash": "bash:5",
    }
    _CMDS = {
        "python": "python3 -c",
        "javascript": "node -e",
        "bash": "bash -c",
    }

    @property
    def name(self):
        return Backend.DOCKER

    def run(self, code, language, timeout):
        image = self._IMAGES.get(language)
        if not image:
            return RunResponse(
                stdout="",
                stderr=f"Unsupported language: {language}",
                exit_code=1,
                duration_ms=0,
                sandbox_id="",
                backend=self.name,
            )
        t0 = time.perf_counter()
        cmd = (
            f"docker run --rm --network none --memory 128m --cpus 0.5 "
            f'{image} {self._CMDS[language]} "{code}"'
        )
        try:
            p = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=timeout + 5
            )
            return RunResponse(
                stdout=p.stdout,
                stderr=p.stderr,
                exit_code=p.returncode,
                duration_ms=(time.perf_counter() - t0) * 1000,
                sandbox_id=str(uuid.uuid4()),
                backend=self.name,
            )
        except subprocess.TimeoutExpired:
            return RunResponse(
                stdout="",
                stderr="Container timed out",
                exit_code=124,
                duration_ms=timeout * 1000,
                sandbox_id=str(uuid.uuid4()),
                backend=self.name,
            )

    def open_session(self):
        cid = f"sbx-{uuid.uuid4().hex[:8]}"
        return SessionResponse(
            session_id=cid, backend=self.name, metadata={"container_id": cid}
        )

    def close_session(self, session_id):
        subprocess.run(["docker", "rm", "-f", session_id], capture_output=True)

    def health_check(self):
        try:
            r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False


class E2BBackend(SandboxBackend):
    @property
    def name(self):
        return Backend.E2B

    def run(self, code, language, timeout):
        try:
            from e2b_code_interpreter import Sandbox  # type: ignore
        except ImportError:
            return RunResponse(
                stdout="",
                stderr="Install: pip install e2b-code-interpreter",
                exit_code=1,
                duration_ms=0,
                sandbox_id="",
                backend=self.name,
            )
        key = os.environ.get("E2B_API_KEY")
        if not key:
            return RunResponse(
                stdout="",
                stderr="E2B_API_KEY not set",
                exit_code=1,
                duration_ms=0,
                sandbox_id="",
                backend=self.name,
            )
        t0 = time.perf_counter()
        with Sandbox(api_key=key) as sbx:
            r = sbx.run_code(code)
            return RunResponse(
                stdout="\n".join(r.logs.stdout),
                stderr="\n".join(r.logs.stderr),
                exit_code=0 if not r.error else 1,
                duration_ms=(time.perf_counter() - t0) * 1000,
                sandbox_id=sbx.sandbox_id,
                backend=self.name,
            )

    def open_session(self):
        return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

    def close_session(self, session_id):
        pass

    def health_check(self):
        return bool(os.environ.get("E2B_API_KEY"))


class GenericHTTPBackend(SandboxBackend):
    def __init__(self, name: str, url: str):
        self._name = name
        self._url = url

    @property
    def name(self):
        return self._name

    def run(self, code, language, timeout):
        payload = {"code": code, "language": language, "timeout": timeout}
        t0 = time.perf_counter()
        try:
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
            return RunResponse(
                stdout="",
                stderr=f"Generic HTTP Backend Error ({self.name}): {e}",
                exit_code=1,
                duration_ms=0,
                sandbox_id="",
                backend=self.name,
            )

    def open_session(self):
        return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

    def close_session(self, session_id):
        pass

    def health_check(self):
        try:
            with httpx.Client(timeout=3) as client:
                r = client.get(self._url)
                return r.status_code < 500
        except Exception:
            return False


# ─────────────────────────────────────────────
# 4.  Registry + factory
# ─────────────────────────────────────────────

_REGISTRY: dict[str, type[SandboxBackend]] = {
    Backend.MOCK: MockBackend,
    Backend.SUBPROCESS: SubprocessBackend,
    Backend.DOCKER: DockerBackend,
    Backend.E2B: E2BBackend,
}


def register_backend(name: str, cls: type[SandboxBackend]) -> None:
    """Register a custom backend class under ``name``."""
    _REGISTRY[name] = cls


def create_backend(name: str) -> SandboxBackend:
    """Instantiate a backend by name, falling back to GenericHTTPBackend."""
    cls = _REGISTRY.get(name)
    if cls:
        return cls()

    env_key = f"BACKEND_URL_{name.upper()}"
    url = os.environ.get(env_key)
    if url:
        return GenericHTTPBackend(name, url)

    available = list(_REGISTRY.keys()) + [
        k.replace("BACKEND_URL_", "").lower()
        for k in os.environ
        if k.startswith("BACKEND_URL_")
    ]
    raise ValueError(f"Unknown backend '{name}'. Available: {available}")


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
    print(f"[startup] active backend       → {state.backend.name}")
    print(f"[startup] OpenSandbox base URL → {opensandbox_base_url()}")
    yield
    print("[shutdown] cleaning up")


app = FastAPI(
    title="Sandbox API",
    description=(
        "Execute code in swappable sandbox backends. "
        "Switch backends without changing client code.\n\n"
        "**OpenSandbox Docs**: [/backend/opensandbox/docs](/backend/opensandbox/docs)"
    ),
    version="2.0.0",
    lifespan=lifespan,
)

# Mount execution routes defined in codeinterpreter.py
app.include_router(codeinterpreter_router)


# ─────────────────────────────────────────────
# 7.  Core execution endpoints
# ─────────────────────────────────────────────


@app.post(
    "/run",
    response_model=RunResponse,
    summary="Execute code in the active backend",
    tags=["Execution"],
)
def run_code(req: RunRequest):
    """Execute code using whichever backend is currently selected."""
    return state.backend.run(req.code, req.language.value, req.timeout)


@app.post(
    "/backend/{backend_name}",
    response_model=RunResponse,
    summary="Execute code in a specific backend directly",
    tags=["Execution"],
)
def run_in_backend(backend_name: str, req: RunRequest):
    """Bypass the globally selected backend and run in a specific one."""
    try:
        backend = create_backend(backend_name)
        return backend.run(req.code, req.language.value, req.timeout)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ─────────────────────────────────────────────
# 8.  OpenSandbox — sandbox creation
# ─────────────────────────────────────────────


@app.post(
    "/sandboxes",
    summary="Create a single OpenSandbox sandbox",
    tags=["OpenSandbox"],
    status_code=status.HTTP_201_CREATED,
)
async def create_sandbox(req: SandboxCreateRequest):
    """
    Create a single sandbox via OpenSandbox.

    Equivalent curl::

        curl -X POST http://localhost:8080/v1/sandboxes \\
          -H "OPEN-SANDBOX-API-KEY: your-key" \\
          -d '{"image": {"uri": "codeintepreter:1.0.0"}, ...}'
    """
    base_url = opensandbox_base_url()
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(
                f"{base_url}/v1/sandboxes",
                json=req.model_dump(),
                headers=opensandbox_headers(),
            )
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"OpenSandbox error: {exc.response.text}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not reach OpenSandbox at {base_url}: {exc}",
            ) from exc


@app.post(
    "/sandboxes/batch",
    response_model=BatchSandboxResponse,
    summary="Create multiple OpenSandbox sandboxes at once",
    tags=["OpenSandbox"],
    status_code=status.HTTP_201_CREATED,
)
async def create_sandboxes_batch(req: BatchSandboxCreateRequest):
    """
    Create **1–10 sandboxes concurrently** via OpenSandbox.

    All sandboxes share the same configuration; each is tagged with a
    ``batch_index`` metadata key so they are distinguishable.  The
    response includes both successful and failed creations.
    """
    base_url = opensandbox_base_url()
    headers = opensandbox_headers()

    # Build a single payload template (re-used for every sandbox)
    payload = {
        "image": req.image,
        "entrypoint": req.entrypoint,
        "timeout": req.timeout,
        "env": req.env,
        "resourceLimits": req.resourceLimits,
        "metadata": req.metadata,
    }

    async def _create_one(index: int) -> tuple[dict | None, str | None]:
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                tagged = {
                    **payload,
                    "metadata": {**payload["metadata"], "batch_index": str(index)},
                }
                r = await client.post(
                    f"{base_url}/v1/sandboxes",
                    json=tagged,
                    headers=headers,
                )
                r.raise_for_status()
                return r.json(), None
            except httpx.HTTPStatusError as exc:
                return None, f"[{index}] HTTP {exc.response.status_code}: {exc.response.text}"
            except Exception as exc:
                return None, f"[{index}] {type(exc).__name__}: {exc}"

    results = await asyncio.gather(*[_create_one(i) for i in range(req.count)])

    sandboxes: list[dict] = []
    errors: list[str] = []
    for result, error in results:
        if result is not None:
            sandboxes.append(result)
        if error is not None:
            errors.append(error)

    return BatchSandboxResponse(
        created=len(sandboxes),
        failed=len(errors),
        sandboxes=sandboxes,
        errors=errors,
    )


# ─────────────────────────────────────────────
# 9.  OpenSandbox — list, get, delete
# ─────────────────────────────────────────────


@app.get(
    "/sandboxes",
    summary="List all OpenSandbox sandboxes",
    tags=["OpenSandbox"],
)
async def list_sandboxes():
    """Return all active sandboxes from OpenSandbox."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(
                f"{opensandbox_base_url()}/v1/sandboxes",
                headers=opensandbox_headers(),
            )
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code, detail=exc.response.text
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get(
    "/sandboxes/{sandbox_id}",
    summary="Get a specific OpenSandbox sandbox",
    tags=["OpenSandbox"],
)
async def get_sandbox(sandbox_id: str):
    """Fetch details for a single sandbox by ID."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(
                f"{opensandbox_base_url()}/v1/sandboxes/{sandbox_id}",
                headers=opensandbox_headers(),
            )
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code, detail=exc.response.text
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.delete(
    "/sandboxes/{sandbox_id}",
    summary="Delete an OpenSandbox sandbox",
    tags=["OpenSandbox"],
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_sandbox(sandbox_id: str):
    """Permanently delete a sandbox and free its resources."""
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.delete(
                f"{opensandbox_base_url()}/v1/sandboxes/{sandbox_id}",
                headers=opensandbox_headers(),
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code, detail=exc.response.text
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


# ─────────────────────────────────────────────
# 10. Management endpoints
# ─────────────────────────────────────────────


@app.get(
    "/health",
    response_model=StatusResponse,
    summary="Backend health check",
    tags=["Management"],
)
def health():
    """Return whether the currently active backend is reachable."""
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
    """Switch to a different sandbox backend at runtime."""
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
    """Return the name and health of the currently active backend."""
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
    """Return all available backend names (static registry + env-configured)."""
    dynamic = [
        k.replace("BACKEND_URL_", "").lower()
        for k in os.environ
        if k.startswith("BACKEND_URL_")
    ]
    return {"backends": list(_REGISTRY.keys()) + dynamic}


# ─────────────────────────────────────────────
# 11. Per-backend docs + OpenAPI spec proxy
# ─────────────────────────────────────────────


@app.get("/backend/{backend_name}/openapi.json", include_in_schema=False)
async def get_backend_openapi_spec(backend_name: str):
    """
    Fetch the real OpenAPI spec from an upstream backend, rewrite its
    ``servers`` list so that Swagger's *Try it out* button routes
    through this proxy (injecting the API key automatically).
    """
    env_key = f"BACKEND_URL_{backend_name.upper()}"
    base_url = os.environ.get(env_key)

    if not base_url and backend_name not in _REGISTRY:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_name}' not found")

    if base_url:
        for spec_path in ["/openapi.json", "/v1/openapi.json", "/docs/openapi.json"]:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(f"{base_url}{spec_path}")
                    if r.status_code == 200:
                        spec = r.json()
                        spec["servers"] = [{"url": f"/backend/{backend_name}"}]
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

    return JSONResponse(content=app.openapi())


@app.get("/backend/{backend_name}/docs", include_in_schema=False)
async def get_backend_docs(backend_name: str):
    """
    Serve a Swagger UI page pointing at the backend's (proxied) OpenAPI
    spec.  Visiting ``/backend/opensandbox/docs`` gives a fully
    functional UI where *Try it out* works transparently via the proxy.
    """
    env_key = f"BACKEND_URL_{backend_name.upper()}"
    if env_key not in os.environ and backend_name not in _REGISTRY:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_name}' not found")

    return get_swagger_ui_html(
        openapi_url=f"/backend/{backend_name}/openapi.json",
        title=f"{backend_name.capitalize()} — Sandbox API Docs",
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
    )


@app.get(
    "/backend/{backend_name}",
    summary="Get info about a specific backend",
    tags=["Management"],
)
def get_backend_info(backend_name: str):
    """Return registration info and health status of a specific backend."""
    try:
        backend = create_backend(backend_name)
        return {
            "backend": backend_name,
            "healthy": backend.health_check(),
            "type": type(backend).__name__,
            "message": "Use POST to this endpoint to execute code.",
            "docs_url": f"/backend/{backend_name}/docs",
            "api_proxy": f"/backend/{backend_name}/...",
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


# ─────────────────────────────────────────────
# 12. Transparent proxy  (catch-all)
# ─────────────────────────────────────────────


@app.api_route(
    "/backend/{backend_name}/{proxy_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    summary="Transparently proxy requests to the backend",
    tags=["Management"],
)
async def proxy_backend(backend_name: str, proxy_path: str, request: Request):
    """
    Forward any HTTP request to the target backend service, injecting
    the ``OPEN-SANDBOX-API-KEY`` header automatically for opensandbox.

    Example::

        GET /backend/opensandbox/v1/sandboxes
        → GET http://localhost:8080/v1/sandboxes
    """
    env_key = f"BACKEND_URL_{backend_name.upper()}"
    base_url = os.environ.get(env_key)
    if not base_url:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Backend '{backend_name}' not configured. "
                f"Set {env_key} environment variable."
            ),
        )

    target_url = f"{base_url.rstrip('/')}/{proxy_path}"
    params = dict(request.query_params)
    body = await request.body()
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ["host", "content-length"]
    }

    if backend_name.lower() == "opensandbox":
        headers["OPEN-SANDBOX-API-KEY"] = os.environ.get(
            "OPENSANDBOX_API_KEY", "your-secure-api-key"
        )

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
                    k: v
                    for k, v in resp.headers.items()
                    if k.lower() not in ["content-encoding", "transfer-encoding"]
                },
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Proxy error to {target_url}: {exc}",
            )


# ─────────────────────────────────────────────
# 13. Session endpoints
# ─────────────────────────────────────────────


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
    """Return metadata for an open session."""
    session = state.get_session(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return session


# ─────────────────────────────────────────────
# 14. Run directly
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("sandbox_fastapi:app", host="0.0.0.0", port=8000, reload=True)