# Async Multi-Job Pipeline — GitHub Actions-Style Scanner

## 1. Problem Statement

| Layer | Current Gap |
|---|---|
| **Frontend** | Single `useState` scan — refresh destroys all state. One scan at a time only. |
| **SSEManager** | `asyncio.Queue` per job — drained once, no replay on reconnect. |
| **Redis** | Only terminal state written (`DONE`/`ERROR`). No event history. |

**Goal**: Every scan job behaves like a GitHub Actions workflow run — independently tracked, replayable, concurrently observable, and persisted across page refreshes.

---

## 2. Target UX — GitHub Actions Model

```
┌─────────────────────────────────────────────────────────────────┐
│  GitHub Repository Scanner                          [Scan New]   │
├────────────────────┬────────────────────────────────────────────┤
│  SCAN JOBS         │  JOB DETAIL: github.com/org/repo-A         │
│  ─────────────     │  ─────────────────────────────────────────  │
│  ● repo-A  DONE ✓  │  ○ QUEUED          ✓  Job Queued           │
│  ● repo-B  RUN… ⟳  │  ○ PROVISIONING    ✓  Sandbox ready        │
│  ● repo-C  QUEUE   │  ○ CLONING         ✓  Cloned in 4.2s       │
│                    │  ○ DETECTING       ✓  Python · Go · TS      │
│                    │  ○ SCANNING        ⟳  Scanning Python…      │
│                    │  ○ DONE            ─  Waiting               │
│                    │                                             │
│                    │  [████████████░░░]  72%                     │
└────────────────────┴────────────────────────────────────────────┘
```

Each row in the left panel is a live, independent scan. Clicking any row switches the right panel to that job's pipeline without pausing or cancelling any other job.

---

## 3. Architecture

```
Browser
  └── useJobStore (localStorage)
        ├── JobsPanel  ← list of all jobs, status badges
        └── PipelineView ← selected job's step-by-step progress

FastAPI Pod
  ├── POST /v1/repo-scan         → job_id, BackgroundTask
  ├── GET  /v1/repo-scan/jobs    → all jobs (memory + Redis)
  ├── GET  /v1/repo-scan/{id}/status?since=N  → SSE stream
  └── GET  /v1/repo-scan/{id}/events?since=N  → event replay

Redis
  ├── repo_scan:status:{id}     → current ScanStep (TTL 24h)
  ├── repo_scan:result:{id}     → final JSON (TTL 24h)
  ├── repo_scan:events:{id}     → RPUSH ordered event log (TTL 24h)
  └── repo_scan:chan:{id}       → Pub/Sub live broadcast
```

---

## 4. Backend Changes

### 4.1 `sse_manager.py` — Event Replay Buffer

Add a `deque` replay log to every `JobRecord` so reconnecting clients can catch up:

```python
from collections import deque

class JobRecord:
    def __init__(self, job_id, repo_url):
        self.job_id = job_id
        self.repo_url = repo_url
        self.queue: asyncio.Queue = asyncio.Queue()
        self.event_log: deque = deque(maxlen=200)  # NEW
        self.result = None
        self.finished_at = None
        self.step = ScanStep.QUEUED

class SSEManager:
    async def push(self, job_id, step, message, progress, detail=None):
        job = self._jobs.get(job_id)
        if not job: return
        event = ScanEvent(job_id=job_id, step=step,
                          message=message, progress=progress, detail=detail)
        job.event_log.append(event)       # buffer for replay
        job.step = step
        await job.queue.put(event)
        if step in (ScanStep.DONE, ScanStep.ERROR):
            job.finished_at = time.monotonic()
            await job.queue.put(None)     # sentinel

    async def stream(self, job_id: str, since_index: int = 0):
        job = self._jobs.get(job_id)
        if not job:
            yield f'data: {{"step":"ERROR","message":"not found"}}\n\n'
            return
        # Replay buffered events client hasn't seen
        for event in list(job.event_log)[since_index:]:
            yield f"data: {event.json()}\n\n"
        if job.finished_at:
            return   # already terminal — no live queue needed
        while True:
            try:
                event = await asyncio.wait_for(job.queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:
                break
            yield f"data: {event.json()}\n\n"
            if event.step in (ScanStep.DONE, ScanStep.ERROR):
                break
```

### 4.2 `scan_repository.py` — Four New Behaviours

