from __future__ import annotations

import uuid
from typing import Callable, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sandboxes.models import ScanJobRequest, ScanJobResponse


def get_scan_jobs_router(state, validate_token: Callable) -> APIRouter:
    router = APIRouter()

    async def run_scan_in_background(job_id: str, req_dict: dict):
        """Background worker — mirrors sandboxes router's full job-tracker lifecycle."""
        import asyncio
        import json

        try:
            # Simulation check for file content error triggers
            files = req_dict.get("files", {})
            for filename, content in files.items():
                if "# SIMULATE_RETRY" in content or "# SIMULATE_ERROR" in content:
                    raise ValueError(
                        f"Simulated error triggered by content in file {filename}"
                    )

            await state.job_tracker.push_event(
                job_id,
                "PROVISIONING",
                "Provisioning remote sandbox and uploading files...",
                25,
            )
            await asyncio.sleep(1.0)
            await state.job_tracker.push_event(
                job_id, "SCANNING", "Running Semgrep security analysis...", 60
            )
            data = await state.backend.create_scan_job(req_dict)

            high_count = medium_count = low_count = 0
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
            summary_data = {
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
            }
            if job_record:
                job_record.metadata["summary"] = summary_data
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
            elif state.use_redis and state.redis_client:
                try:
                    meta_str = state.redis_client.get(f"job:{job_id}:metadata")
                    if meta_str:
                        metadata_copy = json.loads(meta_str)
                        metadata_copy["summary"] = summary_data
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
        except Exception as e:
            print(f"[PROXY BACKGROUND TASK ERROR] Scan job {job_id} failed: {e}")
            await state.job_tracker.push_event(job_id, "ERROR", f"Scan failed: {e}", 0)

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
                # Simulation check for file content error triggers (sync scanning path)
                files = req.files or {}
                for filename, content in files.items():
                    if "# SIMULATE_RETRY" in content or "# SIMULATE_ERROR" in content:
                        raise ValueError(
                            f"Simulated error triggered by content in file {filename}"
                        )

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
        summary="Cancel a queued or running scan job, or permanently purge a completed job",
    )
    async def cancel_or_delete_job(job_id: str, purge: bool = Query(False)):
        """
        Flags a scan job as cancelled. If purge=True, permanently deletes the job records
        from memory/Redis and purges all reports and source files from the PVC.

        When purge=True the Redis keys and in-memory tracker entry are wiped **immediately**
        in this HTTP handler (synchronously) before the RabbitMQ delete message is queued.
        This guarantees that GET /v1/repo-scan/jobs (polled every 5 s by the UI) stops
        returning the job at once, regardless of queue processing latency — so a job that
        was previously cancelled and is then explicitly deleted disappears from the UI
        immediately without waiting for the async worker to finish.
        """
        from core.queue import is_available, publish

        if not is_available():
            raise HTTPException(
                status_code=503,
                detail="RabbitMQ service is unavailable. Cannot process job deletion.",
            )

        # ── Eager purge: wipe Redis + in-memory tracker synchronously ──────────
        # When purge=True we remove the job from all storage layers right now so
        # that any concurrent poll (GET /v1/repo-scan/jobs) cannot surface it again.
        # The RabbitMQ worker will still run to clean up PVC/sandbox artifacts.
        if purge:
            # 1. Remove from in-memory job tracker (this pod)
            state.job_tracker.delete_job(job_id)

            # 2. Delete all Redis keys for this job
            if state.use_redis and state.redis_client:
                try:
                    state.redis_client.delete(
                        f"job:{job_id}:status",
                        f"job:{job_id}:metadata",
                        f"job:{job_id}:events",
                        f"job:{job_id}:result",
                        f"job:{job_id}:cancelled",
                        f"job:{job_id}:child_jobs",
                    )
                    print(
                        f"[DeleteJob] Eagerly purged Redis keys for job {job_id} before queuing worker"
                    )
                except Exception as redis_err:
                    print(
                        f"[DeleteJob] Warning: eager Redis purge failed for job {job_id}: {redis_err}"
                    )

        payload = {"job_id": job_id, "purge": purge}
        await publish("scan.delete", payload)

        return {"job_id": job_id, "status": "DELETE_QUEUED"}

    return router
