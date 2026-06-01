# GitHub Repository Scanner — Technical Workflow

## Overview

The GitHub Repository Scanner is a self-contained FastAPI sub-package (`scan_repository/`) that performs automated language detection and static security analysis on any public GitHub repository. It runs as an async background pipeline, streaming real-time progress to clients via Server-Sent Events (SSE), with Redis Pub/Sub for multi-pod cluster support.

---

## Package Layout

```
scan_repository/
├── __init__.py              # Re-exports get_repo_scan_router()
├── scan_repository.py       # HTTP endpoints + pipeline orchestrator
├── github_validator.py      # URL format check + GitHub REST API check
├── sandbox_provisioner.py   # Local temp-dir lifecycle (provision/clone/destroy)
├── language_detector.py     # Language detection: linguist → tokei → enry → walk
├── file_scanner.py          # Per-language static analysis via POST /scan-jobs
├── sse_manager.py           # In-memory SSE event queues + job registry
└── models.py                # Pydantic request/response/event models
```

---

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/v1/repo-scan` | Submit a repo URL, get back a `job_id` immediately |
| `GET` | `/v1/repo-scan/{job_id}/status` | SSE stream of live pipeline progress events |
| `GET` | `/v1/repo-scan/{job_id}/result` | Fetch the final aggregated scan result |

All endpoints require `Authorization: Bearer <token>`. The SSE endpoint also accepts `?token=<value>` because browser `EventSource` cannot set custom headers.

---

## Full End-to-End Workflow

```
Client
  │
  │  POST /v1/repo-scan  {"repo_url": "https://github.com/owner/repo"}
  ▼
FastAPI (sync, before background task)
  ├─ 1. validate_token()              ← JWT auth check
  ├─ 2. validate_github_repo()        ← URL regex + GitHub REST API
  ├─ 3. sse_manager.create_job()      ← allocate asyncio.Queue for SSE
  ├─ 4. background_tasks.add_task()   ← enqueue pipeline
  └─ 5. return {job_id, status_url, result_url}   ← immediate HTTP 200

Background (_run_scan_pipeline)
  ├─ PROVISIONING  → provision_sandbox()
  ├─ CLONING       → clone_repo()
  ├─ DETECTING     → detect_languages()
  ├─ SCANNING      → scan_language() × N languages
  └─ DONE          → emit RepoScanResult via SSE + store in Redis

Client (SSE)
  └─ GET /v1/repo-scan/{job_id}/status
       → streams ScanEvent JSON objects until DONE or ERROR
