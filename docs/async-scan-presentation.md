# Modular Async Scan Pipeline — Architecture Flow

This document explains the end-to-end flow of the modular asynchronous scan pipeline, including job tracking, SSE streaming, persistence, and recovery after browser refresh.

## Overview

The system solves:
- Independent scans
- Real-time progress updates
- Persistence across refreshes
- Job recovery
- Server-side event tracking

## High-Level Sequence Diagram

```plaintext
 User/UI         useJobStore      localStorage      FastAPI Backend       Redis
    │                 │                 │                  │                │
    ├─ 1. Click Scan ─►                 │                  │                │
    │                 ├───────── 2. POST /v1/repo-scan ────►                │
    │                 │                                    ├─ 3. Init Job ─►
    │                 ◄───────── 4. Return job_id ─────────┤                │
    │                 ├─ 5. upsert() ──►│                  │                │
    │                 ├──── 6. SSE: GET /status?since=0 ───────────────────►

PHASE 2: LIVE STREAMING

    │                 │                 │                  ├─ 7. Pipeline ─►
    │                 │                 │                  │    Progress
    │                 │                 │                  ├─ 8. RPUSH log ─►
    │                 ◄───────── 9. SSE Message ───────────┤                │
    │                 ├─ 10. upsert() ─►│                  │                │
    ◄─ 11. Re-render ─┤

PHASE 3: BROWSER REFRESH (RECOVERY)

    ├─ 12. Refresh ──►│
    │                 ├─ 13. getAll() ─►│
    │                 │  (Index is 14)
    │                 ├──── 14. SSE: GET /status?since=14 ─────────────────►
    │                 │                 │                  ├─ 15. Fetch ───►
    │                 │                 │                  │    Log 14+
    │                 ◄──────── 16. Fast-Forward SSE ──────┤
```

## Phase 1: The Handshake

1. User clicks scan.
2. Frontend sends POST `/v1/repo-scan`.
3. Backend initializes job and Redis tracking.
4. Backend returns `job_id`.
5. Frontend saves via `upsert()` to localStorage.
6. Frontend opens SSE stream using `?since=0`.

## Phase 2: Live Streaming

- Background worker executes scanning pipeline.
- `tracker.push_event()` pushes updates.
- Redis stores logs using RPUSH.
- SSE streams updates to UI.
- Frontend updates local state and re-renders.

## Phase 3: Browser Refresh Recovery

1. Browser refresh resets React memory.
2. `localStorage` restores state.
3. Frontend reconnects using `?since=14`.
4. Backend fetches missing Redis logs.
5. SSE fast-forwards UI without restarting scan.

## Mental Model

Think of it like Netflix resume playback:

- Frontend = Video player
- Redis = Playback history
- SSE = Live stream
- eventIndex = Timestamp
- Refresh = Reopen app
- since=14 = Resume playback



User/UI         useJobStore      localStorage       FastAPI Backend     Ephemeral Pod        RWX PVC
    │                 │                 │                   │                   │                │
    ├─ 1. Click Scan ─►                 │                   │                   │                │
    │                 ├────── 2. POST /v1/repo-scan ────────►                   │                │
    │                 │                                     ├─ 3. Init Job      │                │
    │                 │                                     │  (Redis State)    │                │
    │                 │                                     │                   │                │
    │                 │                                     ├─ 4. Hand off to BackgroundTasks    │
    │                 │                                     │  (Fire-and-forget thread)          │
    │                 ◄────── 5. Return job_id (Immediate) ─┤                   │                │
    │                 │                 │                   │                   │                │
    │                 ├─ 6. upsert() ──►│                   │                   │                │
    │                 │  (~100 bytes)   │                   │                   │                │
    │                 │                 │                   │                   │                │
    │                 ├──────────────── 7. SSE: GET /status?since=0 ────────────►                │
    │                 │                 │                   │                   │                │
    │                 │                 │                   │  8. Dynamic Spawn │                │
    │                 │                 │                   ├─ (Kubernetes API) ─►               │
    │                 │                 │                   │                   │                │
    │                 │                 │                   │                   │ [Runs Scan]    │
    │                 │                 │                   │                   │ ───────────┐   │
    │                 │                 │                   │                   │ │ Semgrep  │   │
    │                 │                 │                   │                   │ ◄──────────┘   │
    │                 │                 │                   │                   ├─ 9. Save JSON ─►
    │                 │                 │                   │                   │  Report File   │
    │                 │                 │                   │                   │                │
    │                 │                 │                   ◄─ 10. push_event ──┤                │
    │                 │                 │                   │   (status="DONE") │                │
    │                 │                 │                   │                   │                │
    │                 │                 │                   │                   │ [Self-Destruct]│
    │                 │                 │                   │                   X (Max 5 mins)   │
    │                 │                 │                   │                                    │
    │                 ◄──────────────── 11. SSE: "DONE" (No heavy payload) ───────────────────────┤
    │                 ├─ 12. upsert() ─►│                   │                                    │
    ◄─ 13. Render Row ┤                 │                   │                                    │
    │                 │                 │                   │                                    │
    │                 │                 │                   │                                    │
    ─── USER CLICKS HISTORICAL ROW (LAZY LOAD FROM PVC) ──────────────────────────────────────────
    │                 │                 │                   │                                    │
    ├─ 14. Select Row ─►                 │                   │                                    │
    │                 ├─────────────── 15. GET /v1/jobs/{id}/result ────────────►                │
    │                 │                                                         ├─ 16. Read File ─►
    │                 │                                                         ◄─ 17. Payload ──┤
    │                 ◄─────────────── 18. Stream raw report JSON ──────────────┤                │
    │                 │                                                                          │
    ◄─ 19. Render UI ──┤ [Held in volatile browser RAM only - completely bypasses localStorage]   │
    │                 │                                                                          │
