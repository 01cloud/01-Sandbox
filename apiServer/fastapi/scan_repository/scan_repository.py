"""
scan_repository.py — FastAPI APIRouter factory for GitHub Repository Scanner.

Pattern mirrors health.py → get_health_router(state, validate_token).
Call get_repo_scan_router(state, validate_token) from codeinspectior_api.py.

This implementation is fully cluster-aware (multi-pod safe) using the shared
Redis instance for job statuses, Pub/Sub event broadcasting, and cached results.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import AsyncIterator, Callable, Optional

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
    app_state,
) -> None:
    """
    Full scan pipeline executed as a background task.
    Pushes SSE events locally and broadcasts them via Redis Pub/Sub.
    """
    sandbox_id: Optional[str] = None
    start_time = time.monotonic()
    backend = app_state.backend
    jid = job_id[:8]  # short ID for log readability

    def log(step: str, msg: str) -> None:
        elapsed = time.monotonic() - start_time
        print(f"[RepoScanner][{step}][{jid}] (+{elapsed:.1f}s) {msg}")

    async def push_event(
        step: ScanStep,
        message: str,
        progress: int,
        detail: Optional[dict] = None,
    ) -> None:
        # 1. Update local sse_manager
        await sse_manager.push(job_id, step, message, progress, detail)
        # 2. Broadcast via Redis Pub/Sub & update status key
        if app_state.use_redis and app_state.redis_client:
            try:
                from .models import ScanEvent

                event = ScanEvent(
                    job_id=job_id,
                    step=step,
                    message=message,
                    progress=progress,
                    detail=detail,
                )
                event_json = event.json()
                app_state.redis_client.publish(f"repo_scan:chan:{job_id}", event_json)
                app_state.redis_client.set(
                    f"repo_scan:status:{job_id}", step.value, ex=3600
                )
            except Exception as e:
                print(f"[RepoScanner] Redis pipeline broadcast error: {e}")

    log("INIT", f"Pipeline started for {owner}/{repo} (job={job_id})")
    log("INIT", f"Repository URL: {repo_url}")
    log("INIT", f"Redis enabled: {app_state.use_redis}")

    try:
        # ── Step 1: Provision sandbox ────────────────────────────────
        log("PROVISIONING", "Creating isolated local sandbox directory...")
        await push_event(ScanStep.PROVISIONING, "Provisioning isolated sandbox...", 10)
        try:
            sandbox_id = await asyncio.wait_for(
                provision_sandbox(backend), timeout=60.0
            )
        except asyncio.TimeoutError:
            raise RuntimeError("Sandbox provisioning timed out after 60 seconds")
        log("PROVISIONING", f"Sandbox ready at: {sandbox_id}")

        # ── Step 2: Clone repository ─────────────────────────────────
        clone_url = repo_url if repo_url.endswith(".git") else f"{repo_url}.git"
        log("CLONING", f"Running: git clone --depth=1 {clone_url}")
        await push_event(
            ScanStep.CLONING, f"Cloning {owner}/{repo} with --depth=1...", 25
        )
        success, error = await asyncio.wait_for(
            clone_repo(sandbox_id, clone_url), timeout=180.0
        )
        if not success:
            log("CLONING", f"git clone FAILED: {error}")
            raise RuntimeError(f"git clone failed: {error}")
        log("CLONING", f"Repository cloned successfully to: {sandbox_id}/repo/")

        # ── Step 3: Detect languages ─────────────────────────────────
        log(
            "DETECTING",
            "Starting language detection (tokei → enry → extension walk)...",
        )
        await push_event(
            ScanStep.DETECTING,
            "Detecting languages (tokei → enry → extension walk)...",
            45,
        )
        lang_map, detection_tool = await asyncio.wait_for(
            detect_languages(sandbox_id), timeout=60.0
        )

        if not lang_map:
            log(
                "DETECTING",
                "No languages detected — repository may be empty or binary-only",
            )
            raise RuntimeError(
                "No languages detected. Repository may be empty or contain only binary files."
            )

        total_langs = len(lang_map)
        total_files = sum(len(f) for f in lang_map.values())
        log("DETECTING", f"Detection tool used: {detection_tool.value}")
        log(
            "DETECTING",
            f"Languages found ({total_langs}): {', '.join(lang_map.keys())}",
        )
        for lang, files in lang_map.items():
            log("DETECTING", f"  {lang}: {len(files)} file(s)")

        # ── Step 4: Scan each language ───────────────────────────────
        def pct(files_count: int) -> float:
            return (files_count / max(total_files, 1)) * 100.0

        log(
            "SCANNING",
            f"Beginning security scan: {total_langs} language(s), {total_files} total file(s)",
        )
        await push_event(
            ScanStep.SCANNING,
            f"Scanning {total_langs} language(s) across {total_files} files...",
            60,
        )

        language_results = {}
        for idx, (language, files) in enumerate(lang_map.items()):
            file_count = min(len(files), 100)
            progress = 60 + int(((idx + 1) / total_langs) * 30)
            log(
                "SCANNING",
                f"[{idx+1}/{total_langs}] Scanning {language} — {file_count} file(s) submitted to scan-jobs pipeline...",
            )
            await push_event(
                ScanStep.SCANNING,
                f"Scanning {language} ({file_count} files)...",
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
            lang_findings = len(result.findings)
            log(
                "SCANNING",
                f"[{idx+1}/{total_langs}] {language} complete — {result.lines_of_code} LoC, {lang_findings} finding(s)",
            )
            if lang_findings > 0:
                for f in result.findings[:5]:  # log first 5 findings
                    log(
                        "SCANNING",
                        f"  [{f.severity}] {f.file}:{f.line or '?'} — {f.issue[:80]} (tool={f.tool})",
                    )
                if lang_findings > 5:
                    log("SCANNING", f"  ... and {lang_findings - 5} more finding(s)")

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

        # 1. Update local job record
        sse_manager.set_result(job_id, final_result)

        # 2. Update Redis results for cluster access
        if app_state.use_redis and app_state.redis_client:
            try:
                app_state.redis_client.set(
                    f"repo_scan:status:{job_id}", ScanStep.DONE.value, ex=3600
                )
                app_state.redis_client.set(
                    f"repo_scan:result:{job_id}", final_result.json(), ex=3600
                )
            except Exception as e:
                print(f"[RepoScanner] Redis final done save error: {e}")

        log("DONE", "=" * 60)
        log("DONE", f"Scan finished for {owner}/{repo}")
        log("DONE", f"  Job ID         : {job_id}")
        log("DONE", f"  Detection tool : {detection_tool.value}")
        log(
            "DONE",
            f"  Languages      : {total_langs}  ({', '.join(language_results.keys())})",
        )
        log("DONE", f"  Total files    : {total_files}")
        log("DONE", f"  Total findings : {total_findings}")
        log("DONE", f"  Duration       : {duration:.2f}s")
        log("DONE", "=" * 60)

        # 3. Emit final DONE status event
        await push_event(
            ScanStep.DONE,
            f"Scan complete — {total_langs} language(s), {total_findings} finding(s) in {duration:.1f}s",
            100,
            detail=final_result.dict(),
        )

    except asyncio.TimeoutError:
        msg = "Scan timed out (5-minute limit exceeded)"
        log("ERROR", msg)
        _store_error(
            job_id, repo_url, owner, repo, msg, time.monotonic() - start_time, app_state
        )
        await push_event(ScanStep.ERROR, msg, 0)

    except Exception as exc:
        msg = str(exc)
        log("ERROR", f"Unhandled exception: {msg}")
        _store_error(
            job_id, repo_url, owner, repo, msg, time.monotonic() - start_time, app_state
        )
        await push_event(ScanStep.ERROR, f"Scan failed: {msg}", 0)

    finally:
        if sandbox_id:
            log("CLEANUP", f"Destroying sandbox: {sandbox_id}")
            await destroy_sandbox(sandbox_id)
        sse_manager.cleanup_expired()
        log("CLEANUP", "Pipeline teardown complete")


def _store_error(
    job_id: str,
    repo_url: str,
    owner: str,
    repo: str,
    error_msg: str,
    duration: float,
    app_state,
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
    if app_state.use_redis and app_state.redis_client:
        try:
            app_state.redis_client.set(
                f"repo_scan:status:{job_id}", ScanStep.ERROR.value, ex=3600
            )
            app_state.redis_client.set(
                f"repo_scan:result:{job_id}", result.json(), ex=3600
            )
        except Exception as e:
            print(f"[RepoScanner] Redis error save error: {e}")


# ─────────────────────────────────────────────
# Router Factory
# ─────────────────────────────────────────────


def get_repo_scan_router(app_state, validate_token: Callable) -> APIRouter:
    """
    Build and return the repo-scan APIRouter, injecting shared state
    and the validate_token dependency — same pattern as get_health_router().
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
        """
        owner, repo = await validate_github_repo(req.repo_url)

        job_id = str(uuid.uuid4())
        sse_manager.create_job(job_id, req.repo_url)

        # 1. Local event push
        await sse_manager.push(
            job_id, ScanStep.QUEUED, "Job queued — awaiting sandbox...", 5
        )

        # 2. Redis status and initial event sync (cluster-wide visibility)
        if app_state.use_redis and app_state.redis_client:
            try:
                from .models import ScanEvent

                event = ScanEvent(
                    job_id=job_id,
                    step=ScanStep.QUEUED,
                    message="Job queued — awaiting sandbox...",
                    progress=5,
                )
                app_state.redis_client.publish(f"repo_scan:chan:{job_id}", event.json())
                app_state.redis_client.set(
                    f"repo_scan:status:{job_id}", ScanStep.QUEUED.value, ex=3600
                )
            except Exception as e:
                print(f"[RepoScanner] Redis queued save error: {e}")

        background_tasks.add_task(
            _run_scan_pipeline,
            job_id,
            req.repo_url,
            owner,
            repo,
            app_state,
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
        Works across clusters using Redis Pub/Sub as fallback.
        """
        # Scenario A: Local pod created this job
        job = sse_manager.get_job(job_id)
        if job is not None:
            return StreamingResponse(
                sse_manager.stream(job_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        # Scenario B: Another replica pod in cluster handles the job — use Redis Pub/Sub fallback
        if app_state.use_redis and app_state.redis_client:

            async def stream_redis_pubsub() -> AsyncIterator[str]:
                # Send bootstrapping update if terminal state is already reached
                status_bytes = app_state.redis_client.get(f"repo_scan:status:{job_id}")
                if status_bytes:
                    status_str = (
                        status_bytes.decode("utf-8")
                        if isinstance(status_bytes, bytes)
                        else str(status_bytes)
                    )
                    if status_str in ("DONE", "ERROR"):
                        res_bytes = app_state.redis_client.get(
                            f"repo_scan:result:{job_id}"
                        )
                        if res_bytes:
                            try:
                                res_json = json.loads(res_bytes)
                                from .models import ScanEvent

                                bootstrap_event = ScanEvent(
                                    job_id=job_id,
                                    step=ScanStep(status_str),
                                    message="Scan complete (loaded from cluster cache)",
                                    progress=100 if status_str == "DONE" else 0,
                                    detail=res_json,
                                )
                                yield f"data: {bootstrap_event.json()}\n\n"
                                return
                            except Exception:
                                pass

                # Subscribe to the job channel
                pubsub = app_state.redis_client.pubsub()
                pubsub.subscribe(f"repo_scan:chan:{job_id}")

                try:
                    while True:
                        msg = pubsub.get_message(ignore_subscribe_messages=True)
                        if msg:
                            data_str = (
                                msg["data"].decode("utf-8")
                                if isinstance(msg["data"], bytes)
                                else str(msg["data"])
                            )
                            yield f"data: {data_str}\n\n"
                            try:
                                ev_dict = json.loads(data_str)
                                if ev_dict.get("step") in ("DONE", "ERROR"):
                                    break
                            except Exception:
                                pass
                        await asyncio.sleep(0.5)
                finally:
                    pubsub.unsubscribe(f"repo_scan:chan:{job_id}")
                    pubsub.close()

            return StreamingResponse(
                stream_redis_pubsub(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

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
        Returns the complete scan result once the job reaches DONE or ERROR.
        Resolves via cluster Redis cache fallback if processed on another node.
        """
        # 1. Local pod check
        job = sse_manager.get_job(job_id)
        if job and job.result:
            return job.result

        # 2. Redis cache fallback
        if app_state.use_redis and app_state.redis_client:
            res_bytes = app_state.redis_client.get(f"repo_scan:result:{job_id}")
            if res_bytes:
                try:
                    return RepoScanResult.parse_raw(res_bytes)
                except Exception as e:
                    print(f"[RepoScanner] Error parsing result from Redis: {e}")

            # If result not in Redis yet, check if the job is still active
            status_bytes = app_state.redis_client.get(f"repo_scan:status:{job_id}")
            if status_bytes:
                status_str = (
                    status_bytes.decode("utf-8")
                    if isinstance(status_bytes, bytes)
                    else str(status_bytes)
                )
                if status_str not in ("DONE", "ERROR"):
                    raise HTTPException(
                        status_code=404,
                        detail=f"Result not ready yet. Scan is currently in step: {status_str}",
                    )

        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return router