**A. Redis event log on every push:**
```python
async def push_event(step, message, progress, detail=None):
    await sse_manager.push(job_id, step, message, progress, detail)
    if app_state.use_redis and app_state.redis_client:
        event = ScanEvent(job_id=job_id, step=step,
                          message=message, progress=progress, detail=detail)
        event_json = event.json()
        app_state.redis_client.publish(f"repo_scan:chan:{job_id}", event_json)
        app_state.redis_client.rpush(f"repo_scan:events:{job_id}", event_json)
        app_state.redis_client.expire(f"repo_scan:events:{job_id}", 86400)
        app_state.redis_client.set(f"repo_scan:status:{job_id}",
                                   step.value, ex=86400)  # 24h TTL
```

**B. Echo `repo_url` + `submitted_at` in submit response** (needed for localStorage indexing):
```python
return RepoScanSubmitResponse(
    job_id=job_id, status=ScanStep.QUEUED,
    status_url=f"{base}/{job_id}/status",
    result_url=f"{base}/{job_id}/result",
    repo_url=req.repo_url,                       # NEW
    submitted_at=datetime.utcnow().isoformat(),  # NEW
)
```

**C. `GET /v1/repo-scan/jobs` — list all jobs:**
```python
@router.get("/v1/repo-scan/jobs", tags=["Repo Scanner"],
            dependencies=[Depends(validate_token)])
async def list_scan_jobs():
    jobs = {}
    for jid, rec in sse_manager._jobs.items():
        jobs[jid] = {"job_id": jid, "repo_url": rec.repo_url,
                     "status": rec.step.value}
    if app_state.use_redis and app_state.redis_client:
        for key in app_state.redis_client.scan_iter("repo_scan:status:*"):
            jid = key.decode().split(":")[-1]
            status = app_state.redis_client.get(key).decode()
            if jid not in jobs:
                jobs[jid] = {"job_id": jid, "status": status}
    return list(jobs.values())
```

**D. `GET /v1/repo-scan/{job_id}/events?since=N` — full replay:**
```python
@router.get("/v1/repo-scan/{job_id}/events",
            dependencies=[Depends(validate_token)])
async def get_job_events(job_id: str, since: int = 0):
    job = sse_manager.get_job(job_id)
    if job:
        return [json.loads(e.json()) for e in list(job.event_log)[since:]]
    if app_state.use_redis and app_state.redis_client:
        raw = app_state.redis_client.lrange(
            f"repo_scan:events:{job_id}", since, -1)
        return [json.loads(e) for e in raw]
    raise HTTPException(404, f"Job {job_id} not found")
```

**E. `?since=N` on SSE stream handler:**
```python
@router.get("/v1/repo-scan/{job_id}/status")
async def stream_scan_status(job_id: str, since: int = 0):
    job = sse_manager.get_job(job_id)
    if job:
        return StreamingResponse(sse_manager.stream(job_id, since_index=since),
                                 media_type="text/event-stream", ...)
```

### 4.3 `models.py` — Updated Response Model

```python
class RepoScanSubmitResponse(BaseModel):
    job_id: str
    status: ScanStep = ScanStep.QUEUED
    status_url: str
    result_url: str
    repo_url: str        # NEW
    submitted_at: str    # NEW — ISO-8601
```

---

## 5. Frontend Changes

### 5.1 `src/lib/jobStore.ts` (NEW)

Typed localStorage adapter — survives refresh, tab close, browser restart.

```typescript
export interface PersistedJob {
  job_id: string;
  repo_url: string;
  status: string;        // ScanStep value
  progress: number;
  stepMessage: string;
  eventIndex: number;    // events consumed — used for ?since=N reconnect
  result: ScanResult | null;
  submittedAt: string;
  completedAt: string | null;
}

const KEY = "repo_scan_jobs_v1";

export const jobStore = {
  getAll: (): PersistedJob[] =>
    JSON.parse(localStorage.getItem(KEY) || "[]"),

  upsert: (job: PersistedJob): void => {
    const all = jobStore.getAll();
    const idx = all.findIndex(j => j.job_id === job.job_id);
    idx >= 0 ? (all[idx] = job) : all.unshift(job);
    localStorage.setItem(KEY, JSON.stringify(all));
  },

  get: (id: string): PersistedJob | null =>
    jobStore.getAll().find(j => j.job_id === id) ?? null,

  remove: (id: string): void => {
    localStorage.setItem(KEY,
      JSON.stringify(jobStore.getAll().filter(j => j.job_id !== id)));
  },
};
```

