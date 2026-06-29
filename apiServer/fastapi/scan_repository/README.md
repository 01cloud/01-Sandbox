# GitHub Repository Scanner — Technical Reference

Deep-scan any public GitHub repository for language composition, lines-of-code, and static security findings. The scanner is a self-contained FastAPI sub-package (`scan_repository/`) that wires into the main API server via the `get_repo_scan_router()` factory function, mirroring the pattern used by `health.py` and bulk-scan.

---

## Table of Contents

1. [Package Layout](#package-layout)
2. [End-to-End Request Flow](#end-to-end-request-flow)
3. [API Endpoints](#api-endpoints)
4. [Authentication](#authentication)
5. [Pipeline Deep Dive](#pipeline-deep-dive)
   - [Step 1 — GitHub Validation](#step-1--github-validation)
   - [Step 2 — Sandbox Provisioning](#step-2--sandbox-provisioning)
   - [Step 3 — Repository Cloning](#step-3--repository-cloning)
   - [Step 4 — Language Detection](#step-4--language-detection)
   - [Step 5 — Per-Language Security Scanning](#step-5--per-language-security-scanning)
   - [Step 6 — Result Aggregation](#step-6--result-aggregation)
6. [Real-Time Progress via SSE](#real-time-progress-via-sse)
7. [Multi-Pod Cluster Architecture (Redis)](#multi-pod-cluster-architecture-redis)
8. [Data Models](#data-models)
9. [Error Handling & Timeouts](#error-handling--timeouts)
10. [Environment Variables](#environment-variables)

---

## Package Layout

```
scan_repository/
├── __init__.py              # Re-exports get_repo_scan_router()
├── scan_repository.py       # HTTP + SSE endpoints and pipeline orchestrator
├── github_validator.py      # URL format check + GitHub REST API accessibility
├── sandbox_provisioner.py   # Local temp-dir sandbox lifecycle (provision, clone, destroy)
├── language_detector.py     # Language detection: tokei → enry → extension walk
├── file_scanner.py          # Per-language static analysis via POST /scan-jobs
├── sse_manager.py           # In-memory SSE event queues + job registry
├── models.py                # Pydantic request/response/event models
└── README.md                # This file
```

---

## End-to-End Request Flow

```
Browser / Client
     │
     │  POST /v1/repo-scan  { "repo_url": "https://github.com/owner/repo" }
     │  Authorization: Bearer <api_key>
     ▼
┌─────────────────────────────────────────────────────────┐
│  FastAPI API Server (sandbox-api pod)                    │
│                                                          │
│  1. validate_token() → authenticate request              │
│  2. validate_github_repo() → check URL + public access   │
│  3. sse_manager.create_job(job_id)                       │
│  4. background_tasks.add_task(_run_scan_pipeline)        │
│  5. Return immediately → { job_id, status_url, result_url}│
└─────────────────────────────────────────────────────────┘
     │
     │  (async background task begins immediately)
     ▼
┌─────────────────────────────────────────────────────────┐
│  _run_scan_pipeline()  [BackgroundTask]                  │
│                                                          │
│  PROVISIONING → provision_sandbox()                      │
│       ↓  creates /tmp/reposcanner_<random>/              │
│  CLONING     → clone_repo()                              │
│       ↓  git clone --depth=1 <repo>.git /tmp/.../repo/   │
│  DETECTING   → detect_languages()                        │
│       ↓  tokei → enry → extension walk                   │
│  SCANNING    → scan_language() × N languages             │
│       ↓  POST /scan-jobs → code-interpreter sandbox      │
│  DONE        → emit final result with all findings       │
└─────────────────────────────────────────────────────────┘
     │
     │  Each step pushes to:
     │    1. sse_manager (local asyncio.Queue per job)
     │    2. Redis Pub/Sub  repo_scan:chan:{job_id}
     ▼
Browser                    Other API pod (replica)
  GET /status  ←───SSE─── sse_manager.stream()
  (EventSource)            OR Redis Pub/Sub subscriber
```

---

## API Endpoints

All endpoints are **authenticated** via `Depends(validate_token)`.

### `POST /v1/repo-scan`

Submit a public GitHub repository for scanning.

**Request body:**
```json
{ "repo_url": "https://github.com/owner/repo" }
```

**Response `200 OK`:**
```json
{
  "job_id": "e6bde193-b3df-41b3-a3cc-93eac428c6d0",
  "status": "QUEUED",
  "status_url": "/v1/repo-scan/e6bde193-b3df-41b3-a3cc-93eac428c6d0/status",
  "result_url": "/v1/repo-scan/e6bde193-b3df-41b3-a3cc-93eac428c6d0/result"
}
```

The pipeline starts **immediately as a background task**. The HTTP response returns before the first scan step begins.

---

### `GET /v1/repo-scan/{job_id}/status`

Stream live scan progress as Server-Sent Events.

**Authentication note:** EventSource in the browser cannot set `Authorization` headers. Pass your API key as a query parameter instead:
```
GET /v1/repo-scan/{job_id}/status?token=<api_key>
```
The `validate_token` middleware accepts credentials from either `Authorization: Bearer <token>` header **or** `?token=<value>` query parameter.

**Stream format** — each event is a JSON-encoded `ScanEvent`:
```
data: {"job_id":"...","step":"CLONING","message":"Cloning owner/repo with --depth=1...","progress":25}

data: {"job_id":"...","step":"DETECTING","message":"Detecting languages (tokei → enry → walk)...","progress":45}

data: {"job_id":"...","step":"DONE","message":"Scan complete — 4 language(s), 12 finding(s) in 23.4s","progress":100,"detail":{...full RepoScanResult...}}
```

The stream closes automatically after `DONE` or `ERROR`. A `: ping` keep-alive comment is emitted every 30 seconds to prevent proxy timeouts.

---

### `GET /v1/repo-scan/{job_id}/result`

Retrieve the final aggregated scan result after the job reaches `DONE` or `ERROR`.

**Response `200 OK`:**
```json
{
  "job_id": "e6bde193-...",
  "repo_url": "https://github.com/owner/repo",
  "owner": "owner",
  "repo": "repo",
  "status": "DONE",
  "detection_tool": "tokei",
  "total_files": 241,
  "total_findings": 12,
  "scan_duration_seconds": 23.4,
  "languages": {
    "Python": {
      "language": "Python",
      "file_count": 45,
      "lines_of_code": 3820,
      "percentage": 18.67,
      "findings": [
        {
          "severity": "HIGH",
          "file": "src/auth/tokens.py",
          "line": 42,
          "issue": "Use of MD5 for hashing — not collision-resistant",
          "tool": "bandit",
          "remediation": "Use hashlib.sha256 or bcrypt"
        }
      ]
    }
  }
}
```

**Response `404 Not Found`:** Returned if the result is not yet ready (still scanning) or the `job_id` is unknown.

---

## Authentication

All three endpoints use the same `validate_token` FastAPI dependency defined in `main.py`. Token resolution order:

1. `Authorization: Bearer <token>` header (all methods)
2. `?token=<value>` query parameter (SSE/EventSource-specific)
3. Cookie fallback (management UI only — disabled for execution routes)

Tokens are RS256-signed JWTs validated against the server's public JWKS. The JWT payload carries the `jti` (key ID) which is cross-referenced against the PostgreSQL `api_keys` table to confirm the key is active and not rate-limited.

---

## Pipeline Deep Dive

### Step 1 — GitHub Validation

**Module:** `github_validator.py`
**Progress:** pre-job (synchronous, before background task)

```python
owner, repo = await validate_github_repo(req.repo_url)
```

Two checks happen synchronously **before** the job is created:

1. **Regex format check** — validates the URL matches `https://github.com/{owner}/{repo}` (with optional `.git` suffix and trailing slash).
2. **GitHub REST API call** — `GET https://api.github.com/repos/{owner}/{repo}` confirms the repository exists and is publicly accessible. A `private: true` field in the response triggers a `400` rejection even if the repo exists.

Set `GITHUB_TOKEN` in environment to raise the GitHub API rate limit from 60 to 5,000 requests/hour.

| Condition | HTTP status returned |
|---|---|
| Invalid URL format | `400 Bad Request` |
| Repo not found / private | `404 Not Found` |
| GitHub API rate limit hit | `403 Forbidden` |
| GitHub API unreachable | `502 Bad Gateway` |

---

### Step 2 — Sandbox Provisioning

**Module:** `sandbox_provisioner.py`
**SSE step:** `PROVISIONING` (progress: 10%)
**Timeout:** 60 seconds

```python
sandbox_id = await provision_sandbox(backend)
# → returns e.g. "/tmp/reposcanner_gup2mn2a"
```

A unique temporary directory is created via `tempfile.mkdtemp()` using the prefix `reposcanner_`. This directory serves as the isolated workspace for the entire scan job. The returned path string is used as `sandbox_id` throughout all subsequent steps.

> **Why local temp directory?** The OpenSandbox server API (`opensandbox-server`) exposes no `/exec` endpoint. All sandbox API surfaces are lifecycle-only (`POST /sandboxes`, `DELETE /sandboxes/{id}`, etc.). Therefore, repository cloning and file reading happen locally on the API pod.

---

### Step 3 — Repository Cloning

**Module:** `sandbox_provisioner.py → clone_repo()`
**SSE step:** `CLONING` (progress: 25%)
**Timeout:** 180 seconds

```python
success, error = await clone_repo(sandbox_id, clone_url)
# → runs: git clone --depth=1 https://github.com/owner/repo.git /tmp/reposcanner_.../repo/
```

`clone_repo()` internally calls `exec_in_sandbox()` which spawns a subprocess via `asyncio.create_subprocess_exec`. The subprocess runs:
```bash
git clone --depth=1 <repo_url>.git <sandbox_id>/repo/
```

- `--depth=1` fetches only the latest commit, keeping clone time and disk usage minimal.
- The `.git` suffix is appended automatically if not already present.
- `git` must be available in the API pod (added to `Dockerfile` runtime stage via `apt-get install -y git ca-certificates`).

On failure, the full stderr output is captured and surfaced in the `ERROR` SSE event.

---

### Step 4 — Language Detection

**Module:** `language_detector.py`
**SSE step:** `DETECTING` (progress: 45%)
**Timeout:** 60 seconds

```python
lang_map, detection_tool = await detect_languages(sandbox_id)
# → e.g. {"Python": ["/tmp/.../repo/src/main.py", ...], "Go": [...]}
```

Language detection runs a **priority chain** against the locally cloned repo directory:

| Priority | Tool | Method | Requires |
|---|---|---|---|
| 1 | **Tokei** | Subprocess, JSON output (`tokei <repo> --output json`) | `tokei` binary on path |
| 2 | **Enry** | Subprocess, text output + extension fallback | `enry` binary on path |
| 3 | **Extension Walk** | Pure Python `os.walk()` — always succeeds | None (built-in fallback) |

#### 1. Tokei (Priority 1)
* **Description**: A fast, compile-optimized tool written in Rust designed to count code lines, comments, and files in a codebase.
* **Orchestration**: Runs via a subprocess call: `tokei <repo_path> --output json`.
* **Output Handling**: Since it outputs clean, structured JSON detailing exact file paths mapped to languages, it allows the pipeline to directly extract the file lists without any additional directory traversal.

#### 2. Enry (Priority 2)
* **Description**: A lightweight Go implementation of GitHub's official Ruby-based `Linguist` library, providing the same language classification heuristics without requiring a heavy Ruby runtime.
* **Orchestration**: Runs via a subprocess call: `enry <repo_path>`.
* **Output Handling**: Since the default output format provides detected language names only, the pipeline uses Enry's stdout as a filter list, then runs a local extension walk to map the files belonging to those specific languages.

#### 3. Extension Walk Fallback (Priority 3)
* **Description**: A pure-Python file walker (`_local_walk()`) that acts as a failsafe when neither `tokei` nor `enry` is installed on the host container.
* **Orchestration**: Automatically crawls the directory structure using standard library `os.walk()`.
* **Output Handling**:
  * Automatically ignores common non-code folders like `.git`, `node_modules`, `.venv`, `__pycache__`, `target`, `dist`, and `build`.
  * Matches files against a predefined dictionary of over 20 extensions (`.py` → `Python`, `.js`/`.jsx` → `JavaScript`, `.ts`/`.tsx` → `TypeScript`, `.go` → `Go`, `.rs` → `Rust`, etc.).
  * This guarantees that language detection succeeds and produces accurate lists under any environmental configuration.

The chosen `DetectionTool` enum value (`tokei`, `enry`, or `unknown`) is saved in the `RepoScanResult` object for full pipeline observability.

---

### Step 5 — Per-Language Security Scanning

**Module:** `file_scanner.py → scan_language()`
**SSE step:** `SCANNING` (progress: 60–90%, interpolated per language)
**Timeout:** 180 seconds per language

```python
result = await scan_language(sandbox_id, language, files, percentage)
```

For each detected language, `scan_language()` does:

#### 5a. Lines-of-Code Count (local, fast)
Non-blank lines are counted directly from the local files without network overhead:
```python
lines_of_code = _count_loc(files_capped)
```

#### 5b. Skip non-code languages
Languages in `LOC_ONLY_LANGS` (`yaml`, `json`, `markdown`, `toml`, `xml`, etc.) receive only a LoC count. No security scan is submitted for them.

#### 5c. Read and submit files to `POST /scan-jobs`
Source files are read from disk and packed into a `{relative_path: content}` dict. Files larger than **200 KB** are silently skipped. Up to **100 files per language** are submitted (hard cap).

```python
files_dict = _read_files_as_dict(files_capped, repo_root)
report = await _submit_scan_job(files_dict, tools=tool_hints)
```

The submission hits the internal `POST /api/v1/01sbx/scan-jobs` endpoint of the `opensandbox-server`. This endpoint provisions a real **code-interpreter sandbox** (the `01community/01sandbox-codeinterpreter` container image) which has all scanning tools pre-installed at image build time — bandit, semgrep, eslint, gosec, shellcheck, yamllint, etc.

#### Tool hints by language

| Language | Tool hints sent |
|---|---|
| Python | `["bandit", "semgrep"]` |
| JavaScript / TypeScript | `["semgrep"]` |
| Go | `["gosec", "semgrep"]` |
| Java | `["semgrep"]` |
| Ruby | `["semgrep"]` |
| Shell / Bash | `["shellcheck", "semgrep"]` |
| YAML | `["yamllint", "semgrep"]` |

#### 5d. Parse findings from the scan report

The scan-jobs endpoint returns a JSON report. `_parse_scan_report()` extracts the `findings` array and maps each entry to a `FindingItem`:

```python
FindingItem(
    severity="HIGH",        # CRITICAL | HIGH | MEDIUM | LOW | INFO
    file="src/auth.py",
    line=42,                # Optional[int] — sanitized from "N/A" strings
    issue="Use of MD5...",
    tool="bandit",
    remediation="Use sha256",
)
```

Line numbers returned as non-integer strings (e.g. `"N/A"`, `"none"`) are **silently converted to `None`** to prevent Pydantic validation errors.

---

### Step 6 — Result Aggregation

**SSE step:** `DONE` (progress: 100%)

After all languages are scanned, the pipeline builds a `RepoScanResult`:

```python
final_result = RepoScanResult(
    job_id=job_id,
    repo_url=repo_url,
    owner=owner,
    repo=repo,
    status=ScanStep.DONE,
    languages=language_results,         # Dict[str, LanguageScanResult]
    detection_tool=detection_tool,
    total_files=total_files,
    total_findings=total_findings,
    scan_duration_seconds=round(duration, 2),
)
```

The result is:
1. Stored in `sse_manager` (local pod memory, accessible via `GET /result`)
2. Stored in Redis under `repo_scan:result:{job_id}` (TTL: 1 hour, cluster-wide)
3. Emitted as the final SSE event's `detail` payload so the frontend receives the full result in-stream without needing a separate `GET /result` call.

---

## Real-Time Progress via SSE

**Module:** `sse_manager.py`

`SSEManager` is a module-level singleton managing per-job `asyncio.Queue` instances.

```
sse_manager (SSEManager)
├── _jobs: Dict[job_id → JobRecord]
│   ├── queue: asyncio.Queue[Optional[ScanEvent]]
│   ├── result: Optional[RepoScanResult]
│   ├── step: ScanStep          ← current step
│   └── finished_at: float      ← monotonic timestamp on terminal event
```

**Event lifecycle:**

```
pipeline pushes event         stream() drains queue
     │                               │
     ▼                               ▼
job.queue.put(ScanEvent)  →  yield f"data: {event.json()}\n\n"

terminal event (DONE/ERROR):
  1. ScanEvent pushed to queue
  2. None sentinel pushed after it
  3. stream() generator breaks on None or terminal step
```

**Keep-alive:** If no event arrives within 30 seconds, `stream()` yields `: ping\n\n`. This prevents reverse proxies (nginx, Cloudflare) from closing idle SSE connections.

**TTL cleanup:** Jobs are removed from `_jobs` after a 10-minute TTL once they reach a terminal state. `cleanup_expired()` is called in the pipeline's `finally` block.

---

## Multi-Pod Cluster Architecture (Redis)

When the API server runs as multiple replicas (HPA), any pod can receive the `GET /status` or `GET /result` request, even if a **different pod** started the scan pipeline.

```
Pod A (ran the scan)            Pod B (client connected here)
──────────────────────          ──────────────────────────────
pipeline pushes events          GET /v1/repo-scan/{id}/status
  → sse_manager (local)         │
  → Redis PUBLISH               │ job = sse_manager.get_job(id)
      repo_scan:chan:{id}        │   → None  (different pod!)
  → Redis SET                   │
      repo_scan:status:{id}     │ fallback: Redis SUBSCRIBE
      repo_scan:result:{id}     │   repo_scan:chan:{id}
                                │   → yield events as they arrive
```

**Redis key schema:**

| Key | Value | TTL |
|---|---|---|
| `repo_scan:status:{job_id}` | `"CLONING"` / `"DONE"` / ... | 1 hour |
| `repo_scan:result:{job_id}` | JSON-serialized `RepoScanResult` | 1 hour |
| `repo_scan:chan:{job_id}` | Pub/Sub channel (ephemeral) | — |

If a replica receives a `GET /result` request for a job that is already in terminal state in Redis, it returns the cached result immediately without hitting the originating pod.

---

## Data Models

**`models.py`** defines all Pydantic schemas:

### Request
```python
class RepoScanRequest(BaseModel):
    repo_url: str  # validated URL, whitespace stripped
```

### Pipeline Steps
```python
class ScanStep(str, Enum):
    QUEUED       = "QUEUED"
    PROVISIONING = "PROVISIONING"
    CLONING      = "CLONING"
    DETECTING    = "DETECTING"
    SCANNING     = "SCANNING"
    DONE         = "DONE"
    ERROR        = "ERROR"
```

### SSE Event
```python
class ScanEvent(BaseModel):
    job_id:   str
    step:     ScanStep
    message:  str
    progress: int           # 0–100
    detail:   Optional[dict]  # populated on DONE with full RepoScanResult
```

### Finding
```python
class FindingItem(BaseModel):
    severity:    str                    # CRITICAL | HIGH | MEDIUM | LOW | INFO
    file:        str                    # relative path within the repo
    line:        Optional[Union[int, str]]  # None if not applicable
    issue:       str
    tool:        str                    # bandit | semgrep | gosec | ...
    remediation: Optional[str]
```

### Language Result
```python
class LanguageScanResult(BaseModel):
    language:      str
    file_count:    int
    lines_of_code: int
    percentage:    float       # share of total repo files
    findings:      List[FindingItem]
```

### Final Result
```python
class RepoScanResult(BaseModel):
    job_id:                 str
    repo_url:               str
    owner:                  str
    repo:                   str
    status:                 ScanStep
    languages:              Dict[str, LanguageScanResult]
    detection_tool:         DetectionTool
    total_files:            int
    total_findings:         int
    scan_duration_seconds:  float
    error:                  Optional[str]
```

---

## Error Handling & Timeouts

| Stage | Timeout | On Failure |
|---|---|---|
| GitHub API validation | 10 s (httpx) | `HTTPException` raised synchronously |
| Sandbox provisioning | 60 s | `RuntimeError` → `ERROR` SSE event |
| `git clone` | 180 s | `RuntimeError` with stderr → `ERROR` SSE event |
| Language detection | 60 s | Falls through to extension walk (never fails) |
| Per-language scan | 180 s | Skipped, LoC-only result returned |
| Total pipeline | No global cap | Each step has its own `asyncio.wait_for()` |

All exceptions are caught in the pipeline's top-level `try/except`. On any unhandled exception:
1. `_store_error()` writes an `ERROR`-status `RepoScanResult` to both `sse_manager` and Redis.
2. An `ERROR` SSE event is pushed to all active subscribers.
3. The sandbox temp directory is **always** cleaned up in the `finally` block.

---

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | _(none)_ | Raises GitHub API limit to 5,000 req/hr |
| `OPENSANDBOX_ROUTE_PREFIX` | `/api/v1/01sbx` | Prefix for the scan-jobs submission URL |
| `BACKEND_URL_OPENSANDBOX` | _(required)_ | Base URL of the opensandbox-server service |
| `SCAN_DATA_ROOT` | `/data` | PVC mount path (used by scan-jobs backend) |
| `REDIS_HOST` | _(none)_ | Enables Redis cluster broadcasting when set |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | _(none)_ | Redis password if auth is enabled |
