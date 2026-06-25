import asyncio
import json

from auth import validate_token
from core import state
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/v1/jobs", tags=["Generic Jobs Infrastructure"])


@router.get("")
async def list_jobs(job_type: str, user_data: dict = Depends(validate_token)):
    """Lists active and cached jobs matching a specific type.
    Combines in-memory active jobs and historical jobs from Redis.
    """
    jobs = []
    seen_ids = set()

    # 1. Gather in-memory active jobs
    stale_ids = []
    for job_id, job in state.job_tracker._jobs.items():
        if job.job_type == job_type:
            # Sync with Redis if it has completed elsewhere
            redis_status = None
            if state.use_redis and state.redis_client:
                try:
                    r_val = state.redis_client.get(f"job:{job_id}:status")
                    if r_val:
                        redis_status = (
                            r_val.decode() if isinstance(r_val, bytes) else r_val
                        )
                except Exception:
                    pass

            if redis_status in ("DONE", "ERROR"):
                stale_ids.append(job_id)
                continue

            job_status = redis_status if redis_status else job.step

            # Strip result and build response skeleton
            jobs.append(
                {
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "status": job_status,
                    "progress": (
                        100
                        if job_status in ("DONE", "ERROR")
                        else (len(job.event_log) * 10 if job.event_log else 10)
                    ),
                    "stepMessage": job.event_log[-1].message if job.event_log else "",
                    "eventIndex": len(job.event_log),
                    "metadata": job.metadata,
                    "summary": job.metadata.get("summary"),
                    "result": None,
                    "submittedAt": job.metadata.get("submitted_at", ""),
                    "completedAt": job.metadata.get("completed_at", ""),
                }
            )
            seen_ids.add(job_id)

    # Clean up stale local references
    for jid in stale_ids:
        if jid in state.job_tracker._jobs:
            del state.job_tracker._jobs[jid]

    # 2. Gather from Redis if enabled (Crash recovery/history)
    if state.use_redis and state.redis_client:
        r = state.redis_client
        try:
            # Find status keys
            status_keys = r.keys("job:*:status")
            for key in status_keys:
                job_id = key.split(":")[1]
                if job_id in seen_ids:
                    continue

                metadata_str = r.get(f"job:{job_id}:metadata")
                if not metadata_str:
                    continue

                metadata = json.loads(metadata_str)
                if metadata.get("job_type") != job_type:
                    continue

                status = r.get(key)
                events_len = r.llen(f"job:{job_id}:events")

                # Fetch last event for message if available
                last_event_str = r.lindex(f"job:{job_id}:events", -1)
                last_event_msg = ""
                if last_event_str:
                    try:
                        last_event_msg = json.loads(last_event_str).get("message", "")
                    except Exception:
                        pass

                jobs.append(
                    {
                        "job_id": job_id,
                        "job_type": job_type,
                        "status": status,
                        "progress": 100 if status in ("DONE", "ERROR") else 10,
                        "stepMessage": last_event_msg,
                        "eventIndex": events_len,
                        "metadata": metadata,
                        "summary": metadata.get("summary"),
                        "result": None,
                        "submittedAt": metadata.get("submitted_at", ""),
                        "completedAt": metadata.get("completed_at", ""),
                    }
                )
                seen_ids.add(job_id)
        except Exception as e:
            print(f"[router] Redis error in list_jobs: {e}")

    # Return latest first
    jobs.sort(key=lambda j: j.get("submittedAt", ""), reverse=True)
    return jobs


@router.get("/{job_id}/events")
async def get_job_events(
    job_id: str, since: int = 0, user_data: dict = Depends(validate_token)
):
    """Retrieves all past events for replay/hydration."""
    job = state.job_tracker.get_job(job_id)
    if job:
        events = list(job.event_log)[since:]
        return [
            json.loads(
                e.model_dump_json() if hasattr(e, "model_dump_json") else e.json()
            )
            for e in events
        ]

    if state.use_redis and state.redis_client:
        r = state.redis_client
        events_json = r.lrange(f"job:{job_id}:events", since, -1)
        return [json.loads(e) for e in events_json]

    raise HTTPException(status_code=404, detail="Job not found")


@router.get("/{job_id}/status")
async def stream_job_status(
    job_id: str, since: int = 0, user_data: dict = Depends(validate_token)
):
    """Streams live events using the Generic SSE Manager."""
    return StreamingResponse(
        state.job_tracker.stream(job_id, since_index=since),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/{job_id}/result")
async def get_job_result(job_id: str, user_data: dict = Depends(validate_token)):
    """Retrieves the completed JSON scan report for a specific job ID.
    Reads report dynamically from PVC, Redis, or local memory, completely
    bypassing localStorage.
    """
    # 1. Try local/in-memory active job result
    job = state.job_tracker.get_job(job_id)
    if job and job.result:
        return job.result

    # 2. Try Redis cached result (first layer fallback)
    if state.use_redis and state.redis_client:
        r = state.redis_client
        cached = r.get(f"job:{job_id}:result")
        if cached:
            return json.loads(cached)

    # 3. Try dynamic PVC report fetching via standard backends
    try:
        report = state.backend.get_scan_report(job_id)
        if report:
            return report
    except Exception as e:
        print(f"[router] PVC report retrieval failed: {e}")

    raise HTTPException(
        status_code=404, detail="Scan result not found or job still running."
    )