### 5.2 `src/hooks/useJobStore.ts` (NEW)

```typescript
export function useJobStore(apiBase: string, apiKey: string) {
  const [jobs, setJobs] = useState<PersistedJob[]>(() => jobStore.getAll());
  const esRefs = useRef<Record<string, EventSource>>({});

  // On mount — reconnect any in-progress jobs
  useEffect(() => {
    jobs
      .filter(j => !["DONE", "ERROR"].includes(j.status))
      .forEach(j => openStream(j.job_id, j.eventIndex));
  }, []);

  const refresh = () => setJobs(jobStore.getAll());

  const addJob = (job: PersistedJob) => { jobStore.upsert(job); refresh(); };

  const openStream = (job_id: string, since = 0) => {
    esRefs.current[job_id]?.close();
    const es = new EventSource(
      `${apiBase}/v1/repo-scan/${job_id}/status?since=${since}` +
      `&token=${encodeURIComponent(apiKey)}`
    );
    esRefs.current[job_id] = es;

    es.onmessage = (e) => {
      const ev = JSON.parse(e.data);
      const stored = jobStore.get(job_id)!;
      jobStore.upsert({
        ...stored,
        status: ev.step,
        progress: ev.progress,
        stepMessage: ev.message,
        eventIndex: stored.eventIndex + 1,
        result: ev.step === "DONE" ? ev.detail : stored.result,
        completedAt: ev.step === "DONE" ? new Date().toISOString() : null,
      });
      refresh();
      if (["DONE", "ERROR"].includes(ev.step)) {
        es.close();
        delete esRefs.current[job_id];
      }
    };

    es.onerror = () => {
      es.close();
      // Reconnect from last known index
      const stored = jobStore.get(job_id);
      if (stored && !["DONE","ERROR"].includes(stored.status)) {
        setTimeout(() => openStream(job_id, stored.eventIndex), 2000);
      }
    };
  };

  // Cleanup on unmount
  useEffect(() => () => {
    Object.values(esRefs.current).forEach(es => es.close());
  }, []);

  return { jobs, addJob, openStream };
}
```

### 5.3 `RepoScanner.tsx` — Component Refactor

Split into three parts:

**`ScanSubmitForm`** — input + submit, calls POST, registers job:
```typescript
const handleScan = async () => {
  const resp = await fetch(`${API_BASE}/v1/repo-scan`, {
    method: "POST",
    headers: { "Content-Type": "application/json",
                Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({ repo_url: url }),
  });
  const data = await resp.json();
  addJob({
    job_id: data.job_id,
    repo_url: data.repo_url,
    submittedAt: data.submitted_at,
    status: "QUEUED", progress: 0, stepMessage: "",
    eventIndex: 0, result: null, completedAt: null,
  });
  openStream(data.job_id, 0);  // starts SSE independently
  setSelectedJobId(data.job_id);
};
```

**`JobsPanel`** — left sidebar listing all jobs:
```tsx
{jobs.map(job => (
  <button key={job.job_id}
    onClick={() => setSelectedJobId(job.job_id)}
    className={cn("job-row", selectedJobId === job.job_id && "active")}>
    <StatusDot status={job.status} />
    <span className="repo-name">{extractRepo(job.repo_url)}</span>
    <StatusBadge status={job.status} />
  </button>
))}
```

**`PipelineView`** — GitHub Actions-style step list for selected job:
```tsx
const PIPELINE_STEPS = [
  { key: "QUEUED",       label: "Job Queued" },
  { key: "PROVISIONING", label: "Provision Sandbox" },
  { key: "CLONING",      label: "Clone Repository" },
  { key: "DETECTING",    label: "Detect Languages" },
  { key: "SCANNING",     label: "Security Scan" },
  { key: "DONE",         label: "Complete" },
];

{PIPELINE_STEPS.map((s, i) => {
  const stepIdx = STEPS.indexOf(job.status);
  const thisIdx = STEPS.indexOf(s.key);
  const isDone   = thisIdx < stepIdx || job.status === "DONE";
  const isActive = s.key === job.status && job.status !== "ERROR";
  const isError  = job.status === "ERROR" && thisIdx >= stepIdx;

  return (
    <div key={s.key} className="pipeline-step">
      <StepIcon done={isDone} active={isActive} error={isError} />
      <span>{s.label}</span>
      {isActive && <span className="step-msg">{job.stepMessage}</span>}
    </div>
  );
})}
<ProgressBar value={job.progress} error={job.status === "ERROR"} />
```

