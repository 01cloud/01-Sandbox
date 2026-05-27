"""
sandbox_provisioner.py — Sandbox lifecycle management for repo scanning.

Provisions sandboxes via the same state.backend (GenericHTTPBackend)
that Quick Scan and Bulk Scan use. Commands are executed inside the
sandbox via the OpenSandbox /exec endpoint.
"""

from __future__ import annotations

import json
import os
from typing import Optional, Tuple

import httpx
from config import opensandbox_base_url, opensandbox_headers, opensandbox_route_prefix

# Image used for scanning — the code-interpreter base image that has
# all detection tools (linguist, tokei, enry, bandit, eslint, etc.)
# pre-installed in Dockerfile_base.
DEFAULT_SCANNER_IMAGE = os.getenv(
    "SCANNER_IMAGE",
    "01community/01sandbox-codeinterpreter-base-image:1.0.1",
)

# Sandbox stays alive for 5 minutes max (constraint from spec)
SANDBOX_TIMEOUT_SECONDS = 300

REPO_DIR = "/repo"


async def provision_sandbox(backend) -> str:
    """
    Provision an isolated sandbox using the scanner base image.
    Returns the sandbox_id string.

    Uses GenericHTTPBackend.create_sandbox() — the same call made by
    POST /v1/sandboxes.
    """
    from models import CreateSandboxRequest, ImageSpec, ResourceLimits  # parent package

    req = CreateSandboxRequest(
        image=ImageSpec(uri=DEFAULT_SCANNER_IMAGE),
        entrypoint=["sleep", str(SANDBOX_TIMEOUT_SECONDS)],
        timeout=SANDBOX_TIMEOUT_SECONDS,
        env={},
        resourceLimits=ResourceLimits(cpu="500m", memory="1Gi"),
        metadata={"purpose": "repo-scan"},
    )
    result = backend.create_sandbox(req)
    return result.id


async def exec_in_sandbox(
    sandbox_id: str,
    command: list[str],
    workdir: Optional[str] = None,
    timeout: float = 120.0,
) -> Tuple[str, str, int]:
    """
    Execute a command inside the sandbox via the OpenSandbox exec endpoint.
    Returns (stdout, stderr, exit_code).

    Mirrors how scan jobs call the backend directly via httpx.
    """
    base_url = opensandbox_base_url()
    prefix = opensandbox_route_prefix()
    url = f"{base_url.rstrip('/')}{prefix}/sandboxes/{sandbox_id}/exec"

    payload: dict = {"command": command}
    if workdir:
        payload["workdir"] = workdir

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=opensandbox_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return (
                data.get("stdout", ""),
                data.get("stderr", ""),
                int(data.get("exit_code", 0)),
            )
    except httpx.HTTPStatusError as exc:
        return ("", f"HTTP error {exc.response.status_code}: {exc.response.text}", 1)
    except Exception as exc:
        return ("", f"exec failed: {exc}", 1)


async def clone_repo(sandbox_id: str, repo_url: str) -> Tuple[bool, str]:
    """
    Clone the repository into REPO_DIR inside the sandbox using --depth=1.
    Returns (success, error_message).
    """
    stdout, stderr, exit_code = await exec_in_sandbox(
        sandbox_id=sandbox_id,
        command=["git", "clone", "--depth=1", repo_url, REPO_DIR],
        timeout=180.0,
    )
    if exit_code != 0:
        return False, stderr or stdout
    return True, ""


async def destroy_sandbox(sandbox_id: str) -> None:
    """
    Delete the sandbox via DELETE /v1/sandboxes/{id}.
    Always called in a finally block so it runs even on error.
    """
    base_url = opensandbox_base_url()
    prefix = opensandbox_route_prefix()
    url = f"{base_url.rstrip('/')}{prefix}/sandboxes/{sandbox_id}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await client.delete(url, headers=opensandbox_headers())
    except Exception as exc:
        # Non-fatal — log and continue
        print(f"[RepoScanner] Warning: failed to destroy sandbox {sandbox_id}: {exc}")