```

---

## Step-by-Step Pipeline Details

### Step 0 — Request Submission (`scan_repository.py`)

`POST /v1/repo-scan` is handled synchronously. Before the background task starts:

1. `validate_token()` dependency authenticates the caller via RS256 JWT.
2. `validate_github_repo(req.repo_url)` is awaited — this **blocks** the HTTP response until the GitHub check completes (see Step 1).
3. A UUIDv4 `job_id` is generated.
4. `sse_manager.create_job(job_id, repo_url)` registers an in-memory `JobRecord` with an `asyncio.Queue`.
5. A `QUEUED` event is pushed to both the local queue and Redis.
6. `background_tasks.add_task(_run_scan_pipeline, ...)` enqueues the full pipeline.
7. `RepoScanSubmitResponse` is returned immediately — the client does not wait for the scan.

```python
# Immediate HTTP response shape
{
  "job_id": "e6bde193-b3df-41b3-a3cc-93eac428c6d0",
  "status": "QUEUED",
  "status_url": "/v1/repo-scan/e6bde193-.../status",
  "result_url": "/v1/repo-scan/e6bde193-.../result"
}
```

---

### Step 1 — GitHub Validation (`github_validator.py`)

**Runs synchronously before the job is queued. Timeout: 10 s.**

Two sequential checks:

**1a. Regex format check**

```python
GITHUB_URL_PATTERN = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+?)(?:\.git)?/?$"
)
```

Accepts URLs with or without `.git` suffix and optional trailing slash. Extracts `(owner, repo)`.

**1b. GitHub REST API accessibility check**

```
GET https://api.github.com/repos/{owner}/{repo}
```

- Uses `GITHUB_TOKEN` env var if set → raises rate limit from 60 to 5,000 req/hr.
- Checks `private: true` in the response body — private repos are rejected even if accessible.

| Condition | HTTP status |
|-----------|-------------|
| Invalid URL format | `400` |
| Repo not found or private | `404` |
| GitHub API rate limit | `403` |
| GitHub API unreachable | `502` |

---

### Step 2 — Sandbox Provisioning (`sandbox_provisioner.py`)

**SSE step:** `PROVISIONING` — progress 10%
**Timeout:** 60 seconds

```python
sandbox_id = await provision_sandbox(backend)
# Returns e.g. "/tmp/reposcanner_gup2mn2a"
```

`tempfile.mkdtemp(prefix="reposcanner_")` is called via `loop.run_in_executor()` to avoid blocking the event loop. The returned absolute path serves as the `sandbox_id` for all subsequent steps.

> **Design note:** The OpenSandbox server has no `/exec` endpoint — only sandbox lifecycle APIs. Therefore all file operations (cloning, reading, language detection) happen locally on the API pod. The `backend` parameter is accepted for signature compatibility but not used.

---

### Step 3 — Repository Cloning (`sandbox_provisioner.py → clone_repo()`)

**SSE step:** `CLONING` — progress 25%
**Timeout:** 180 seconds

```python
success, error = await clone_repo(sandbox_id, clone_url)
# Runs: git clone --depth=1 <repo>.git /tmp/reposcanner_.../repo/
```

Internally calls `exec_in_sandbox()`, which uses `asyncio.create_subprocess_exec`:

```python
command = ["git", "clone", "--depth=1", repo_url, target]
proc = await asyncio.create_subprocess_exec(*command, ...)
stdout, stderr = await proc.communicate()
```

Key details:
- `.git` suffix is appended automatically if missing.
- `--depth=1` fetches only the latest commit — minimises clone time and disk usage.
- Clone target is `{sandbox_id}/repo/` (the `REPO_DIR` constant).
- On failure, stderr is captured and emitted in the `ERROR` SSE event.
- After success, `os.walk()` counts the total file count and logs it.

`exec_in_sandbox()` is a reusable helper that wraps any subprocess command with timeout handling, stderr logging (first 300 chars on non-zero exit), and `FileNotFoundError` detection for missing binaries.

---

### Step 4 — Language Detection (`language_detector.py`)

**SSE step:** `DETECTING` — progress 45%
**Timeout:** 60 seconds

```python
lang_map, detection_tool = await detect_languages(sandbox_id)
# Returns e.g.:
# ({"Python": ["/tmp/.../repo/src/main.py", ...], "Go": [...]}, DetectionTool.TOKEI)
```

Detection runs a **waterfall priority chain**. Each tool is tried in order; the first to return a non-empty result wins.

#### Priority 1 — GitHub Linguist

```bash
linguist <repo_path> --breakdown --json
```

- Gold-standard language classifier (the same tool GitHub itself uses).
- JSON output includes per-file breakdown: `{language: {files: [...], percentage: N}}`.
- `_parse_linguist()` resolves relative paths to absolute, validates `os.path.isfile()`.
- If the binary is missing or returns exit code != 0, falls through to tokei.

#### Priority 2 — Tokei

```bash
tokei <repo_path> --output json
```

- Rust-based, extremely fast line-counter.
- JSON output: `{language: {reports: [{name: "/abs/path/file.py", ...}]}}`.
- `_parse_tokei()` extracts `reports[].name` as absolute file paths.
- Produces the most accurate file-level breakdown after linguist.

#### Priority 3 — Enry

```bash
enry <repo_path>
```

- Lightweight Go port of GitHub Linguist (no Ruby runtime required).
- Output is **language names only** — no file paths.
- `_parse_enry()` builds `{lang: []}` name list.
- Immediately followed by `_local_walk()` to populate file lists for each detected language.

#### Priority 4 — Extension Walk (always available)

```python
lang_map = _local_walk(repo_path)
```

Pure-Python `os.walk()` fallback — never requires external binaries:

- Skips: `.git`, `node_modules`, `.venv`, `venv`, `__pycache__`, `vendor`, `target`, `.idea`, `.vscode`, `dist`, `build`
- Maps 20+ extensions: `.py`→Python, `.js`/`.jsx`→JavaScript, `.ts`/`.tsx`→TypeScript, `.go`→Go, `.rs`→Rust, `.rb`→Ruby, `.sh`/`.bash`→Shell, `.yaml`/`.yml`→YAML, etc.

The chosen tool is recorded in `RepoScanResult.detection_tool` as a `DetectionTool` enum: `linguist | tokei | enry | unknown`.

---

### Step 5 — Per-Language Security Scanning (`file_scanner.py`)

**SSE step:** `SCANNING` — progress 60–90% (interpolated across languages)
**Timeout:** 180 seconds per language

For each language in `lang_map`, `scan_language()` is called:

```python
result = await scan_language(sandbox_id, language, files, percentage)
```

#### 5a. Lines-of-Code Count (local, no network)

```python
lines_of_code = _count_loc(files_capped)
```

Counts non-blank lines across all files using `open()`. Fast, local-only, no subprocess.

#### 5b. LoC-Only Languages (skip security scan)

Languages in `LOC_ONLY_LANGS` receive only a file count and LoC — no scan submitted:

```python
LOC_ONLY_LANGS = {"json", "markdown", "text", "toml", "xml", "ini", "dockerfile"}
```

Note: `yaml`/`yml` are **not** in this set — they are delegated to the YAML splitter.

#### 5c. File Reading

```python
files_dict, skipped = _read_files_as_dict(files_capped, repo_root)
```

- Cap: max **100 files per language** (`FILE_CAP = 100`)
- Files > **200 KB** are silently skipped
- Binary/unreadable files are skipped
- Output: `{"relative/path/file.py": "<file contents>"}` dict

#### 5d. YAML Splitting (special case)

YAML files are routed to `scan_yaml_files()` before the generic scan path:

```python
if lang_lower in ("yaml", "yml"):
    return await scan_yaml_files(sandbox_id, files_capped, percentage)
```

Each YAML file is classified by reading its first 4 KB:

```python
def _is_k8s_yaml(filepath):
    # Returns True if file contains both "apiVersion:" and "kind:" at line start
