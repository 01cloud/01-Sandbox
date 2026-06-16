"""
private_clone.py — Modular functions for secure private repository scanning.

This module handles:
1. URL parsing to identify Git providers (GitHub, GitLab, Bitbucket).
2. Credential sanitation to scrub PATs and SSH keys from logs/outputs.
3. Universal repo checking using 'git ls-remote'.
4. Authenticated clone executions using subprocesses.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from typing import Optional, Tuple
from urllib.parse import urlparse, urlunparse

_TAG = "[RepoScanner][PrivateClone]"


def parse_provider(repo_url: str) -> str:
    """Identify the provider from the repo URL."""
    url_lower = repo_url.lower()
    if "github" in url_lower:
        return "github"
    elif "gitlab" in url_lower:
        return "gitlab"
    elif "bitbucket" in url_lower:
        return "bitbucket"
    return "unknown"


def sanitize_credentials(
    text: str, git_token: Optional[str] = None, ssh_key: Optional[str] = None
) -> str:
    """Scrub raw tokens and SSH private key lines from output text."""
    if not text:
        return text
    if git_token:
        text = text.replace(git_token, "******")
    if ssh_key:
        lines = [line.strip() for line in ssh_key.splitlines() if line.strip()]
        for line in lines:
            if len(line) > 10 and "PRIVATE KEY" not in line:
                text = text.replace(line, "******")
        text = text.replace(ssh_key, "******")
    return text


def get_authenticated_url(url: str, token: str) -> str:
    """Rewrite HTTP/HTTPS Git URLs to embed token authentication dynamically."""
    parsed = urlparse(url)
    netloc = parsed.netloc

    # Strip existing user info if present (e.g. user@host -> host)
    if "@" in netloc:
        netloc = netloc.split("@")[-1]

    host = parsed.hostname or ""
    if "github.com" in host:
        new_netloc = f"{token}@{netloc}"
    elif "gitlab.com" in host:
        new_netloc = f"oauth2:{token}@{netloc}"
    elif "bitbucket.org" in host:
        new_netloc = f"x-token-auth:{token}@{netloc}"
    else:
        new_netloc = f"{token}@{netloc}"

    return urlunparse(parsed._replace(netloc=new_netloc))


async def run_git_command(
    command: list[str],
    cwd: str,
    git_token: Optional[str] = None,
    ssh_key: Optional[str] = None,
    timeout: float = 120.0,
) -> Tuple[str, str, int]:
    """Execute a git command with optional credentials in an isolated env."""
    env = {**os.environ}
    temp_key_file = None

    try:
        # Write SSH key ephemerally if provided
        if ssh_key:
            f = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix="_id_rsa")
            try:
                # Add trailing newline if missing
                clean_key = ssh_key.strip() + "\n"
                f.write(clean_key)
                f.flush()
                temp_key_file = f.name
            finally:
                f.close()

            os.chmod(temp_key_file, 0o600)
            env[
                "GIT_SSH_COMMAND"
            ] = f"ssh -i {temp_key_file} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes"

        # Apply HTTPS token rewrites to command arguments
        rewritten_command = []
        for arg in command:
            if git_token and (arg.startswith("http://") or arg.startswith("https://")):
                rewritten_command.append(get_authenticated_url(arg, git_token))
            else:
                rewritten_command.append(arg)

        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *rewritten_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            ),
            timeout=timeout,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        rc = proc.returncode or 0

        # Scrub credentials from stdout and stderr
        stdout = sanitize_credentials(stdout, git_token, ssh_key)
        stderr = sanitize_credentials(stderr, git_token, ssh_key)

        return stdout, stderr, rc

    except asyncio.TimeoutError:
        return "", "Git command timed out", 1
    except Exception as exc:
        err_msg = sanitize_credentials(str(exc), git_token, ssh_key)
        return "", f"Git command failed: {err_msg}", 1
    finally:
        # Secure cleanup of temp deploy keys
        if temp_key_file and os.path.exists(temp_key_file):
            try:
                os.remove(temp_key_file)
            except Exception as e:
                print(f"{_TAG} Failed to delete temp SSH key file {temp_key_file}: {e}")


async def check_repo_access(
    repo_url: str,
    git_token: Optional[str] = None,
    ssh_key: Optional[str] = None,
) -> dict:
    """
    Query the repository's HEAD via 'git ls-remote' to check status.
    Returns:
        {"accessible": bool, "requires_auth": bool, "provider": str, "error": str}
    """
    provider = parse_provider(repo_url)

    # Try public access first (no credentials)
    stdout, stderr, rc = await run_git_command(
        command=["git", "ls-remote", repo_url, "HEAD"],
        cwd=tempfile.gettempdir(),
        timeout=30.0,
    )

    if rc == 0:
        return {
            "accessible": True,
            "requires_auth": False,
            "provider": provider,
            "error": "",
        }

    # Check if stderr hints at authorization requirements
    stderr_lower = stderr.lower()
    auth_indicators = [
        "terminal prompts disabled",
        "authentication failed",
        "permission denied",
        "could not read username",
        "login",
        "password",
        "credentials",
    ]
    is_auth_error = any(ind in stderr_lower for ind in auth_indicators)

    # Retry with credentials if they are provided
    if git_token or ssh_key:
        stdout_cred, stderr_cred, rc_cred = await run_git_command(
            command=["git", "ls-remote", repo_url, "HEAD"],
            cwd=tempfile.gettempdir(),
            git_token=git_token,
            ssh_key=ssh_key,
            timeout=30.0,
        )
        if rc_cred == 0:
            return {
                "accessible": True,
                "requires_auth": True,
                "provider": provider,
                "error": "",
            }
        else:
            return {
                "accessible": False,
                "requires_auth": True,
                "provider": provider,
                "error": stderr_cred.strip(),
            }

    if is_auth_error:
        return {
            "accessible": False,
            "requires_auth": True,
            "provider": provider,
            "error": "Authentication required. Please provide a Personal Access Token or SSH Deploy Key.",
        }

    clean_error = stderr.strip() or "Repository not found or host unreachable."
    return {
        "accessible": False,
        "requires_auth": False,
        "provider": provider,
        "error": clean_error,
    }


async def clone_private_repo(
    sandbox_id: str,
    repo_url: str,
    git_token: Optional[str] = None,
    ssh_key: Optional[str] = None,
) -> Tuple[bool, str]:
    """Clone a repository inside the local sandbox using optional credentials."""
    from .sandbox_provisioner import REPO_DIR

    target = os.path.join(sandbox_id, REPO_DIR)

    stdout, stderr, rc = await run_git_command(
        command=["git", "clone", "--depth=1", repo_url, target],
        cwd=sandbox_id,
        git_token=git_token,
        ssh_key=ssh_key,
        timeout=180.0,
    )

    if rc == 0:
        return True, ""
    return False, stderr or stdout
