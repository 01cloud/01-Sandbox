# GitHub Repository Scanner

Deep-scan public GitHub repositories for language distribution and vulnerabilities using accurate language-detection tools (`github-linguist`, `tokei`, `enry`) and dedicated static analysis scanners.

## Backend Architecture

The scanner is implemented as a modular FastAPI router inside the `scan_repository/` package, mirroring the patterns in Quick Scan and Bulk Scan.

- **`__init__.py`**: Router factory exports
- **`scan_repository.py`**: Holds HTTP and SSE endpoints
- **`github_validator.py`**: Validates URLs and checks repository public accessibility
- **`sandbox_provisioner.py`**: Handles provisioning and git clone inside the sandbox
- **`language_detector.py`**: Executes accurate detection tools (`linguist` → `tokei` → `enry`)
- **`file_scanner.py`**: Dispatches static analysis scanners (`bandit`, `pylint`, `eslint`, `go vet`, `staticcheck`, `rubocop`, `pmd`) per detected language
- **`sse_manager.py`**: In-memory status reporting manager with asyncio queues

## Sandbox Environment

All language detection and static analysis tools are built directly into the base image (`Dockerfile_base`) at build time. Provisioned sandboxes have zero runtime tool installation cost, ensuring extremely fast start times.

## API Endpoints

All endpoints are protected under the same API key authorization gate (`Depends(validate_token)`):

1. **Submit Repository Scan Job**
   - `POST /v1/repo-scan`
   - Body: `{"repo_url": "https://github.com/owner/repo"}`
   - Returns a `job_id` and endpoints for polling.

2. **Server-Sent Events Status Stream**
   - `GET /v1/repo-scan/{job_id}/status`
   - Streams live progress step updates: `QUEUED` → `PROVISIONING` → `CLONING` → `DETECTING` → `SCANNING` → `DONE`/`ERROR`.

3. **Retrieve Scan Result**
   - `GET /v1/repo-scan/{job_id}/result`
   - Returns aggregated languages, lines of code, and findings.
