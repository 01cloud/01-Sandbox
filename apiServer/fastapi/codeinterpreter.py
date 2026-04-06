"""
codeinterpreter.py
==================
Execution-related logic for the OpenSandbox code interpreter.

This module owns:
  - OpenSandbox-specific Pydantic request / response models
  - The helper that builds OpenSandbox HTTP headers
  - All FastAPI route handlers that deal with sandbox *execution*
    (run code, pause, resume, renew-expiration, get endpoint)

It exposes a single APIRouter (``router``) that sandbox_fastapi.py
mounts with ``app.include_router(router)``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

# ─────────────────────────────────────────────
# 1.  OpenSandbox-specific Pydantic models
# ─────────────────────────────────────────────


class SandboxImageModel(BaseModel):
    uri: str = Field(
        "codeintepreter:1.0.0",
        description="Docker image URI for the sandbox",
    )


class ResourceLimitsModel(BaseModel):
    cpu: str = Field("1", description="CPU limit (e.g. '1', '0.5')")
    memory: str = Field("2Gi", description="Memory limit (e.g. '2Gi', '512Mi')")


class SandboxCreateRequest(BaseModel):
    image: SandboxImageModel = Field(default_factory=SandboxImageModel)
    entrypoint: list[str] = Field(
        default=["/opt/opensandbox/code-interpreter.sh"],
        description="Container entrypoint command",
    )
    timeout: int = Field(600, ge=1, le=3600, description="Sandbox timeout in seconds")
    env: dict[str, str] = Field(
        default={"PYTHON_VERSION": "3.11"},
        description="Environment variables",
    )
    resourceLimits: ResourceLimitsModel = Field(default_factory=ResourceLimitsModel)
    metadata: dict[str, str] = Field(
        default={"project": "my-ai-agent", "environment": "production"},
        description="Arbitrary metadata labels",
    )


class CodeRunRequest(BaseModel):
    """Payload sent to the sandbox's built-in code-interpreter endpoint."""

    sandbox_id: str = Field(..., description="ID of an already-running sandbox")
    code: str = Field(..., example="print('hello from sandbox')")
    language: str = Field("python", description="python | javascript | bash")
    timeout: int = Field(30, ge=1, le=120)


class CodeRunResponse(BaseModel):
    sandbox_id: str
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float


# ─────────────────────────────────────────────
# 2.  OpenSandbox HTTP helpers
# ─────────────────────────────────────────────


def opensandbox_headers() -> dict[str, str]:
    """
    Returns the headers required by every OpenSandbox API call.

    The API key is read from ``OPENSANDBOX_API_KEY``; fall back to a
    placeholder so the server starts without crashing even when the
    variable is absent.

    Set it before running::

        export OPENSANDBOX_API_KEY=your-secure-api-key
    """
    api_key = os.environ.get("OPENSANDBOX_API_KEY", "your-secure-api-key")
    return {
        "Content-Type": "application/json",
        "OPEN-SANDBOX-API-KEY": api_key,
    }


def opensandbox_base_url() -> str:
    """Returns the configured OpenSandbox server base URL."""
    return os.environ.get("BACKEND_URL_OPENSANDBOX", "http://localhost:8080")


# ─────────────────────────────────────────────
# 3.  Shared async helper  (avoids boilerplate per route)
# ─────────────────────────────────────────────


async def _forward(
    method: str,
    path: str,
    *,
    json: Any = None,
    timeout: float = 15.0,
) -> Any:
    """
    Fire a single request to the OpenSandbox server and return the
    parsed JSON body, or raise an appropriate HTTPException.

    Args:
        method:  HTTP verb ("GET", "POST", "DELETE", …).
        path:    Path relative to the base URL, e.g. ``"/v1/sandboxes"``.
        json:    Optional request body (will be serialised to JSON).
        timeout: Request timeout in seconds.

    Raises:
        HTTPException: Propagates upstream HTTP errors or network failures.
    """
    url = f"{opensandbox_base_url().rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.request(
                method,
                url,
                json=json,
                headers=opensandbox_headers(),
            )
            resp.raise_for_status()
            # DELETE returns 204 No Content — nothing to parse
            return resp.json() if resp.content else None
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"OpenSandbox error: {exc.response.text}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not reach OpenSandbox at {opensandbox_base_url()}: {exc}",
            ) from exc


# ─────────────────────────────────────────────
# 4.  Router  (mounted by sandbox_fastapi.py)
# ─────────────────────────────────────────────

router = APIRouter(tags=["CodeInterpreter"])


# ── Execute code inside a running sandbox ────

@router.post(
    "/sandboxes/{sandbox_id}/run",
    response_model=CodeRunResponse,
    summary="Run code inside a sandbox",
    status_code=status.HTTP_200_OK,
)
async def run_code_in_sandbox(sandbox_id: str, req: CodeRunRequest):
    """
    Send code to the code-interpreter endpoint of a running OpenSandbox
    sandbox and return its output.

    The sandbox must already be created (``POST /sandboxes``) and in a
    *running* state before you call this endpoint.

    Example::

        curl -X POST http://localhost:8000/sandboxes/sbx-abc123/run \\
          -H "Content-Type: application/json" \\
          -d '{"sandbox_id": "sbx-abc123", "code": "print(1+1)", "language": "python"}'
    """
    result = await _forward(
        "POST",
        f"/v1/sandboxes/{sandbox_id}/run",
        json={
            "code": req.code,
            "language": req.language,
            "timeout": req.timeout,
        },
        timeout=float(req.timeout) + 10,
    )
    # Normalise whatever the upstream returns into our response model
    return CodeRunResponse(
        sandbox_id=sandbox_id,
        stdout=result.get("stdout", ""),
        stderr=result.get("stderr", ""),
        exit_code=result.get("exit_code", 0),
        duration_ms=result.get("duration_ms", 0.0),
    )


# ── Lifecycle actions on an existing sandbox ─

@router.post(
    "/sandboxes/{sandbox_id}/pause",
    summary="Pause a running sandbox",
)
async def pause_sandbox(sandbox_id: str):
    """Pause a running sandbox (preserves its in-memory state)."""
    return await _forward("POST", f"/v1/sandboxes/{sandbox_id}/pause")


@router.post(
    "/sandboxes/{sandbox_id}/resume",
    summary="Resume a paused sandbox",
)
async def resume_sandbox(sandbox_id: str):
    """Resume a previously paused sandbox."""
    return await _forward("POST", f"/v1/sandboxes/{sandbox_id}/resume")


@router.post(
    "/sandboxes/{sandbox_id}/renew-expiration",
    summary="Extend a sandbox's expiration",
)
async def renew_sandbox_expiration(sandbox_id: str):
    """Extend the timeout of a running sandbox so it does not expire."""
    return await _forward("POST", f"/v1/sandboxes/{sandbox_id}/renew-expiration")


# ── Networking ───────────────────────────────

@router.get(
    "/sandboxes/{sandbox_id}/endpoints/{port}",
    summary="Get the public endpoint for a sandbox port",
)
async def get_sandbox_endpoint(sandbox_id: str, port: int):
    """
    Retrieve the externally reachable URL for a port that the sandbox
    exposes (e.g. a web-server or Jupyter kernel running inside it).
    """
    return await _forward("GET", f"/v1/sandboxes/{sandbox_id}/endpoints/{port}")