```

- **Plain YAML** → submitted to scan-jobs with `tools=["yamllint"]` → section: `"YAML"`
- **Kubernetes manifests** → submitted with `tools=["kubelinter", "kubescore", "kubeconform"]` → section: `"Kubernetes YAML"`

Returns a `(yaml_result, k8s_result)` tuple. `k8s_result` is `None` if no manifests found.

#### 5e. Tool Hint Selection

For each language, a `tool_hints` list tells the scan-jobs orchestrator which tools to prioritise:

| Language | Tools submitted |
|----------|----------------|
| Python | `bandit`, `semgrep` |
| JavaScript / TypeScript | `semgrep` |
| Go | `gosec`, `semgrep` |
| Java | `semgrep` |
| Ruby | `semgrep` |
| Shell / Bash | `shellcheck`, `semgrep` |
| Others | `None` (auto-select) |

#### 5f. Submission to POST /scan-jobs

```python
report = await _submit_scan_job(files_dict, tools=tool_hints)
```

```python
url = f"{opensandbox_base_url()}{opensandbox_route_prefix()}/scan-jobs"
payload = {"files": files_dict, "tools": tool_hints}
resp = await httpx_client.post(url, json=payload, headers=opensandbox_headers())
```

- **Timeout:** 300 seconds (httpx client-level)
- The `opensandbox-server` provisions a real **code-interpreter sandbox** pod (image: `01community/01sandbox-codeinterpreter`) with all tools pre-installed (bandit, semgrep, gosec, shellcheck, yamllint, kubelinter, etc.)
- Response shape: `{"report": {"findings": [...], "scans": {tool: {...}}}}`

#### 5g. Finding Parsing

```python
findings = _parse_scan_report(report, lang_lower)
```

Each entry in `report["findings"]` is mapped to a `FindingItem`:

```python
FindingItem(
    severity="HIGH",          # normalised to CRITICAL|HIGH|MEDIUM|LOW|INFO
    file="src/auth.py",       # relative path
    line=42,                  # None if non-integer (e.g. "N/A")
    issue="Use of MD5...",
    tool="bandit",
    remediation="Use sha256",
)
```

#### 5h. Cross-Language Finding Filter

```python
findings = _filter_findings_to_submitted_files(raw_findings, submitted_paths, language)
```

The scan-jobs pod runs tools across the entire workspace. This filter drops findings for files not in the submitted set (preventing cross-language contamination). Path normalisation strips `/workspace/`, `workspace/`, and `./` prefixes before comparison.

---

### Step 5.5 — Finding Redistribution & Deduplication (`scan_repository.py`)

After all language scans complete, raw findings from all `LanguageScanResult.raw_findings` lists are gathered, deduplicated, and redistributed:

**Deduplication key:**
```python
key = (severity, file_normalized, line, issue, tool)
```

**Redistribution** — each finding is re-assigned to the correct language section via `get_target_language(file_path, tool_name)`:

| File extension | Assigned to |
|---------------|-------------|
| `.py` | Python |
| `.go`, `go.mod`, `go.sum` | Go |
| `.js`, `.jsx` | JavaScript (or TypeScript if JS absent) |
| `.ts`, `.tsx` | TypeScript |
| `.sh`, `.bash` | Shell |
| `.yaml`/`.yml` (k8s tool) | Kubernetes YAML |
| `.yaml`/`.yml` (other) | YAML |
| `.rb` | Ruby |
| `.java` | Java |
| unmatched | Secrets & Infrastructure |

Findings from tools like `gitleaks` and `trivy` (which scan the entire repo) may be assigned to a synthetic `"Secrets & Infrastructure"` section if they cannot be attributed to a specific language file.

---

### Step 6 — Result Aggregation & Completion (`scan_repository.py`)

**SSE step:** `DONE` — progress 100%

```python
final_result = RepoScanResult(
    job_id=job_id,
    repo_url=repo_url,
    owner=owner,
    repo=repo,
    status=ScanStep.DONE,
    languages=language_results,
    detection_tool=detection_tool,
    total_files=total_files,
    total_findings=total_findings,
    scan_duration_seconds=round(duration, 2),
)
```

The result is stored in three places simultaneously:

1. **`sse_manager.set_result(job_id, final_result)`** — local pod memory (serves `GET /result` on same pod)
2. **`Redis SET repo_scan:result:{job_id}`** — JSON serialised, TTL 1 hour (cluster-wide access)
3. **Final SSE DONE event `detail` payload** — full result embedded in the stream so clients do not need a separate `GET /result` call

---

## Real-Time Progress (SSE Architecture)

### `sse_manager.py` — SSEManager Singleton

```
SSEManager._jobs: Dict[job_id, JobRecord]
                            │
                  ┌─────────┴──────────┐
                  │      JobRecord      │
                  │  queue: asyncio.Queue[ScanEvent | None]
                  │  result: RepoScanResult | None
                  │  step: ScanStep
                  │  finished_at: float | None
                  └────────────────────┘
```

**Push flow (pipeline side):**
```python
await sse_manager.push(job_id, ScanStep.CLONING, "Cloning...", 25)
# → puts ScanEvent into job.queue
# → if DONE/ERROR: also puts None sentinel
```

**Stream flow (HTTP handler side):**
```python
async for chunk in sse_manager.stream(job_id):
    # yields: "data: {ScanEvent JSON}\n\n"
    # keep-alive: ": ping\n\n" every 30s if queue is empty
    # closes on None sentinel or terminal step
