"""
scan_repository.py — FastAPI APIRouter factory for GitHub Repository Scanner.

Pattern mirrors health.py → get_health_router(state, validate_token).
Call get_repo_scan_router(state, validate_token) from codeinspectior_api.py.

Endpoints (all gated by the same validate_token as Quick Scan / Bulk Scan):

  POST   /v1/repo-scan                      Submit a new repo scan job
  GET    /v1/repo-scan/{job_id}/status      SSE stream of live step events
  GET    /v1/repo-scan/{job_id}/result      Final aggregated results

The scan pipeline runs as an asyncio background task so POST returns
immediately with a job_id (same pattern as ?async=true in /v1/scan-jobs).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Callable, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .file_scanner import scan_language
from .github_validator import validate_github_repo
from .language_detector import detect_languages
from .models import RepoScanRequest, RepoScanResult, RepoScanSubmitResponse, ScanStep
from .sandbox_provisioner import clone_repo, destroy_sandbox, provision_sandbox
from .sse_manager import sse_manager

# ─────────────────────────────────────────────
# Background Scan Pipeline
# ─────────────────────────────────────────────


async def _run_scan_pipeline(
    job_id: str,
    repo_url: str,
    owner: str,
    repo: str,
    backend,
) -> None:
    """
    Full scan pipeline executed as a background task.
    Pushes SSE events at each step and stores the final result.

    Steps: PROVISIONING → CLONING → DETECTING → SCANNING → DONE | ERROR
    """
    sandbox_id: Optional[str] = None
    start_time = time.monotonic()

    try:
        # ── Step 1: Provision sandbox ────────────────────────────────
        await sse_manager.push(
            job_id, ScanStep.PROVISIONING, "Provisioning isolated sandbox...", 10
        )
        try:
            sandbox_id = await asyncio.wait_for(
                provision_sandbox(backend), timeout=60.0
            )
        except asyncio.TimeoutError:
            raise RuntimeError("Sandbox provisioning timed out after 60 seconds")

        # ── Step 2: Clone repository ─────────────────────────────────
        await sse_manager.push(
            job_id, ScanStep.CLONING, f"Cloning {owner}/{repo} with --depth=1...", 25
        )
        clone_url = repo_url if repo_url.endswith(".git") else f"{repo_url}.git"
        success, error = await asyncio.wait_for(
            clone_repo(sandbox_id, clone_url), timeout=180.0
        )
        if not success:
            raise RuntimeError(f"git clone failed: {error}")

        # ── Step 3: Detect languages ─────────────────────────────────
        await sse_manager.push(
            job_id,
            ScanStep.DETECTING,
            "Detecting languages (linguist → tokei → enry)...",
            45,
        )
        lang_map, detection_tool = await asyncio.wait_for(
            detect_languages(sandbox_id), timeout=60.0
        )

        if not lang_map:
            raise RuntimeError(
                "No languages detected. Repository may be empty or contain only binary files."
            )

        # ── Step 4: Scan each language ───────────────────────────────
        total_langs = len(lang_map)
        total_files = sum(len(f) for f in lang_map.values())

        def pct(files_count: int) -> float:
            return (files_count / max(total_files, 1)) * 100.0

        await sse_manager.push(
            job_id,
            ScanStep.SCANNING,
            f"Scanning {total_langs} language(s) across {total_files} files...",
            60,
        )

        language_results = {}
        for idx, (language, files) in enumerate(lang_map.items()):
            progress = 60 + int(((idx + 1) / total_langs) * 30)
            await sse_manager.push(
                job_id,
                ScanStep.SCANNING,
                f"Scanning {language} ({min(len(files), 100)} files)...",
                progress,
            )
            result = await asyncio.wait_for(
                scan_language(
                    sandbox_id=sandbox_id,
                    language=language,
                    files=files,
                    percentage=pct(len(files)),
                ),
                timeout=180.0,
            )
            language_results[language] = result

        # ── Step 5: Build and emit final result ──────────────────────
        duration = time.monotonic() - start_time
        total_findings = sum(len(r.findings) for r in language_results.values())

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
        sse_manager.set_result(job_id, final_result)

        await sse_manager.push(
            job_id,
            ScanStep.DONE,
            f"Scan complete — {total_langs} language(s), {total_findings} finding(s) in {duration:.1f}s",
            100,
            detail=final_result.dict(),
        )

    except asyncio.TimeoutError:
        msg = "Scan timed out (5-minute limit exceeded)"
        _store_error(job_id, repo_url, owner, repo, msg, time.monotonic() - start_time)
        await sse_manager.push(job_id, ScanStep.ERROR, msg, 0)

    except Exception as exc:
        msg = str(exc)
        print(f"[RepoScanner] Pipeline error for job {job_id}: {msg}")
        _store_error(job_id, repo_url, owner, repo, msg, time.monotonic() - start_time)
        await sse_manager.push(job_id, ScanStep.ERROR, f"Scan failed: {msg}", 0)

    finally:
        if sandbox_id:
            await destroy_sandbox(sandbox_id)
        sse_manager.cleanup_expired()


def _store_error(
    job_id: str,
    repo_url: str,
    owner: str,
    repo: str,
    error_msg: str,
    duration: float,
) -> None:
    result = RepoScanResult(
        job_id=job_id,
        repo_url=repo_url,
        owner=owner,
        repo=repo,
        status=ScanStep.ERROR,
        error=error_msg,
        scan_duration_seconds=round(duration, 2),
    )
    sse_manager.set_result(job_id, result)


# ─────────────────────────────────────────────
# Router Factory  (mirrors get_health_router pattern)
# ─────────────────────────────────────────────


def get_repo_scan_router(app_state, validate_token: Callable) -> APIRouter:
    """
    Build and return the repo-scan APIRouter, injecting shared state
    and the validate_token dependency — same pattern as get_health_router().

    Usage in codeinspectior_api.py:
        from scan_repository import get_repo_scan_router
        app.include_router(get_repo_scan_router(state, validate_token))
    """
    router = APIRouter()

    # ── POST /v1/repo-scan ──────────────────────────────────────────────
    @router.post(
        "/v1/repo-scan",
        response_model=RepoScanSubmitResponse,
        tags=["Repo Scanner"],
        summary="Submit a public GitHub repository for language detection and scanning",
        dependencies=[Depends(validate_token)],
    )
    async def submit_repo_scan(
        req: RepoScanRequest,
        background_tasks: BackgroundTasks,
    ) -> RepoScanSubmitResponse:
        """
        Validates a public GitHub repository URL and enqueues a full scan job.

        Returns immediately with a `job_id`. Connect to the `/status` SSE endpoint
        for live progress updates and poll `/result` for the final report.

        **Auth**: Same API key (Bearer token) as Quick Scan and Bulk Scan.
        """
        owner, repo = await validate_github_repo(req.repo_url)

        job_id = str(uuid.uuid4())
        sse_manager.create_job(job_id, req.repo_url)
        await sse_manager.push(
            job_id, ScanStep.QUEUED, "Job queued — awaiting sandbox...", 5
        )

        background_tasks.add_task(
            _run_scan_pipeline,
            job_id,
            req.repo_url,
            owner,
            repo,
            app_state.backend,
        )

        base = "/v1/repo-scan"
        return RepoScanSubmitResponse(
            job_id=job_id,
            status=ScanStep.QUEUED,
            status_url=f"{base}/{job_id}/status",
            result_url=f"{base}/{job_id}/result",
        )

    # ── GET /v1/repo-scan/{job_id}/status (SSE) ────────────────────────
    @router.get(
        "/v1/repo-scan/{job_id}/status",
        tags=["Repo Scanner"],
        summary="Stream live scan progress via Server-Sent Events",
        dependencies=[Depends(validate_token)],
    )
    async def stream_scan_status(job_id: str) -> StreamingResponse:
        """
        SSE endpoint streaming scan step events in real-time.

        Each event is a JSON object:
        ```json
        {
          "job_id": "...",
          "step": "CLONING",
          "message": "Cloning owner/repo...",
          "progress": 25,
          "detail": null
        }
        ```
        The stream closes automatically when **DONE** or **ERROR** is emitted.
        """
        job = sse_manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

        return StreamingResponse(
            sse_manager.stream(job_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # ── GET /v1/repo-scan/{job_id}/result ──────────────────────────────
    @router.get(
        "/v1/repo-scan/{job_id}/result",
        response_model=RepoScanResult,
        tags=["Repo Scanner"],
        summary="Retrieve the final aggregated scan result",
        dependencies=[Depends(validate_token)],
    )
    async def get_scan_result(job_id: str) -> RepoScanResult:
        """
        Returns the complete scan result once the job reaches **DONE** or **ERROR**.

        Returns **404** while the scan is still in progress.
        """
        job = sse_manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        if job.result is None:
            raise HTTPException(
                status_code=404,
                detail="Result not ready yet. Job is still in progress.",
            )
        return job.result

    return router
