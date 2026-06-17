"""
sandbox_provisioner.py — Sandbox lifecycle management for repo scanning.

Revised architecture: Since the OpenSandbox server has no /exec endpoint,
we clone the repository locally on the API pod using a subprocess git clone,
store cloned files in a local temp directory, and run tools via the
existing POST /scan-jobs pipeline.

The "sandbox_id" returned by provision_sandbox() is the path of the
local temp directory (e.g. /tmp/reposcanner_abc123).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from typing import Optional, Tuple

# Subdirectory inside the temp sandbox where the repo is cloned
REPO_DIR = "repo"

_TAG = "[RepoScanner][Sandbox]"


async def provision_sandbox(backend) -> str:
    """
    Create an isolated temp directory to act as the local 'sandbox'.
    Returns the absolute path of the temp directory as the sandbox_id.
    """
    t0 = time.monotonic()
    loop = asyncio.get_event_loop()
    tmpdir = await loop.run_in_executor(None, tempfile.mkdtemp, None, "reposcanner_")
    elapsed = time.monotonic() - t0
    print(f"{_TAG} Provisioned local sandbox in {elapsed:.3f}s: {tmpdir}")
    try:
        from observability.metrics import sandbox_provision_duration_seconds

        sandbox_provision_duration_seconds.observe(elapsed)
    except Exception:
        pass
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
    cmd_str = " ".join(command)
    print(f"{_TAG} exec: {cmd_str}  (cwd={effective_cwd}, timeout={timeout}s)")
    t0 = time.monotonic()

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
        elapsed = time.monotonic() - t0
        rc = proc.returncode or 0
        print(f"{_TAG} exec done: exit={rc}, elapsed={elapsed:.2f}s, cmd={command[0]}")
        if rc != 0 and stderr_bytes:
            # Log first 300 chars of stderr so failures are visible in pod logs
            err_preview = stderr_bytes.decode("utf-8", errors="replace")[:300].strip()
            print(f"{_TAG} exec stderr (exit={rc}): {err_preview}")
        return (
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
            rc,
        )
    except asyncio.TimeoutError:
        print(f"{_TAG} exec TIMEOUT after {timeout}s: {cmd_str}")
        return ("", f"Command timed out after {timeout}s", 1)
    except FileNotFoundError as exc:
        print(f"{_TAG} exec MISSING BINARY: {command[0]} — {exc}")
        return ("", f"Command not found: {command[0]}: {exc}", 127)
    except Exception as exc:
        print(f"{_TAG} exec EXCEPTION: {exc}")
        return ("", f"exec failed: {exc}", 1)


async def clone_repo(
    sandbox_id: str,
    repo_url: str,
    git_token: Optional[str] = None,
    ssh_key: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Clone the repository into REPO_DIR inside the sandbox using --depth=1.
    sandbox_id is the temp directory path.
    Returns (success, error_message).
    """
    from .private_clone import clone_private_repo

    target = os.path.join(sandbox_id, REPO_DIR)
    print(
        f"{_TAG} git clone --depth=1 {repo_url} (auth provided: {bool(git_token or ssh_key)})"
    )
    print(f"{_TAG} Clone target directory: {target}")
    t0 = time.monotonic()

    success, err_msg = await clone_private_repo(
        sandbox_id=sandbox_id,
        repo_url=repo_url,
        git_token=git_token,
        ssh_key=ssh_key,
    )
    elapsed = time.monotonic() - t0

    try:
        from urllib.parse import urlparse

        from observability.metrics import repo_clone_duration_seconds

        host = urlparse(repo_url).hostname or "unknown"
        repo_clone_duration_seconds.labels(repo_host=host).observe(elapsed)
    except Exception:
        pass

    if not success:
        print(f"{_TAG} git clone FAILED (elapsed={elapsed:.1f}s)")
        print(f"{_TAG} git stderr: {err_msg[:500]}")
        return False, err_msg

    # Log size of cloned repo
    try:
        file_count = sum(len(files) for _, _, files in os.walk(target))
        print(
            f"{_TAG} git clone SUCCESS in {elapsed:.1f}s — {file_count} file(s) in {target}"
        )
    except Exception:
        print(f"{_TAG} git clone SUCCESS in {elapsed:.1f}s → {target}")

    return True, ""


async def destroy_sandbox(sandbox_id: str) -> None:
    """
    Delete the local temp directory. Always called in a finally block.
    """
    try:
        t0 = time.monotonic()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, shutil.rmtree, sandbox_id, True)
        elapsed = time.monotonic() - t0
        print(f"{_TAG} Sandbox destroyed in {elapsed:.3f}s: {sandbox_id}")
    except Exception as exc:
        print(f"{_TAG} WARNING: failed to destroy sandbox {sandbox_id}: {exc}")