```

**TTL cleanup:** Jobs are purged 10 minutes after reaching `DONE` or `ERROR`. `cleanup_expired()` is called in the pipeline `finally` block.

### Progress Map

| SSE Step | Progress % | Description |
|----------|-----------|-------------|
| `QUEUED` | 5% | Job registered, pipeline not started |
| `PROVISIONING` | 10% | Creating temp directory |
| `CLONING` | 25% | git clone --depth=1 running |
| `DETECTING` | 45% | Language detection running |
| `SCANNING` | 60–90% | Per-language scans (interpolated) |
| `DONE` | 100% | All scans complete |
| `ERROR` | 0% | Pipeline failure |

---

## Multi-Pod Cluster Architecture (Redis)

When the API server runs as multiple replicas under Kubernetes HPA, any pod can receive `GET /status` or `GET /result` even if a **different pod** started the scan.

```
Pod A (ran the scan)              Pod B (client connects here)
─────────────────────             ──────────────────────────────
pipeline emits events             GET /v1/repo-scan/{id}/status
  → sse_manager (local)           │
  → Redis PUBLISH                 │  job = sse_manager.get_job(id)
      repo_scan:chan:{id}         │    → None (foreign job)
  → Redis SET                     │
      repo_scan:status:{id}       │  fallback: Redis SUBSCRIBE
      repo_scan:result:{id}       │    repo_scan:chan:{id}
                                  │    → yield events in real-time
```

**Redis key schema:**

| Key pattern | Value | TTL |
|-------------|-------|-----|
| `repo_scan:status:{job_id}` | `"CLONING"` / `"DONE"` / ... | 1 hour |
| `repo_scan:result:{job_id}` | JSON `RepoScanResult` | 1 hour |
| `repo_scan:chan:{job_id}` | Pub/Sub channel | ephemeral |

**Bootstrap optimisation:** If a client connects to Pod B after the scan already finished, `stream_redis_pubsub()` checks `repo_scan:status:{id}` first. If terminal, it loads `repo_scan:result:{id}` and emits a single bootstrap event — no subscription needed.

---

## Data Models (`models.py`)

### ScanStep (pipeline state machine)

```
QUEUED → PROVISIONING → CLONING → DETECTING → SCANNING → DONE
                                                        ↘ ERROR
```

### ScanEvent (SSE payload per event)

```python
class ScanEvent(BaseModel):
    job_id:   str
    step:     ScanStep
    message:  str
    progress: int           # 0–100
    detail:   Optional[dict]  # full RepoScanResult on DONE
```

### FindingItem

```python
class FindingItem(BaseModel):
    severity:    str              # CRITICAL | HIGH | MEDIUM | LOW | INFO
    file:        str              # relative path within repo
    line:        Optional[int]    # None if not applicable
    issue:       str
    tool:        str              # bandit | semgrep | gosec | shellcheck | ...
    remediation: Optional[str]
```

### LanguageScanResult

```python
class LanguageScanResult(BaseModel):
    language:      str
    file_count:    int
    lines_of_code: int
    percentage:    float          # share of total repo files (0–100)
    findings:      List[FindingItem]
    raw_findings:  List[FindingItem]  # excluded from JSON output
```

### RepoScanResult

```python
class RepoScanResult(BaseModel):
    job_id:                str
    repo_url:              str
    owner:                 str
    repo:                  str
    status:                ScanStep
    languages:             Dict[str, LanguageScanResult]
    detection_tool:        DetectionTool   # linguist|tokei|enry|unknown
    total_files:           int
    total_findings:        int
    scan_duration_seconds: float
    error:                 Optional[str]
```

---

## Error Handling & Timeouts

| Stage | Timeout | Failure behaviour |
|-------|---------|-------------------|
| GitHub API validation | 10 s | `HTTPException` raised synchronously (job never created) |
| Sandbox provisioning | 60 s | `RuntimeError` → `ERROR` SSE event |
| `git clone` | 180 s | `RuntimeError` with stderr → `ERROR` SSE event |
| Language detection | 60 s | Falls through waterfall; extension walk never fails |
| Per-language scan | 180 s | Returns LoC-only result; scan skipped |
| httpx `/scan-jobs` | 300 s | Returns `{}` report; 0 findings for language |

All exceptions in `_run_scan_pipeline` are caught by the top-level `try/except`:

1. `_store_error()` writes an `ERROR`-status `RepoScanResult` to `sse_manager` and Redis.
2. An `ERROR` SSE event is pushed to all active subscribers.
3. The `finally` block **always** calls `destroy_sandbox(sandbox_id)` — the temp directory is deleted regardless of success or failure.

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `GITHUB_TOKEN` | _(none)_ | Raises GitHub API rate limit to 5,000 req/hr |
| `BACKEND_URL_OPENSANDBOX` | _(required)_ | Base URL of the opensandbox-server service |
| `OPENSANDBOX_ROUTE_PREFIX` | `/api/v1/01sbx` | Route prefix for the `/scan-jobs` endpoint |
| `REDIS_HOST` | _(none)_ | Enables Redis cluster broadcasting when set |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | _(none)_ | Redis auth password |
| `SCAN_DATA_ROOT` | `/data` | PVC mount used by the scan-jobs backend |

---

## Module Interaction Diagram

```
scan_repository.py
  │
  ├──► github_validator.py     (Step 1: validate URL + GitHub API)
  │
  ├──► sandbox_provisioner.py  (Step 2: mkdtemp → sandbox_id)
  │       └── clone_repo()     (Step 3: git clone --depth=1)
  │           └── exec_in_sandbox() [asyncio subprocess wrapper]
  │
  ├──► language_detector.py    (Step 4: linguist → tokei → enry → walk)
  │       └── exec_in_sandbox() [reused from sandbox_provisioner]
  │
  ├──► file_scanner.py         (Step 5: per-language scan dispatch)
  │       ├── _count_loc()
  │       ├── _read_files_as_dict()
  │       ├── _submit_scan_job()    → POST /scan-jobs (opensandbox-server)
  │       ├── _parse_scan_report()
  │       ├── _filter_findings_to_submitted_files()
  │       └── scan_yaml_files()     (YAML: plain vs K8s split)
  │
  ├──► sse_manager.py          (push_event + stream to clients)
  │
  └──► Redis                   (Pub/Sub + status/result cache)
