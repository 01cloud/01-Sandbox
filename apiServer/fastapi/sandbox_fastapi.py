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

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


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
    cls = _REGISTRY.get(name)
    if not cls:
        raise ValueError(f"Unknown backend '{name}'. Available: {list(_REGISTRY)}")
    return cls()


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
    return {"backends": list(_REGISTRY.keys())}


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