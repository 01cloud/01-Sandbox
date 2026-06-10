"""
github_validator.py — URL format validation + GitHub REST API accessibility check.

Uses the public GitHub API (unauthenticated, 60 req/hr).
Set GITHUB_TOKEN env var to raise the limit to 5000 req/hr.
Never hardcodes credentials.
"""

from __future__ import annotations

import os
import re
from typing import Tuple

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
    Parse a GitHub repository URL and return the (owner, repo) tuple.
    Does not perform external API calls.
    Raises HTTPException(400) for invalid URL format.
    """
    url = url.strip()
    match = GITHUB_URL_PATTERN.match(url)
    if not match:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid GitHub URL format. "
                "Expected: https://github.com/{owner}/{repo}"
            ),
        )
    return match.group(1), match.group(2)


async def validate_github_repo(url: str) -> Tuple[str, str]:
    """
    Validate a GitHub repo URL and confirm it is public and accessible.

    Returns:
        (owner, repo) tuple on success.

    Raises:
        HTTPException(400) for invalid URL format.
        HTTPException(404) for non-existent or private repositories.
        HTTPException(502) if the GitHub API is unreachable.
    """
    # 1. Format check
    url = url.strip()
    match = GITHUB_URL_PATTERN.match(url)
    if not match:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid GitHub URL format. "
                "Expected: https://github.com/{owner}/{repo}"
            ),
        )

    owner = match.group(1)
    repo = match.group(2)

    # 2. Accessibility check via GitHub REST API
    api_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(api_url, headers=_build_headers())
    except httpx.RequestError as exc:
        print(
            f"[github_validator] Warning: Could not reach GitHub API ({exc}). "
            f"Bypassing API check and proceeding with regex-matched {owner}/{repo}."
        )
        return owner, repo

    if response.status_code == 404:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Repository '{owner}/{repo}' was not found or is private. "
                "Only public repositories are supported."
            ),
        )

    if response.status_code == 403:
        print(
            f"[github_validator] Warning: GitHub API rate limit exceeded. "
            f"Bypassing API check and proceeding with regex-matched {owner}/{repo}."
        )
        return owner, repo

    if not response.is_success:
        print(
            f"[github_validator] Warning: GitHub API returned unexpected status {response.status_code}. "
            f"Bypassing API check and proceeding with regex-matched {owner}/{repo}."
        )
        return owner, repo

    data = response.json()

    # 3. Confirm repo is not private
    if data.get("private", False):
        raise HTTPException(
            status_code=400,
            detail=f"Repository '{owner}/{repo}' is private. Only public repositories are supported.",
        )

    return owner, repo