```

---

## Deep Dive — Bulk File Submission & Auto Sandbox Creation

### How Files Are Collected and Submitted in Bulk

The scanner does **not** scan files one by one. It groups all files for a given language into a single HTTP request. Here is the exact three-phase sequence using Python as the example:

#### Phase A — File Path Discovery (`language_detector.py`)

After `git clone`, the language detector produces a map of **absolute local file paths** grouped by language. No file content is read yet — only paths:

```python
lang_map = {
    "Python": [
        "/tmp/reposcanner_abc123/repo/src/auth/tokens.py",
        "/tmp/reposcanner_abc123/repo/src/api/views.py",
        "/tmp/reposcanner_abc123/repo/tests/test_auth.py",
        # ... every .py file found in the repo
    ],
    "Go":   [...],
    "YAML": [...],
}
```

#### Phase B — File Content Reading (`_read_files_as_dict`)

When `scan_language("Python", files, ...)` is called, every file path is opened and its **full source code** is read into memory as a string:

```python
def _read_files_as_dict(files, repo_root):
    result = {}
    for path in files[:100]:               # hard cap: max 100 files per language
        if os.path.getsize(path) > 200 * 1024:   # skip files > 200 KB
            continue
        with open(path, "r", errors="replace") as f:
            content = f.read()             # entire file content as a string
        rel_path = os.path.relpath(path, repo_root)   # strip sandbox prefix
        result[rel_path] = content
    return result
```

The resulting dict for Python looks like:

```python
files_dict = {
    "src/auth/tokens.py":  "import hashlib\ndef hash_password(p):\n    return hashlib.md5(p.encode()).hexdigest()\n...",
    "src/api/views.py":    "from flask import request\n@app.route('/login')\ndef login():\n    ...",
    "tests/test_auth.py":  "import unittest\nclass TestAuth(unittest.TestCase):\n    ...",
    # all python files, full code as strings
}
```

**All file contents for one language live in a single Python dict in memory on the API pod.**

#### Phase C — Single HTTP POST to `/scan-jobs`

The entire `files_dict` is serialised to JSON and sent in **one HTTP call**:

```python
payload = {
    "files": {
        "src/auth/tokens.py": "<full code>",
        "src/api/views.py":   "<full code>",
        "tests/test_auth.py": "<full code>",
        # ... all files
    },
    "tools": ["bandit", "semgrep"]    # tool hints for Python
}

resp = await httpx_client.post(
    "http://opensandbox-server/api/v1/01sbx/scan-jobs",
    json=payload,
    timeout=300.0
)
```

This means for a repo with 4 languages there are **4 total `/scan-jobs` calls**, not hundreds of individual file calls.

---

### Scan Granularity Summary

```
Repo (N total files)
  │
  ├── Python  (45 files) ──► 1 POST /scan-jobs ──► bandit + semgrep on all 45 at once
  ├── Go      (20 files) ──► 1 POST /scan-jobs ──► gosec + semgrep on all 20 at once
  ├── Shell   (5 files)  ──► 1 POST /scan-jobs ──► shellcheck + semgrep on all 5 at once
  └── YAML    (30 files)
        ├── plain  (22)  ──► 1 POST /scan-jobs ──► yamllint on all 22 at once
        └── K8s    (8)   ──► 1 POST /scan-jobs ──► kubelinter + kubescore + kubeconform on all 8
```

Languages are processed **sequentially** (one at a time). There is no parallel submission across languages.

---

### How the Sandbox Is Auto-Created per Scan Job

You (the scanner) never explicitly create or manage the scan sandbox. That is entirely the responsibility of the `opensandbox-server`. When it receives `POST /scan-jobs`, it automatically:

```
Your API pod                              opensandbox-server
─────────────────────                    ──────────────────────────────────────────
POST /scan-jobs                    ──►   1. Receives {files: {...}, tools: ["bandit", "semgrep"]}
  payload: all Python files              2. AUTO: provisions a fresh code-interpreter
  tools: ["bandit", "semgrep"]               sandbox container
                                              Image: 01community/01sandbox-codeinterpreter
                                         3. AUTO: writes each file into /workspace/
                                              e.g. /workspace/src/auth/tokens.py
                                         4. AUTO: runs bandit -r /workspace/
                                         5. AUTO: runs semgrep --config auto /workspace/
                                         6. AUTO: collects stdout/stderr from both tools
                                         7. AUTO: destroys the sandbox container
                                         8. Returns JSON report
◄──────────────────────────────────      {"report": {"findings": [...], "scans": {...}}}
```

**One `POST /scan-jobs` call = one sandbox container created, used, and destroyed.**

The sandbox container image (`01community/01sandbox-codeinterpreter`) has all security tools pre-installed at image build time:

| Tool | Languages covered |
|------|------------------|
| `bandit` | Python |
| `semgrep` | Python, JS, TS, Go, Java, Ruby, Shell, YAML |
| `gosec` | Go |
| `staticcheck` / `golangci-lint` | Go |
| `shellcheck` | Shell / Bash |
| `yamllint` | YAML |
| `kubelinter` | Kubernetes YAML |
| `kubescore` | Kubernetes YAML |
| `kubeconform` | Kubernetes YAML |
| `gitleaks` | All (secret scanning) |
| `trivy` | All (dependency/container CVE) |

---

### Full Trace for Python Files

```
lang_map["Python"] = [
    "/tmp/reposcanner_abc/repo/src/auth.py",
    "/tmp/reposcanner_abc/repo/src/api.py",
    ...45 files total
]
          │
          ▼
