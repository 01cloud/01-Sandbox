from __future__ import annotations

import uuid
from typing import Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from .models import (
    CreateSandboxRequest,
    RunRequest,
    RunResponse,
    SandboxResponse,
    ScanJobRequest,
    ScanJobResponse,
)


async def run_scan_in_background(job_id: str, req_dict: dict):
    """Background worker to trigger scan without blocking HTTP requests."""
    import asyncio
    import json

    from core.app_state import state

    try:
        await state.job_tracker.push_event(
            job_id,
            "PROVISIONING",
            "Provisioning remote sandbox and uploading files...",
            25,
        )
        # Add small delay so UI status transitions are clean
        await asyncio.sleep(1.0)
        await state.job_tracker.push_event(
            job_id, "SCANNING", "Running Semgrep security analysis...", 60
        )
        data = await state.backend.create_scan_job(req_dict)

        # Count severity findings
        high_count = 0
        medium_count = 0
        low_count = 0
        findings = data.get("findings", [])
        for f in findings:
            sev = str(f.get("severity", "INFO")).upper()
            if "HIGH" in sev:
                high_count += 1
            elif "MEDIUM" in sev:
                medium_count += 1
            elif "LOW" in sev:
                low_count += 1

        # Save summary count to job tracker metadata
        job_record = state.job_tracker.get_job(job_id)
        if job_record:
            job_record.metadata["summary"] = {
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
            }
            if state.use_redis and state.redis_client:
                try:
                    metadata_copy = dict(job_record.metadata)
                    metadata_copy["job_type"] = "quick-scan"
                    state.redis_client.set(
                        f"job:{job_id}:metadata",
                        json.dumps(metadata_copy),
                        ex=86400,
                    )
                except Exception:
                    pass

        detail_dict = dict(data)
        detail_dict["high_count"] = high_count
        detail_dict["medium_count"] = medium_count
        detail_dict["low_count"] = low_count

        await state.job_tracker.push_event(
            job_id,
            "DONE",
            f"Scan complete — found {len(findings)} security findings.",
            100,
            detail=detail_dict,
        )
    except asyncio.CancelledError:
        print(f"[BACKGROUND TASK] Scan job cancelled: {job_id}")
        await state.job_tracker.push_event(job_id, "CANCELLED", "Job was cancelled.", 0)
        raise
    except Exception as e:
        print(f"[BACKGROUND TASK ERROR] Scan job failed: {e}")
        await state.job_tracker.push_event(job_id, "ERROR", f"Scan failed: {e}", 0)
        raise


