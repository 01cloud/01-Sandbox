"""
sse_manager.py — In-memory job store and Server-Sent Events streaming.

Each scan job gets an asyncio.Queue. The scanner pushes ScanEvent
objects into the queue; the SSE endpoint drains it as a streaming
response. Jobs are cleaned up automatically after TTL expires.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncIterator, Dict, Optional

from .models import RepoScanResult, ScanEvent, ScanStep

# Job TTL in seconds after reaching a terminal state (DONE / ERROR)
JOB_TTL_SECONDS = 600  # 10 minutes


class JobRecord:
    """Internal state record for a single scan job."""

    def __init__(self, job_id: str, repo_url: str):
        self.job_id = job_id
        self.repo_url = repo_url
        self.queue: asyncio.Queue[Optional[ScanEvent]] = asyncio.Queue()
        self.result: Optional[RepoScanResult] = None
        self.finished_at: Optional[float] = None
        self.step: ScanStep = ScanStep.QUEUED


class SSEManager:
    """
    Manages in-memory job state and SSE event queues.

    Usage pattern (mirrors how /v1/scan-jobs tracks state.latest_job_id):
        manager = SSEManager()
        manager.create_job(job_id, repo_url)
        await manager.push(job_id, ScanStep.CLONING, "Cloning repository...", 30)
        # In the HTTP handler:
        return StreamingResponse(manager.stream(job_id), media_type="text/event-stream")
    """

    def __init__(self):
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    def create_job(self, job_id: str, repo_url: str) -> JobRecord:
        """Register a new scan job. Must be called before push/stream."""
        record = JobRecord(job_id, repo_url)
        self._jobs[job_id] = record
        return record

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    async def push(
        self,
        job_id: str,
        step: ScanStep,
        message: str,
        progress: int,
        detail: Optional[dict] = None,
    ) -> None:
        """Push a status event into the job's SSE queue."""
        job = self._jobs.get(job_id)
        if job is None:
            return
        job.step = step
        event = ScanEvent(
            job_id=job_id,
            step=step,
            message=message,
            progress=progress,
            detail=detail,
        )
        await job.queue.put(event)

        # Mark terminal jobs for TTL cleanup
        if step in (ScanStep.DONE, ScanStep.ERROR):
            job.finished_at = time.monotonic()
            # Sentinel None signals the stream to close
            await job.queue.put(None)

    def set_result(self, job_id: str, result: RepoScanResult) -> None:
        """Store the final result so it can be retrieved by GET /result."""
        job = self._jobs.get(job_id)
        if job:
            job.result = result

    async def stream(self, job_id: str) -> AsyncIterator[str]:
        """
        Async generator that yields SSE-formatted strings.
        Closes automatically when a terminal event (DONE/ERROR) is received.
        """
        job = self._jobs.get(job_id)
        if job is None:
            # Yield a single error event and close
            error_event = ScanEvent(
                job_id=job_id,
                step=ScanStep.ERROR,
                message=f"Job {job_id} not found",
                progress=0,
            )
            yield f"data: {error_event.json()}\n\n"
            return

        while True:
            try:
                event: Optional[ScanEvent] = await asyncio.wait_for(
                    job.queue.get(), timeout=30.0
                )
            except asyncio.TimeoutError:
                # Send keep-alive ping to prevent proxy timeouts
                yield ": ping\n\n"
                continue

            if event is None:
                # Terminal sentinel received — close the stream
                break

            yield f"data: {event.json()}\n\n"

            if event.step in (ScanStep.DONE, ScanStep.ERROR):
                break

    def cleanup_expired(self) -> None:
        """Remove job records past their TTL. Call periodically."""
        now = time.monotonic()
        expired = [
            jid
            for jid, rec in self._jobs.items()
            if rec.finished_at and (now - rec.finished_at) > JOB_TTL_SECONDS
        ]
        for jid in expired:
            del self._jobs[jid]


# Module-level singleton shared across the router
sse_manager = SSEManager()