---

## 6. Sequence Diagrams

### 6.1 — Concurrent Job Submission

```
User              Browser (JobStore)        FastAPI             Redis
 │                      │                     │                   │
 ├─ Submit repo-A ──────►│                     │                   │
 │                      ├─ POST /v1/repo-scan ►│                   │
 │                      │                     ├─ create job aaa   │
 │                      │                     ├─ RPUSH events:aaa ►│
 │                      │◄── {job_id: aaa} ────┤                   │
 │                      ├─ localStorage.upsert(aaa, QUEUED)        │
 │                      ├─ EventSource /aaa/status?since=0         │
 │                      │       (independent SSE stream)           │
 │                      │                     │                   │
 ├─ Submit repo-B ──────►│   (aaa still scanning)                  │
 │                      ├─ POST /v1/repo-scan ►│                   │
 │                      │◄── {job_id: bbb} ────┤                   │
 │                      ├─ localStorage.upsert(bbb, QUEUED)        │
 │                      ├─ EventSource /bbb/status?since=0         │
 │                      │       (second independent SSE stream)    │
 │                      │                     │                   │
 │  JobsPanel shows:    │                     │                   │
 │  ● aaa SCANNING ⟳    │                     │                   │
 │  ● bbb QUEUED        │                     │                   │
```

### 6.2 — Page Refresh / Reconnect

```
User              localStorage          FastAPI           Redis
 │  (refresh)          │                   │                │
 ├──────────────────── page mount ─────────────────────────►
 │                     │                   │                │
 │              getAll() ── [{aaa, SCANNING, idx=14}, ...]  │
 │                     │                   │                │
 │         aaa not terminal                │                │
 │                     ├── GET /aaa/events?since=14 ────────►
 │                     │                   │◄── LRANGE 14 ──┤
 │                     │◄── [events 14..N] ┤                │
 │                     │                   │                │
 │          if N contains DONE:            │                │
 │                     ├── upsert(DONE, result)             │
 │          else still running:            │                │
 │                     ├── EventSource /aaa/status?since=N  │
 │                     │   (resumes live stream)            │
```

### 6.3 — Event Flow Per Pipeline Step

```
BackgroundTask    SSEManager         Redis          EventSource (Browser)
     │                │                │                    │
     ├─ push(CLONING) ►│                │                    │
     │                ├─ event_log.append(ev)               │
     │                ├─ queue.put(ev)                      │
     │                ├──── RPUSH events:{id} ─────────────►│
     │                ├──── PUBLISH chan:{id} ──────────────►│
     │                ├──── SET status:{id}=CLONING ────────►│
     │                │                │◄── SSE data ────────┤
     │                │                │  onmessage:         │
     │                │                │  localStorage.upsert│
     │                │                │  {status=CLONING,   │
     │                │                │   eventIndex+1}     │
```

---

## 7. File Change Summary

### Backend

| File | Change Type | Description |
|---|---|---|
| `sse_manager.py` | Modify | Add `event_log: deque` to `JobRecord`; `since_index` param in `stream()` |
| `scan_repository.py` | Modify | `RPUSH` every event to Redis; 24h TTL; `GET /jobs`; `GET /{id}/events`; `?since=N` on SSE |
| `models.py` | Modify | Add `repo_url`, `submitted_at` to `RepoScanSubmitResponse` |

### Frontend

| File | Change Type | Description |
|---|---|---|
| `src/lib/jobStore.ts` | **NEW** | localStorage CRUD for `PersistedJob[]` |
| `src/hooks/useJobStore.ts` | **NEW** | React hook; SSE management; reconnect logic |
| `src/pages/RepoScanner.tsx` | Modify | Split into `ScanSubmitForm`, `JobsPanel`, `PipelineView` |

---

## 8. Verification Plan

| Test | Method | Pass Condition |
|---|---|---|
| Concurrent scans | Submit 2 URLs back-to-back | Both appear in `JobsPanel`; pipeline advances independently |
| Refresh mid-scan | Refresh at step `SCANNING` | UI fast-forwards to current step from Redis replay |
| Completed job restore | Complete scan, close tab, reopen | Result loads instantly from localStorage |
| Cross-pod replay | Hit `/events` on a different pod | Returns full event list from Redis `LRANGE` |
| Reconnect SSE | Kill network, restore | EventSource reopens with `?since=N` from stored `eventIndex` |
