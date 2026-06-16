# Implementation Plan - Secure Private Repository Scans (Modularized)

Implement credential management to allow users to register secure personal access tokens (PATs) or deploy keys (SSH keys) to authorize scanning of private GitHub, GitLab, or Bitbucket repositories.

The application will not store tokens or SSH keys in the database. Instead, they will be supplied dynamically in the API request payload, propagated ephemerally through the execution queue, used in-memory / in temp files during cloning, and then immediately discarded.

We will introduce a dedicated module, `private_clone.py`, to encapsulate all private credential logic, repository validation, and authenticated cloning.

---

## User Review Required

> [!IMPORTANT]
> **Zero Database Persistence**
> - Credentials (PATs and SSH keys) will not be stored in Postgres or any persistent database.
> - Credentials will live only in memory in the FastAPI request context and in the temporary RabbitMQ message payload during job queuing. They are fully deleted from the worker pod after the cloning stage.

> [!NOTE]
> **Universal Git Validation**
> - We will use `git ls-remote` to validate repository existence and credential validity. This approach works universally for GitHub, GitLab, and Bitbucket, avoiding the need for provider-specific REST API integration.

---

## Proposed Changes

### 1. New Modular Logic for Private Repositories

#### [NEW] [private_clone.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/scan_repository/private_clone.py)
This module will handle all private repository features:
- **URL Parsing**: Support URL formats for GitHub, GitLab, and Bitbucket (both HTTPS and SSH).
- **Credential Sanitation**: A helper to scrub any occurrences of the raw PAT/SSH keys from CLI stdout, stderr, or exceptions before logging or returning them to the client.
- **Universal Validation via `git ls-remote`**:
  ```python
  async def check_repo_access(repo_url: str, git_token: Optional[str] = None, ssh_key: Optional[str] = None) -> dict:
      # Runs git ls-remote to check accessibility.
      # Returns {"accessible": bool, "requires_auth": bool, "provider": str, "error": str}
  ```
- **Authenticated Cloning**:
  ```python
  async def clone_private_repo(sandbox_id: str, repo_url: str, git_token: Optional[str] = None, ssh_key: Optional[str] = None) -> Tuple[bool, str]:
      # Writes ssh_key to a temporary 0600 file if provided
      # Rewrites HTTPS URL with token if token is provided
      # Runs git clone inside the sandbox with GIT_SSH_COMMAND if using SSH
      # Cleans up temporary credential files in a finally block
  ```

---

### 2. Backend Integration

#### [MODIFY] [models.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/scan_repository/models.py)
- Extend `RepoScanRequest` to include optional fields:
  - `git_token: Optional[str] = None`
  - `ssh_key: Optional[str] = None`
- Define `RepoScanPrecheckRequest` and `RepoScanPrecheckResponse` models for the auto-detection endpoint.

#### [MODIFY] [scan_repository.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/scan_repository/scan_repository.py)
- Add a new endpoint `POST /v1/repo-scan/precheck` that calls `check_repo_access` from `private_clone.py`.
- Update `submit_repo_scan` API route to accept optional `git_token` and `ssh_key` and pass them to the RabbitMQ queue payload or background task.
- Update `_run_scan_pipeline` to accept `git_token` and `ssh_key`, and forward them to the validation step and cloning step.

#### [MODIFY] [github_validator.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/scan_repository/github_validator.py)
- Integrate with `check_repo_access` from `private_clone.py`. If a token or key is provided (or if the URL matches GitLab/Bitbucket), bypass the old GitHub REST API check and use the `git ls-remote` validation path.

#### [MODIFY] [sandbox_provisioner.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/scan_repository/sandbox_provisioner.py)
- Update `clone_repo` to accept optional `git_token` and `ssh_key`.
- Delegate the cloning logic to `clone_private_repo` in `private_clone.py`.

#### [MODIFY] [consumer.py](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/apiServer/fastapi/core/queue/consumer.py)
- Extract `git_token` and `ssh_key` from the incoming RabbitMQ message body and pass them into `_run_scan_pipeline`.

---

### 3. Frontend: UI Integration

#### [MODIFY] [RepoScanner.tsx](file:///home/berrybytes/Desktop/Kamal/01-Sandbox/z1sandbox-website/src/pages/RepoScanner.tsx)
- Expand the URL validation pattern to match GitHub, GitLab, and Bitbucket (HTTPS & SSH).
- Add state variables:
  - `requiresAuth: boolean` (indicates if the repo is private and needs credentials)
  - `authMethod: "token" | "ssh"`
  - `gitToken: string`
  - `sshKey: string`
  - `isValidating: boolean` (loading state for precheck)
- Trigger a precheck on input blur:
  - Call `/v1/repo-scan/precheck` with the entered URL.
  - If `requires_auth` is true, automatically show the credential management fields in the UI.
- Update `handleScan` to send `git_token` and `ssh_key` in the request body if they are filled in.

---

## Verification Plan

### Automated Tests
- Write test scripts in `apiServer/fastapi/tests` testing:
  - Precheck endpoint behavior for public/private/invalid repositories.
  - Verification of Git cloning with token authentication and SSH authentication.
  - Ensure secrets are sanitized in the output.

### Manual Verification
- Test URL input in the UI with:
  1. A public GitHub repository (should scan directly without asking for credentials).
  2. A private GitHub/GitLab repository URL (should automatically expand credential input).
- Validate that the scan completes successfully with credentials and cleans up properly.
- Verify that credentials are not exposed in container logs.