def get_sandboxes_router(state, validate_token: Callable) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/run",
        response_model=RunResponse,
        summary="Dispatch synchronous script explicitly",
        tags=["System"],
    )
    def run_code(req: RunRequest):
        """Evaluates payload instructions passing securely to the configured backend."""
        return state.backend.run(req.code, req.language.value, req.timeout)

    @router.post(
        "/v1/sandboxes",
        response_model=SandboxResponse,
        tags=["Sandboxes"],
        summary="Provision a new isolated sandbox",
        dependencies=[Depends(validate_token)],
    )
    def create_sandbox(req: CreateSandboxRequest):
        """Creates a new sandbox environment using the active backend."""
        return state.backend.create_sandbox(req)

    @router.get(
        "/v1/sandboxes",
        response_model=list[SandboxResponse],
        tags=["Sandboxes"],
        summary="List all active sandboxes",
        dependencies=[Depends(validate_token)],
    )
    def list_sandboxes():
        """Retrieves a list of all currently active sandboxes from the backend."""
        return state.backend.list_sandboxes()

    @router.post(
        "/v1/scan-jobs",
        response_model=ScanJobResponse,
        tags=["Security Scan Pipeline"],
        dependencies=[Depends(validate_token)],
    )
    async def create_scan_job(
        req: ScanJobRequest,
        background_tasks: BackgroundTasks,
        is_async: bool = Query(False, alias="async"),
    ):
        """
        Submits files for unified security scanning.
        Every submission is isolated by a unique UUID in the PVC.
        Supports asynchronous execution via ?async=true query parameter to bypass edge proxy timeouts.
        """
        import datetime
        import json

        job_id = str(uuid.uuid4())
        state.latest_job_id = job_id

        if req.metadata is None:
            req.metadata = {}
        req.metadata["job_id"] = job_id

        submitted_at = datetime.datetime.now(datetime.UTC).isoformat()

        # Initialize in job tracker
        state.job_tracker.create_job(
            job_id,
            "quick-scan",
            {
                "submitted_at": submitted_at,
                "files_count": len(req.files) if req.files else 0,
            },
        )

        await state.job_tracker.push_event(
            job_id, "QUEUED", "Job queued — preparing files...", 5
        )

        from core.queue import QUICK_SCAN, is_available, publish

        if is_available():
            await publish(
                QUICK_SCAN.routing_key,
                {"job_id": job_id, "req_dict": req.dict(exclude_none=True)},
            )
            return ScanJobResponse(job_id=job_id, status="PROCESSING")

        if is_async:
            background_tasks.add_task(
                run_scan_in_background, job_id, req.dict(exclude_none=True)
            )
            return ScanJobResponse(job_id=job_id, status="PROCESSING")
        else:
            try:
                await state.job_tracker.push_event(
                    job_id,
                    "PROVISIONING",
                    "Provisioning remote sandbox and uploading files...",
                    25,
                )
                await state.job_tracker.push_event(
                    job_id, "SCANNING", "Running Semgrep security analysis...", 60
                )
                data = await state.backend.create_scan_job(req.dict(exclude_none=True))

                # Count findings
                high_count = 0
                medium_count = 0
                low_count = 0
                findings = data.get("findings", [])
                for f in findings:
                    sev = str(f.get("severity", "INFO")).upper()
                    if "HIGH" in sev:
                        high_count += 1
                    elif "MEDIUM" in sev:
                        medium_count += 1
                    elif "LOW" in sev:
                        low_count += 1

                job_record = state.job_tracker.get_job(job_id)
                if job_record:
                    job_record.metadata["summary"] = {
                        "high": high_count,
                        "medium": medium_count,
                        "low": low_count,
                    }
                    if state.use_redis and state.redis_client:
                        try:
                            metadata_copy = dict(job_record.metadata)
                            metadata_copy["job_type"] = "quick-scan"
                            state.redis_client.set(
                                f"job:{job_id}:metadata",
                                json.dumps(metadata_copy),
                                ex=86400,
                            )
                        except Exception:
                            pass

                detail_dict = dict(data)
                detail_dict["high_count"] = high_count
                detail_dict["medium_count"] = medium_count
                detail_dict["low_count"] = low_count

                await state.job_tracker.push_event(
                    job_id,
                    "DONE",
                    f"Scan complete — found {len(findings)} security findings.",
                    100,
                    detail=detail_dict,
                )
                return ScanJobResponse(**data)
            except Exception as e:
                await state.job_tracker.push_event(
                    job_id, "ERROR", f"Scan failed: {e}", 0
                )
                raise HTTPException(status_code=500, detail=str(e))

    @router.get(
        "/v1/scan-jobs/{job_id}/report",
        tags=["Security Scan Pipeline"],
        dependencies=[Depends(validate_token)],
    )
    async def get_scan_report(job_id: str):
        """
        Retrieves the persistent JSON scan report for a specific job ID.
        Visible even after the sandbox pod has finished.
        """
        return state.backend.get_scan_report(job_id)

    @router.get(
        "/v1/scan-status/{job_id}",
        tags=["Security Scan Pipeline"],
        dependencies=[Depends(validate_token)],
    )
    async def get_scan_status(job_id: str):
        """
        Retrieves the active state of the sandbox handling the given scan job.
        Useful for polling while a long scan is queued or running asynchronously.
        """
        return state.backend.get_scan_status(job_id)

    @router.get(
        "/v1/job-id",
        tags=["Security Scan Pipeline"],
        dependencies=[Depends(validate_token)],
    )
    async def get_latest_job_id():
        """
        Retrieves the job_id of the most recently initiated scan job in the current session.
        Useful when a /v1/scan-jobs request is blocking and you need the job_id from another tab.
        """
        if not state.latest_job_id:
            raise HTTPException(
                status_code=404, detail="No scan jobs have been initiated yet."
            )
        return {"job_id": state.latest_job_id}

    @router.get(
        "/v1/job-status",
        tags=["Security Scan Pipeline"],
        dependencies=[Depends(validate_token)],
    )
    async def get_latest_job_status():
        """
        Retrieves the status of the most recently initiated scan job in the current session.
        """
        if not state.latest_job_id:
            raise HTTPException(
                status_code=404, detail="No scan jobs have been initiated yet."
            )
        return state.backend.get_scan_status(state.latest_job_id)

    @router.delete(
        "/v1/jobs/{job_id}",
        tags=["Security Scan Pipeline"],
        dependencies=[Depends(validate_token)],
        summary="Cancel a queued or running scan job",
    )
    async def cancel_job(job_id: str):
        """
        Flags a scan job as cancelled in Redis and broadcasts the cancellation request.
        """
        if state.use_redis and state.redis_client:
            try:
                # Set cancellation flag in Redis
                state.redis_client.set(f"job:{job_id}:cancelled", "true", ex=86400)
                # Broadcast cancellation event
                state.redis_client.publish("job:cancellations", job_id)
                print(f"[Cancellation] Published cancellation event for job {job_id}")
            except Exception as e:
                print(f"[Cancellation] Redis cancel error: {e}")
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to publish cancellation to Redis: {e}",
                )
        else:
            # Fallback to local cancellation if Redis not active
            from core.queue.cancellation import cancel_active_task

            await cancel_active_task(state, job_id)

        return {"job_id": job_id, "status": "CANCEL_REQUESTED"}

    return router
