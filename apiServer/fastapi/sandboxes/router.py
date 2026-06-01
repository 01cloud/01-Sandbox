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

    async def run_scan_in_background(req_dict: dict):
        """Background worker to trigger scan without blocking HTTP requests."""
        try:
            await state.backend.create_scan_job(req_dict)
        except Exception as e:
            print(f"[BACKGROUND TASK ERROR] Scan job failed: {e}")

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
        job_id = str(uuid.uuid4())
        state.latest_job_id = job_id

        if req.metadata is None:
            req.metadata = {}
        req.metadata["job_id"] = job_id

        if is_async:
            background_tasks.add_task(
                run_scan_in_background, req.dict(exclude_none=True)
            )
            return ScanJobResponse(job_id=job_id, status="PROCESSING")
        else:
            data = await state.backend.create_scan_job(req.dict(exclude_none=True))
            return ScanJobResponse(**data)

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

    return router
