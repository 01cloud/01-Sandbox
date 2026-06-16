"""
github_validator.py — URL format validation + GitHub REST API accessibility check.

Uses the public GitHub API (unauthenticated, 60 req/hr).
Set GITHUB_TOKEN env var to raise the limit to 5000 req/hr.
Never hardcodes credentials.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple

import httpx
from fastapi import HTTPException

# Matches: https://github.com/owner/repo  (with optional .git and trailing slash)
GITHUB_URL_PATTERN = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+?)(?:\.git)?/?$"
)

GITHUB_API_BASE = "https://api.github.com"


def _build_headers() -> dict:
    """Build request headers, injecting GITHUB_TOKEN if available."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_github_url(url: str) -> Tuple[str, str]:
    """
    Parse any GitHub, GitLab, or Bitbucket repository URL and return (owner, repo).
    Supports HTTPS, SSH, and token-embedded URLs.
    Raises HTTPException(400) for invalid URL formats.
    """
    url = url.strip()

    # Try SSH format: git@host:owner/repo.git or ssh://git@host/owner/repo.git
    ssh_match = re.search(
        r"(?:^git@[a-zA-Z0-9\-.]+[:/]|^ssh://git@[a-zA-Z0-9\-.]+(?::[0-9]+)?/)([^/]+)/([^/]+?)(?:\.git)?/?$",
        url,
    )
    if ssh_match:
        return ssh_match.group(1), ssh_match.group(2)

    # Try HTTPS format: https://host/owner/repo.git
    http_match = re.search(
        r"^https?://(?:[^@/]+@)?[a-zA-Z0-9\-.]+/([^/]+)/([^/]+?)(?:\.git)?/?$", url
    )
    if http_match:
        return http_match.group(1), http_match.group(2)

    # Fallback to default regex check
    match = GITHUB_URL_PATTERN.match(url)
    if not match:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid repository URL format. "
                "Expected formats: HTTPS (https://github.com/owner/repo) or SSH (git@github.com:owner/repo.git)"
            ),
        )
    return match.group(1), match.group(2)


async def validate_github_repo(
    url: str,
    git_token: Optional[str] = None,
    ssh_key: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Validate a repository URL and confirm accessibility (public or private with auth).

    Returns:
        (owner, repo) tuple on success.

    Raises:
        HTTPException(400) for invalid URL format.
        HTTPException(404) for non-existent or unauthorized repositories.
    """
    # 1. Format check
    owner, repo = parse_github_url(url)

    # 2. Universal Git accessibility check using check_repo_access
    from .private_clone import check_repo_access

    result = await check_repo_access(url, git_token=git_token, ssh_key=ssh_key)

    if not result.get("accessible", False):
        status_code = 404 if "not found" in result.get("error", "").lower() else 400
        raise HTTPException(
            status_code=status_code,
            detail=result.get("error")
            or "Repository is private or unreachable. Please provide credentials.",
        )

    return owner, repo
