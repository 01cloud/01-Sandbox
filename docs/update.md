# Summary of Changes - July 6, 2026

## 1. Repository Scanner UI Restructure
*   **Two-Column Layout**: Swapped the triple-panel layout for a cleaner two-column layout in `RepoScannerWidget.tsx`.
*   **Embedded Scan List**: Moved the repository scans list (`JobsPanel`) directly into the left sidebar column, sitting beneath the **Repository URL** inputs and clone credentials.
*   **Embedded Styles**: Configured `JobsPanel.tsx` with a new conditional `embedded` prop to seamlessly remove sidebar borders/padding and fit cleanly inside the left column.
*   **Restored Language Distribution Graph**: Resolved a caching bug in `useJobStore.ts` that caused the language distribution graph and per-language scans table to be missing. Now details are retrieved instantly on job completion and persist through page reloads via `localStorage` payload fallbacks.

## 2. Unit Testing & CI/CD Pipeline
*   **Config Unit Tests**: Added a beginner-friendly unit test suite (`tests/config/test_config.py`) to test default environment fallbacks, custom route prefixes, token headers, and JSON error handling inside `config.py` without touching database layers.
*   **CI Test Isolation**: Split the automated pytest execution in `.github/workflows/test-api.yaml` into four independent stages (PostgreSQL, Redis, Ratelimit, and Configuration) for easier tracking of failure sources.
*   **Warning Silence**: Muted pytest event-loop scope warnings by configuring `asyncio_default_fixture_loop_scope = function` inside `pytest.ini`.

## 3. Configuration Flow Documentation
*   **Architecture Header**: Added a descriptive module header comment block to the top of `config.py` mapping out which parts of the application (JWT validators, API Key routers, sandbox file scanners) import and depend on specific configuration endpoints.






























Real-Time Terminal Logs — Technical Walkthrough
The Problem (Before)
When a scan ran, the UI terminal showed only useless server bookkeeping messages:

[SERVER] Starting security scan job: f90d95f9...
[SERVER] Writing 2 source file(s) to PVC workspace...
[SERVER] Sandbox created (ID: edd879eb...). Waiting for scan results...
The actual scanner tool output — Semgrep findings, Bandit results, Trivy advisories, the full scan summary table — existed only inside the sandbox pod's stdout, which nothing was reading or forwarding.

The Full Pipeline (After)
Browser Terminal (React)
       ↑  SSE stream (EventSource)
       │  event: LOG_LINE
       │  data: "[eab3df4e] Running Semgrep scan..."
       │
apiServer/fastapi (sandbox-api pods)
       ↑  polls process.log every 1s, pushes new lines as LOG_LINE SSE events
       │
opensandbox-server (Pod: opensandbox-server-xxx)
       │  stream_pod_logs_to_file() thread — writes each line to process.log
       ↑
Kubernetes Pod Logs API (read_namespaced_pod_log, follow=True)
       │
Sandbox Pod (e.g. eab3df4e-...-0)
       └── container: sandbox → stdout: Semgrep, Bandit, Gosec, Trivy output
Step-by-Step Breakdown
Step 1 — Frontend: SSE EventSource listener
File: z1sandbox-website/src/hooks/useJobStore.ts

The React frontend opens a persistent EventSource connection to the backend:

GET /v1/repo-scan/<job_id>/status   (SSE stream)
It listens for two event types:

status_update — progress percentage, step message, language statuses
log_line — a raw text line to append to the terminal
typescript
es.addEventListener("log_line", (e) => {
  const data = JSON.parse(e.data);
  setVolatileLogs(prev => [...prev, data.message]);
});
The terminal component (RepoScannerWidget.tsx) renders volatileLogs as scrolling lines in the dark terminal box — no polling, no page refresh, purely event-driven.

Step 2 — apiServer: SSE event publisher & process.log poller
File: apiServer/fastapi/scan_repository/file_scanner.py

When _submit_scan_job() is called for each language, a background asyncio task (poll_logs()) runs concurrently:

python
async def poll_logs():
    last_offset = 0
    await asyncio.sleep(2.0)          # give sandbox time to boot
    while not done_event.is_set():
        status_res = await loop.run_in_executor(
            None, state.backend.get_scan_status, child_job_id
        )
        if isinstance(status_res, str) and len(status_res) > last_offset:
            new_content = status_res[last_offset:]
            last_offset = len(status_res)
            for line in new_content.splitlines():
                if line.strip():
                    await state.job_tracker.push_event(
                        parent_id, "LOG_LINE", line, 60,
                        detail={"child_job_id": child_job_id}
                    )
        await asyncio.sleep(1.0)
get_scan_status(child_job_id) calls GET /scan-status/<job_id> on the opensandbox-server, which reads and returns the content of process.log as plain text.

The poller tracks a last_offset so it only sends new lines each second — it never re-sends lines already pushed to the frontend.

For a 10-language repo scan, 10 of these pollers run concurrently (one per child job), all pushing LOG_LINE events to the single parent job's SSE stream. The frontend receives them interleaved, each prefixed with the sandbox ID so you can tell which pod is talking.

Step 3 — opensandbox-server: process.log writer
File: opensandbox-server/docker-build/src/api/lifecycle.py

3a. log_job_event() — the log file writer
This existing helper appends timestamped lines to the job's log file on the shared PVC:

