from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from scan_repository.models import RepoScanPrecheckRequest, RepoScanRequest
from scan_repository.private_clone import (
    check_repo_access,
    get_authenticated_url,
    parse_provider,
    sanitize_credentials,
)


def test_parse_provider():
    assert parse_provider("https://github.com/owner/repo") == "github"
    assert parse_provider("git@gitlab.com:owner/repo.git") == "gitlab"
    assert (
        parse_provider("https://x-token-auth@bitbucket.org/owner/repo") == "bitbucket"
    )
    assert parse_provider("https://other.com/owner/repo") == "unknown"


def test_sanitize_credentials():
    token = "git-token-xyz-123"
    ssh_key = "dummy-line-1-data\ndummy-line-2-data\ndummy-line-3-data"

    raw_log = f"Error: clone failed for https://{token}@github.com/owner/repo"
    sanitized = sanitize_credentials(raw_log, git_token=token)
    assert token not in sanitized
    assert "******" in sanitized

    raw_ssh_log = f"Identity file {ssh_key} could not be loaded"
    sanitized_ssh = sanitize_credentials(raw_ssh_log, ssh_key=ssh_key)
    assert "dummy-line-2-data" not in sanitized_ssh
    assert "******" in sanitized_ssh


def test_get_authenticated_url():
    token = "gitlab-token-abc"
    assert (
        get_authenticated_url("https://github.com/owner/repo", token)
        == f"https://{token}@github.com/owner/repo"
    )
    assert (
        get_authenticated_url("https://gitlab.com/owner/repo.git", token)
        == f"https://oauth2:{token}@gitlab.com/owner/repo.git"
    )
    assert (
        get_authenticated_url("https://bitbucket.org/owner/repo", token)
        == f"https://x-token-auth:{token}@bitbucket.org/owner/repo"
    )


@pytest.mark.asyncio
@patch("scan_repository.private_clone.run_git_command")
async def test_check_repo_access_public(mock_run):
    # Public repo returns 0 on public check
    mock_run.return_value = ("", "", 0)

    res = await check_repo_access("https://github.com/owner/repo")
    assert res["accessible"] is True
    assert res["requires_auth"] is False
    assert res["provider"] == "github"


@pytest.mark.asyncio
@patch("scan_repository.private_clone.run_git_command")
async def test_check_repo_access_private_requires_auth(mock_run):
    # First check (public) fails with auth indicator, second check is not run since no creds provided
    mock_run.return_value = ("", "terminal prompts disabled", 1)

    res = await check_repo_access("https://github.com/owner/repo")
    assert res["accessible"] is False
    assert res["requires_auth"] is True
    assert res["provider"] == "github"


@pytest.mark.asyncio
@patch("scan_repository.private_clone.run_git_command")
async def test_check_repo_access_private_with_valid_creds(mock_run):
    # Mock first check (public) to fail with auth indicator
    # Mock second check (with credentials) to succeed (rc=0)
    mock_run.side_effect = [("", "terminal prompts disabled", 1), ("", "", 0)]

    res = await check_repo_access("https://github.com/owner/repo", git_token="token")
    assert res["accessible"] is True
    assert res["requires_auth"] is True
    assert res["provider"] == "github"


def test_models_validation():
    # Valid model
    req = RepoScanRequest(
        repo_url="https://github.com/owner/repo", git_token="my_token"
    )
    assert req.repo_url == "https://github.com/owner/repo"
    assert req.git_token == "my_token"

    # Missing repo_url raises validation error
    with pytest.raises(ValidationError):
        RepoScanRequest()