_read_files_as_dict(files[:100], repo_root="/tmp/reposcanner_abc/repo")
  → opens each .py file on the local API pod disk
  → reads full content as string
  → builds dict: {"src/auth.py": "import hashlib...", "src/api.py": "..."}
          │
          ▼
_submit_scan_job(files_dict, tools=["bandit", "semgrep"])
  → single HTTP POST to opensandbox-server /scan-jobs
  → JSON body: {files: {all 45 files}, tools: ["bandit","semgrep"]}
          │
          ▼  (inside opensandbox-server — automatic)
  [auto] NEW sandbox container spun up
  [auto] files written to /workspace/
  [auto] bandit -r /workspace/    → JSON findings
  [auto] semgrep /workspace/      → JSON findings
  [auto] findings merged into report
  [auto] sandbox container destroyed
          │
          ▼
  HTTP response: {"report": {"findings": [...], "scans": {"bandit": {...}, "semgrep": {...}}}}
          │
          ▼
_parse_scan_report(report, "python")
  → extracts each finding into FindingItem(severity, file, line, issue, tool, remediation)

_filter_findings_to_submitted_files(raw_findings, submitted_paths, "Python")
  → drops any finding whose file was not in the 45-file set we submitted
  → prevents cross-language contamination

→ returns LanguageScanResult(language="Python", file_count=45, lines_of_code=3820, findings=[...])
```

---

### File Submission Constraints at a Glance

| Constraint | Value | Where enforced |
|------------|-------|---------------|
| Max files per language | **100** | `files[:FILE_CAP]` in `scan_language()` |
| Max file size | **200 KB** | `_read_files_as_dict()` size check |
| Binary / unreadable files | skipped | `open(..., errors="replace")` + exception catch |
| Sandbox per call | **1 auto-created** | `opensandbox-server` internal |
| Calls per language | **1** (bulk) | `_submit_scan_job()` called once |
| Languages in parallel | **No** — sequential | `for` loop in `_run_scan_pipeline()` |

---

## Deep Dive — JSON Wire Format for File Submission

### Yes — All File Code Is Sent as JSON Strings

When `_submit_scan_job()` calls `httpx_client.post(..., json=payload)`, Python serialises the entire `files_dict` into a JSON body. Each file's **full source code** becomes a plain JSON string value. Newlines in the source code become `\n` escape sequences inside the JSON string.

### Exact JSON Payload Structure

```json
{
  "files": {
    "src/auth/tokens.py": "import hashlib\ndef hash_password(p):\n    return hashlib.md5(p.encode()).hexdigest()\n",
    "src/api/views.py":   "from flask import request\n@app.route('/login')\ndef login():\n    user = request.json.get('user')\n    ...\n",
    "tests/test_auth.py": "import unittest\nclass TestAuth(unittest.TestCase):\n    def test_hash(self):\n        ...\n"
  },
  "tools": ["bandit", "semgrep"]
}
```

- **Key** → relative file path (e.g. `"src/auth/tokens.py"`)
- **Value** → entire raw source code as a JSON string
- **`tools`** → list of tool names the scan-jobs orchestrator should run

All files for one language are inside a **single `"files"` object** in one HTTP request body.

### How the Code Leaves the API Pod and Enters the Container

```
API pod (file_scanner.py)                  opensandbox-server container
──────────────────────────────             ────────────────────────────────────────
files_dict = {                             Receives JSON body
  "src/auth/tokens.py": "<code>",   ──►   Parses "files" object
  "src/api/views.py":   "<code>",          Writes each key-value as a real file:
  ...                                        /workspace/src/auth/tokens.py  ← file on disk
}                                            /workspace/src/api/views.py    ← file on disk
                                           Runs tools against /workspace/:
payload = {"files": files_dict,              bandit -r /workspace/
           "tools": ["bandit","semgrep"]}    semgrep --config auto /workspace/
                                           Tools read actual filesystem files
POST /scan-jobs  ──────────────────►       (NOT the JSON — JSON was just transport)
  Content-Type: application/json
  Body: {JSON above}
```

The JSON is **only a transport mechanism**. By the time the security tools run, the code exists as normal files in `/workspace/` inside the container — the tools have no knowledge they came from JSON.

### Field-Level Breakdown

| JSON field | Python source | Example value |
|------------|--------------|---------------|
| `files` | `files_dict` from `_read_files_as_dict()` | `{"src/auth.py": "import hashlib\n..."}` |
| `files.<key>` | `os.path.relpath(abs_path, repo_root)` | `"src/auth/tokens.py"` |
| `files.<value>` | `open(path).read()` | Full source code as a string |
| `tools` | `tool_hints` selected per language | `["bandit", "semgrep"]` |

### What Happens to Non-String Characters in Source Code

Since source code can contain characters that need escaping in JSON (backslashes, quotes, Unicode), `httpx` handles this automatically via Python's `json.dumps()`. The `opensandbox-server` decodes it back to the original bytes before writing to disk — the tools always see the original unescaped source.

### Serialisation Path (Code to Wire)

```
open(path).read()          →  raw string in Python memory
                                 e.g.  'import hashlib\ndef ...'
                           ↓
json=payload in httpx      →  json.dumps() encodes to JSON string
                                 e.g.  "import hashlib\\ndef ..."
                           ↓