python
def log_job_event(job_id: str, message: str):
    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
    log_path = os.path.join(data_root, job_id, "reports", "process.log")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as f:
        f.write(f"[{timestamp}] {message}\n")
Previously, log_job_event was only called with [SERVER] bookkeeping messages. The fix makes it also write every line from the pod's stdout.

3b. stream_pod_logs_to_file() — the new pod log streamer (NEW)
This is the core of the implementation. It runs in a background daemon thread, started immediately after the sandbox pod is created:

python
def stream_pod_logs_to_file(sandbox_id, job_id, namespace, stop_event):
    from kubernetes import client as k8s_client, watch as k8s_watch
    v1 = k8s_client.CoreV1Api()       # uses already-loaded in-cluster config
    pod_name = f"{sandbox_id}-0"      # StatefulSet/BatchSandbox naming convention
    # 1. Wait until the pod is Running
    _wait_for_pod(v1, pod_name, timeout=90)
    # 2. Open a streaming log connection (like `kubectl logs -f`)
    w = k8s_watch.Watch()
    for raw in w.stream(
        v1.read_namespaced_pod_log,
        name=pod_name,
        namespace=namespace,
        container="sandbox",
        follow=True,
        _preload_content=False,       # streaming, not buffered
    ):
        if stop_event.is_set():
            w.stop()
            break
        line = raw.decode("utf-8", errors="replace").rstrip()
        if line.strip():
            log_job_event(job_id, f"[{sandbox_id[:8]}] {line}")  # → process.log
Key design decisions:

_preload_content=False — critical. Without this the client buffers until the pod exits. With it, lines are delivered byte-by-byte as the pod writes them.
follow=True — equivalent to kubectl logs -f. Keeps the connection open.
stop_event — a threading.Event so the thread exits cleanly when the scan finishes (success, failure, or timeout).
daemon=True — the thread won't block server shutdown.
3c. Wiring it into create_scan_job
python
created_sandbox = sandbox_service.create_sandbox(sandbox_req)
sandbox_id = created_sandbox.id
_stop_log_stream = threading.Event()
_log_thread = threading.Thread(
    target=stream_pod_logs_to_file,
    args=(sandbox_id, job_id, namespace, _stop_log_stream),
    daemon=True,
)
_log_thread.start()
try:
    # ... existing polling loop for the JSON report ...
finally:
    _stop_log_stream.set()     # always stop the thread
    _log_thread.join(timeout=3)
The finally block guarantees the thread is stopped and joined even if the scan times out or raises an exception.

Step 4 — RBAC fix
File: codeInspector/charts/opensandbox/templates/controller.yaml

The opensandbox-server pod uses service account opensandbox-server, which was bound to the opensandbox-controller ClusterRole. That role granted access to pods (CRUD) but not pods/log — a separate Kubernetes sub-resource.

The kubernetes client's read_namespaced_pod_log maps to:

GET /api/v1/namespaces/<ns>/pods/<name>/log
Without pods/log in the ClusterRole, the API returned HTTP 403 Forbidden.

Fix — Helm chart template (persists across upgrades):

yaml
# Before
resources: ["pods", "services", "configmaps", "namespaces"]
# After
resources: ["pods", "pods/log", "pods/exec", "services", "configmaps", "namespaces"]
Fix — live cluster (takes effect immediately, no pod restart needed):

bash
kubectl patch clusterrole opensandbox-controller --type=json -p='[
  {"op": "replace", "path": "/rules/0/resources",
   "value": ["pods", "pods/log", "pods/exec", "services", "configmaps", "namespaces"]}
]'
RBAC changes in Kubernetes take effect immediately — no pod restart is needed. The opensandbox-server service account gained the permission within milliseconds.

What the terminal now shows for a 10-language scan
All 10 child jobs fire simultaneously via asyncio.gather(). Each gets its own sandbox pod and its own log stream thread. All 10 streams write to the same process.log file (append-only, thread-safe at OS level). The poller on the apiServer side picks up new lines every second and fans them out as SSE events to the browser.

The output is interleaved by time, prefixed by sandbox ID:

[11:38:16] [eab3df4e] Classified Files: Go(45), Rust(12)
[11:38:16] [2e60f3c0] Classified Files: Python(23)
[11:38:17] [eab3df4e] Running Gosec scan...
[11:38:17] [2e60f3c0] Running Bandit scan...
[11:38:17] [5f98493a] Running Semgrep scan...
[11:38:19] [2e60f3c0] ⚠️  B106: Hardcoded password – config.py:42
[11:38:21] [eab3df4e] ══ SECURITY SCAN SUMMARY ══
[11:38:21] [eab3df4e]  GOSEC   │ ⚠️  RISK │ G401: Weak crypto – server.go:88
[11:38:22] [5f98493a]  SEMGREP │ ✅ CLEAN  │ No issues found
Files Changed Summary
File	Change
opensandbox-server/.../lifecycle.py	Added stream_pod_logs_to_file() function + wired it into create_scan_job with threading.Event lifecycle
codeInspector/charts/opensandbox/templates/controller.yaml	Added pods/log and pods/exec to ClusterRole resources
apiServer/fastapi/scan_repository/file_scanner.py	No change — the existing poll_logs() + get_scan_status() mechanism already handled forwarding lines to the SSE stream
apiServer/fastapi/auth/token_validator.py	Separate fix — restricted scan endpoints to require an active API key
