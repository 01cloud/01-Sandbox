"""
sandbox_provisioner.py — Sandbox lifecycle management for repo scanning.

Revised architecture: Since the OpenSandbox server has no /exec endpoint,
we clone the repository locally on the API pod using a subprocess git clone,
store cloned files in a local temp directory, and run tools via the
existing POST /scan-jobs pipeline.

The "sandbox_id" returned by provision_sandbox() is the path of the
local temp directory (e.g. /tmp/repo_abc123).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from typing import Optional, Tuple

# Subdirectory inside the temp sandbox where the repo is cloned
REPO_DIR = "repo"


async def provision_sandbox(backend) -> str:
    """
    Create an isolated temp directory to act as the local 'sandbox'.
    Returns the absolute path of the temp directory as the sandbox_id.
    """
    loop = asyncio.get_event_loop()
    tmpdir = await loop.run_in_executor(None, tempfile.mkdtemp, None, "reposcanner_")
    print(f"[RepoScanner] Provisioned local sandbox at: {tmpdir}")
    return tmpdir


async def exec_in_sandbox(
    sandbox_id: str,
    command: list[str],
    workdir: Optional[str] = None,
    timeout: float = 120.0,
) -> Tuple[str, str, int]:
    """
    Execute a command inside the local sandbox directory using asyncio subprocess.
    sandbox_id is the absolute path of the temp directory.

    Returns (stdout, stderr, exit_code).
    """
    effective_cwd = workdir or sandbox_id

    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=effective_cwd,
                env={**os.environ},
            ),
            timeout=timeout,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        return (
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
            proc.returncode or 0,
        )
    except asyncio.TimeoutError:
        return ("", f"Command timed out after {timeout}s", 1)
    except FileNotFoundError as exc:
        return ("", f"Command not found: {command[0]}: {exc}", 127)
    except Exception as exc:
        return ("", f"exec failed: {exc}", 1)


async def clone_repo(sandbox_id: str, repo_url: str) -> Tuple[bool, str]:
    """
    Clone the repository into REPO_DIR inside the sandbox using --depth=1.
    sandbox_id is the temp directory path.
    Returns (success, error_message).
    """
    target = os.path.join(sandbox_id, REPO_DIR)
    stdout, stderr, exit_code = await exec_in_sandbox(
        sandbox_id=sandbox_id,
        command=["git", "clone", "--depth=1", repo_url, target],
        timeout=180.0,
    )
    if exit_code != 0:
        return False, stderr or stdout
    print(f"[RepoScanner] Cloned repo to: {target}")
    return True, ""


async def destroy_sandbox(sandbox_id: str) -> None:
    """
    Delete the local temp directory. Always called in a finally block.
    """
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, shutil.rmtree, sandbox_id, True)
        print(f"[RepoScanner] Cleaned up local sandbox: {sandbox_id}")
    except Exception as exc:
        print(f"[RepoScanner] Warning: failed to destroy sandbox {sandbox_id}: {exc}")