HTTP body (bytes)          →  UTF-8 encoded JSON sent over TCP
                           ↓
opensandbox-server         →  json.loads() decodes back to string
                           ↓
open(path, "w").write()    →  written as real file on container disk
                           ↓
bandit / semgrep           →  reads file from disk normally
```

### Size Implications

Because the entire source code of all files goes into a single JSON body, very large files are excluded before building the dict to keep the payload manageable:

```python
if os.path.getsize(path) > 200 * 1024:   # 200 KB per file
    skipped += 1
    continue
```

With a cap of 100 files × 200 KB each, the theoretical maximum JSON body per language is **~20 MB** before JSON string escaping overhead.

---

## Planned Improvements

### 1 — Structured File Tracking (Hashmap)

**What the problem is today:**
The scanner currently holds only a flat list of file paths per language. It has no knowledge of each file's size or line count until it re-reads the file during the scan. When findings come back, the scanner has to guess which file belongs to which language using file extension rules — this is fragile.

**What the improvement does:**
Instead of a flat list, each file gets its own entry in a hashmap that stores its path, size, and line count upfront — collected once at the language detection stage. This metadata travels through the entire pipeline so nothing needs to be re-computed later.

**Action items:**

- [ ] Create a `FileEntry` structure to hold per-file info: relative path, absolute path, file size in bytes, line count
- [ ] Update the language detection step to build a hashmap of `{file_path → FileEntry}` per language instead of a plain list of paths
- [ ] Pass this hashmap through the pipeline so the scanner uses pre-collected metadata rather than opening files multiple times
- [ ] Use the hashmap keys directly when matching findings back to files — removes the current extension-guessing fallback in the redistribution step
- [ ] Surface per-file metadata (size, line count) in the final scan result for better observability

---

### 2 — Concurrent Independent Job Scanning

**What the problem is today:**

There are two gaps:

1. **Within a single scan** — when a repo has multiple languages (e.g. Python + Go + Shell), they are scanned one after another. The Go scan cannot start until the Python scan finishes. This makes the total scan time the sum of all language scan times rather than the longest one.

2. **Across multiple scans** — the backend already handles multiple jobs independently via job IDs. However, the frontend has no job history. If a user submits a second repo while the first is still scanning, they lose visibility into the first job. There is no way to switch between jobs or pick up where a previous scan left off.

**What the improvement does:**

- **Backend:** All languages within a single repo scan run at the same time instead of waiting for each other. Total scan time becomes roughly the time of the slowest language, not the total of all.
- **Frontend:** A job history panel shows all submitted scans with their live status. The user can freely switch between jobs. Each job has its own independent live stream that continues running in the background regardless of which job the user is currently viewing.

**Action items:**

**Backend:**
- [ ] Run all language scans at the same time (in parallel) within a single repo scan job instead of sequentially one by one
- [ ] Add a `language` field to the live progress events so the frontend can display which specific language is currently being scanned when multiple run in parallel
- [ ] Add a new endpoint to list all active and recently completed job IDs along with their current status — this lets the frontend restore the job history on page load
- [ ] Allow the frontend to check whether a specific job is still running or already finished before deciding whether to reconnect to its live stream or load the cached result

**Frontend:**
- [ ] Keep a job history stored in the browser (persisted across page navigation) showing all submitted scans, their repo URL, current step, and progress
- [ ] Each job gets its own independent live stream connection — switching to view a different job does not disconnect or cancel the others
- [ ] When the user switches back to a job that is still running, reconnect its live stream automatically and resume showing real-time progress
- [ ] When the user switches back to a job that has already finished, load its saved result directly without reconnecting to the stream
- [ ] The scan results view is scoped to a specific job ID, not to the most recently submitted scan — this allows viewing any job from history at any time

---

## Detailed Implementation Plan

### Plan 1 — Structured File Tracking (Hashmap)

#### What changes and why

**Step 1 — Create a `FileEntry` data structure**

A new model is added to `models.py`. Every file discovered during language detection gets one `FileEntry` instead of just its path string. It holds:
- The relative path (used as the key when submitting to scan-jobs)
- The absolute path (used when reading file content from disk)
- File size in bytes (collected once via `os.path.getsize` at detection time)
- Line count (counted once at detection time, not re-counted during scanning)
- The language it belongs to

**Step 2 — Language detector returns a hashmap**

Currently `detect_languages()` returns `Dict[str, List[str]]` — language name to list of absolute paths.

After the change it returns `Dict[str, Dict[str, FileEntry]]` — language name to a dict keyed by relative path, each value being a `FileEntry`.

All four detection paths (linguist, tokei, enry, extension walk) are updated to build this structure. Since the detector already has the absolute path, getting `rel_path` and `size_bytes` at this point costs nothing extra.

**Step 3 — Scanner consumes the hashmap directly**

`scan_language()` receives `Dict[str, FileEntry]` instead of `List[str]`.

- `_count_loc()` is removed — the line count already lives in `FileEntry.loc`
- `_read_files_as_dict()` uses `entry.size_bytes` from the hashmap to skip oversized files instead of calling `os.path.getsize()` again
- The `submitted_rel_paths` set used by `_filter_findings_to_submitted_files()` is just `set(files_dict.keys())` — no change there, but the keys are now guaranteed to be clean relative paths from the hashmap

**Step 4 — Finding redistribution becomes reliable**

Currently `get_target_language()` in `scan_repository.py` guesses which language a finding belongs to by inspecting the file extension. This fails for edge cases.

After the change, the pipeline builds a reverse lookup at the start: `rel_path → language` derived directly from the hashmap. When redistributing findings, the language is looked up from this map — no extension guessing needed.

**Step 5 — Per-file metadata surfaces in the result**

`LanguageScanResult` gains an optional `files` field holding the hashmap. The final `RepoScanResult` therefore exposes per-file size and line count for each language section. This is useful for the frontend to show file-level detail without a second API call.

---

#### Data flow before vs after

```
BEFORE:
detect_languages() → {"Python": ["/tmp/.../auth.py", "/tmp/.../views.py"]}
                               ↓ (must re-read disk to get size/loc)
scan_language() → reads each file again for loc count
                → reads each file again in _read_files_as_dict for content+size check

AFTER:
detect_languages() → {
  "Python": {
    "src/auth.py": FileEntry(abs="/tmp/.../auth.py", size=4200, loc=120),
    "src/views.py": FileEntry(abs="/tmp/.../views.py", size=8100, loc=310),
  }
}
                               ↓ (metadata already collected)
scan_language() → uses FileEntry.loc directly, no second loc count
               → uses FileEntry.size_bytes in _read_files_as_dict, no second stat call
               → uses hashmap keys as authoritative rel_paths for finding attribution
```

---

### Plan 2 — Concurrent Independent Job Scanning

This plan has two independent parts: **backend parallelism** (languages scan at the same time) and **frontend job history** (users can manage multiple scans simultaneously).

---

#### Part A — Backend: Parallel Language Scanning

**Step 1 — Replace sequential loop with parallel execution**

In `scan_repository.py`, the pipeline currently does:

```
for language in lang_map:
    result = await scan_language(...)   # waits here before moving to next language
```

This is changed to launch all language scans at the same time and wait for all of them to finish together. The total wall-clock time for the SCANNING step drops from the sum of all language scan times to roughly the time of the slowest single language.

**Step 2 — Add `language` field to SSE progress events**

When scans run in parallel, multiple languages are making progress simultaneously. The frontend needs to know which language each progress event refers to. A `language` field is added to `ScanEvent` in `models.py` (optional, only populated during `SCANNING` step events).

The overall SCANNING progress percentage is recalculated based on how many language scans have completed out of the total, rather than the current loop index.

**Step 3 — Finding redistribution still works the same**

The redistribution step (Step 5.5) waits for all parallel results to be collected before running. No structural change needed — it just receives a dict of results built from the parallel outputs instead of the sequential loop's dict.

**Step 4 — Add a job list endpoint**

A new `GET /v1/repo-scan/jobs` endpoint is added. It returns a list of all job IDs known to the current pod's SSE manager plus any active job IDs from Redis. Each entry includes the job ID, repo URL, and current status. This allows the frontend to restore job history after a page refresh.

**Step 5 — Add a job status check endpoint**

A new `GET /v1/repo-scan/{job_id}/ping` endpoint (or reuse the existing status endpoint) returns just the current step of a job without opening an SSE stream. The frontend calls this when reconnecting to determine whether to open a live stream or go straight to loading the cached result.

---

#### Part B — Frontend: Multi-Job Management

**Step 1 — Job registry in browser storage**

When a user submits a scan, the returned `job_id`, `repo_url`, and initial status are saved to `localStorage`. On every page load, the frontend reads this registry back and displays all previously submitted jobs. Jobs remain in the registry until manually cleared or their TTL expires (10 minutes after completion).

**Step 2 — Job history sidebar**

A sidebar or tab list is added to the scanner page. Each entry shows:
- The repo URL that was submitted
- The current step (QUEUED, CLONING, SCANNING, DONE, ERROR)
- A progress bar
- A timestamp of when the scan was submitted

Clicking a job entry switches the main view to that job's results or live progress.

**Step 3 — Independent live stream per job**

Each job's `EventSource` connection is created when the job is submitted and stored in a ref keyed by `job_id`. The connection is not closed when the user switches to view a different job — it continues receiving events in the background and updates the job's entry in the registry.

When a `DONE` or `ERROR` event arrives on a background job's stream, the result is saved to `localStorage` under that job's ID and the stream is closed.

**Step 4 — Smart reconnection when switching jobs**

When the user clicks a job entry in the history sidebar:

- **If the job is DONE or ERROR** → load the result from `localStorage` and render it immediately. No network call needed.
- **If the job is still active** → check if the `EventSource` for this job is already open (running in background). If yes, just switch the view to show its live progress. If the stream was lost (e.g. page was refreshed), call `GET /ping` to check status, then either reconnect the stream or load the cached result.

**Step 5 — Scope results view to job ID**

The scan results and live progress panels are refactored to receive a `job_id` as input rather than reading from a global "latest scan" state. This allows any job in the history to be rendered in the main view without affecting other jobs.

---

#### How concurrent jobs stay isolated

Each job already has a unique `job_id` (UUIDv4). The isolation is guaranteed at every layer:

| Layer | How isolation works |
|-------|-------------------|
| Backend pipeline | Each job runs as a separate `BackgroundTask` with its own temp directory and SSE queue |
| SSE queue | `SSEManager._jobs` is a dict keyed by `job_id` — queues never overlap |
| Redis | All keys are namespaced with `job_id` — `repo_scan:status:{job_id}`, `repo_scan:result:{job_id}` |
| Frontend stream | Each `EventSource` is opened to `/v1/repo-scan/{job_id}/status` — different URLs, no shared state |
| Frontend storage | Results saved to `localStorage` under `job_id` key |
