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

from .file_scanner import (
    active_child_jobs_by_parent,
    cleanup_child_jobs,
    current_parent_job_id,
    scan_language,
)
from .github_validator import parse_github_url, validate_github_repo
from .language_detector import detect_languages
from .models import RepoScanRequest, RepoScanResult, RepoScanSubmitResponse, ScanStep
from .sandbox_provisioner import clone_repo, destroy_sandbox, provision_sandbox

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
    token = current_parent_job_id.set(job_id)
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
        step_val = step.value if isinstance(step, ScanStep) else step
        await app_state.job_tracker.push_event(
            job_id, step_val, message, progress, detail
        )

    log("INIT", f"Pipeline started for {owner}/{repo} (job={job_id})")
    log("INIT", f"Repository URL: {repo_url}")
    log("INIT", f"Redis enabled: {app_state.use_redis}")

    print("\n[INFO] Repository scan started")
    print(f"Repository: {repo_url}\n")

    # Simulation check for retry/DLQ testing
    if (
        "simulate_retry" in repo_url.lower()
        or "simulate_error" in repo_url.lower()
        or owner.lower() == "simulate"
    ):
        raise ValueError(
            "Simulated repository scan processing error for retry/DLQ testing"
        )

    try:
        # ── Step 0: Validate Repository Accessibility ────────────────
        log(
            "VALIDATING", f"Verifying accessibility of {owner}/{repo} via GitHub API..."
        )
        await push_event(
            ScanStep.PROVISIONING,
            f"Verifying accessibility of {owner}/{repo} via GitHub API...",
            5,
        )
        try:
            await validate_github_repo(repo_url)
        except HTTPException as he:
            raise RuntimeError(he.detail)

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

        print("\n[INFO] Language detection completed")
        print("Languages detected:")
        for lang, files in sorted(
            lang_map.items(), key=lambda x: len(x[1]), reverse=True
        ):
            pct_val = (len(files) / max(total_files, 1)) * 100.0
            print(f"- {lang}: {pct_val:.0f}%")
        print()

        print("[INFO] Triggering security tools\n")

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
            scan_output = await asyncio.wait_for(
                scan_language(
                    sandbox_id=sandbox_id,
                    language=language,
                    files=files,
                    percentage=pct(len(files)),
                ),
                timeout=180.0,
            )

            # YAML returns a tuple (plain_result, optional k8s_result)
            # All other languages return a single LanguageScanResult
            if isinstance(scan_output, tuple):
                yaml_result, k8s_result = scan_output
                language_results["YAML"] = yaml_result
                log(
                    "SCANNING",
                    f"[{idx+1}/{total_langs}] YAML (plain) complete — {yaml_result.file_count} file(s), "
                    f"{yaml_result.lines_of_code} LoC, {len(yaml_result.findings)} finding(s)",
                )
                if k8s_result is not None:
                    language_results["Kubernetes YAML"] = k8s_result
                    log(
                        "SCANNING",
                        f"[{idx+1}/{total_langs}] Kubernetes YAML complete — {k8s_result.file_count} manifest(s), "
                        f"{k8s_result.lines_of_code} LoC, {len(k8s_result.findings)} finding(s)",
                    )
                else:
                    log(
                        "SCANNING",
                        f"[{idx+1}/{total_langs}] No K8s manifests found — Kubernetes YAML section skipped",
                    )
            else:
                result = scan_output
                language_results[language] = result
                lang_findings = len(result.findings)
                log(
                    "SCANNING",
                    f"[{idx+1}/{total_langs}] {language} complete — {result.lines_of_code} LoC, {lang_findings} finding(s)",
                )
                for f in result.findings[:5]:
                    log(
                        "SCANNING",
                        f"  [{f.severity}] {f.file}:{f.line or '?'} — {f.issue[:80]} (tool={f.tool})",
                    )
                if lang_findings > 5:
                    log("SCANNING", f"  ... and {lang_findings - 5} more finding(s)")

        # ── Step 4.5: Redistribute and Deduplicate Findings ──────────
        # Gather all raw (unfiltered) findings from all language scans
        all_raw_findings = []
        for r in language_results.values():
            if hasattr(r, "raw_findings") and r.raw_findings:
                all_raw_findings.extend(r.raw_findings)

        # Deduplicate all raw findings to ensure each finding is recorded exactly once
        seen = set()
        deduped_findings = []
        for f in all_raw_findings:
            # Normalise file path for keying
            file_normalized = (f.file or "").strip()
            # Construct a unique key
            key = (f.severity, file_normalized, f.line, f.issue, f.tool)
            if key not in seen:
                seen.add(key)
                deduped_findings.append(f)

        # Clear existing language findings so we can populate them cleanly
        for r in language_results.values():
            r.findings = []

        # Helper to map a file path to its matching language section
        def get_target_language(file_path: str, tool_name: str) -> str:
            import os

            normalized = (file_path or "").strip()
            for prefix in ("/workspace/", "workspace/", "./"):
                if normalized.startswith(prefix):
                    normalized = normalized[len(prefix) :]
                    break

            ext = os.path.splitext(normalized)[1].lower()
            base = os.path.basename(normalized).lower()

            if ext == ".py":
                return "Python"
            elif ext == ".go" or base in ("go.mod", "go.sum", "go.work"):
                return "Go"
            elif ext in (".js", ".jsx") or base in (
                "package.json",
                "package-lock.json",
                "yarn.lock",
            ):
                if "JavaScript" in language_results:
                    return "JavaScript"
                if "TypeScript" in language_results:
                    return "TypeScript"
                return "JavaScript"
            elif ext in (".ts", ".tsx") or base in (
                "tsconfig.json",
                "tsconfig.node.json",
            ):
                if "TypeScript" in language_results:
                    return "TypeScript"
                if "JavaScript" in language_results:
                    return "JavaScript"
                return "TypeScript"
            elif ext in (".sh", ".bash"):
                for possible in ("Shell", "Bash", "ShellScript"):
                    if possible in language_results:
                        return possible
                return "Shell"
            elif ext in (".yaml", ".yml"):
                is_k8s_finding = any(
                    t in str(tool_name).lower()
                    for t in ("kubelinter", "kubeconform", "kubescore")
                )
                if is_k8s_finding or (
                    "Kubernetes YAML" in language_results
                    and "YAML" not in language_results
                ):
                    if "Kubernetes YAML" in language_results:
                        return "Kubernetes YAML"
                return "YAML"
            elif ext == ".rb" or base in ("gemfile", "gemfile.lock"):
                return "Ruby"
            elif ext == ".java" or base in ("pom.xml", "build.gradle"):
                return "Java"

            # Fallback based on extension matching other detected languages
            for lang in language_results.keys():
                if lang.lower() in normalized.lower():
                    return lang

            # If it's a global secret, manifest, or not specific to a code language
            return "Secrets & Infrastructure"

        # Distribute each finding to the correct language section
        for f in deduped_findings:
            target_lang = get_target_language(f.file, f.tool)
            if target_lang not in language_results:
                from .models import LanguageScanResult

                language_results[target_lang] = LanguageScanResult(
                    language=target_lang,
                    file_count=0,
                    lines_of_code=0,
                    percentage=0.0,
                    findings=[],
                )
            language_results[target_lang].findings.append(f)

        # Log clean-up summary of redistribution
        for lang, r in language_results.items():
            log(
                "SCANNING",
                f"Redistributed findings for {lang}: {len(r.findings)} finding(s)",
            )

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

        # Count severities
        high_count = 0
        medium_count = 0
        low_count = 0
        for r in language_results.values():
            for f in r.findings:
                sev = str(f.severity).upper()
                if "HIGH" in sev:
                    high_count += 1
                elif "MEDIUM" in sev:
                    medium_count += 1
                elif "LOW" in sev:
                    low_count += 1

        detail_dict = final_result.dict()
        detail_dict["high_count"] = high_count
        detail_dict["medium_count"] = medium_count
        detail_dict["low_count"] = low_count

        # Save summary count to job tracker metadata
        job_record = app_state.job_tracker.get_job(job_id)
        if job_record:
            job_record.metadata["summary"] = {
                "high": high_count,
                "medium": medium_count,
                "low": low_count,
            }
            if app_state.use_redis and app_state.redis_client:
                try:
                    metadata_copy = dict(job_record.metadata)
                    metadata_copy["job_type"] = "repo-scan"
                    app_state.redis_client.set(
                        f"job:{job_id}:metadata", json.dumps(metadata_copy), ex=86400
                    )
                except Exception as e:
                    print(f"[RepoScanner] Redis metadata update error: {e}")

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
            detail=detail_dict,
        )

    except asyncio.CancelledError:
        log("CANCELLED", "Job was cancelled by the user")
        raise

    except asyncio.TimeoutError as exc:
        msg = "Scan timed out (5-minute limit exceeded)"
        log("ERROR", msg)
        result = RepoScanResult(
            job_id=job_id,
            repo_url=repo_url,
            owner=owner,
            repo=repo,
            status=ScanStep.ERROR,
            error=msg,
            scan_duration_seconds=round(time.monotonic() - start_time, 2),
        )
        await push_event(ScanStep.ERROR, msg, 0, detail=result.dict())
        raise exc

    except Exception as exc:
        msg = str(exc)
        log("ERROR", f"Unhandled exception: {msg}")
        result = RepoScanResult(
            job_id=job_id,
            repo_url=repo_url,
            owner=owner,
            repo=repo,
            status=ScanStep.ERROR,
            error=msg,
            scan_duration_seconds=round(time.monotonic() - start_time, 2),
        )
        await push_event(ScanStep.ERROR, f"Scan failed: {msg}", 0, detail=result.dict())
        raise exc

    finally:
        if sandbox_id:
            log("CLEANUP", f"Destroying sandbox: {sandbox_id}")
            await destroy_sandbox(sandbox_id)

        # Clean up any active/dangling child scan jobs on the server
        child_jobs = active_child_jobs_by_parent.pop(job_id, set())
        if child_jobs:
            log("CLEANUP", f"Cleaning up dangling child jobs on server: {child_jobs}")
            await asyncio.shield(cleanup_child_jobs(child_jobs))

        current_parent_job_id.reset(token)
        log("CLEANUP", "Pipeline teardown complete")


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
        owner, repo = parse_github_url(req.repo_url)

        job_id = str(uuid.uuid4())

        import datetime

        submitted_at = datetime.datetime.now(datetime.UTC).isoformat()

        app_state.job_tracker.create_job(
            job_id,
            "repo-scan",
            {"repo_url": req.repo_url, "submitted_at": submitted_at},
        )

        await app_state.job_tracker.push_event(
            job_id, ScanStep.QUEUED.value, "Job queued — awaiting sandbox...", 5
        )

        from core.queue import REPO_SCAN, is_available, publish

        if is_available():
            await publish(
                REPO_SCAN.routing_key,
                {
                    "job_id": job_id,
                    "repo_url": req.repo_url,
                    "owner": owner,
                    "repo": repo,
                },
            )
        else:
            background_tasks.add_task(
                _run_scan_pipeline,
                job_id,
                req.repo_url,
                owner,
                repo,
                app_state,
            )
        return RepoScanSubmitResponse(
            job_id=job_id,
            status=ScanStep.QUEUED,
            status_url=f"/v1/repo-scan/{job_id}/status",
            result_url=f"/v1/repo-scan/{job_id}/result",
            repo_url=req.repo_url,
            submitted_at=submitted_at,
        )

    # ── GET /v1/repo-scan/{job_id}/status (SSE) ────────────────────────
    @router.get(
        "/v1/repo-scan/{job_id}/status",
        tags=["Repo Scanner"],
        summary="Stream live scan progress via Server-Sent Events",
        dependencies=[Depends(validate_token)],
    )
    async def stream_scan_status(job_id: str, since: int = 0) -> StreamingResponse:
        """
        SSE endpoint streaming scan step events in real-time.
        Accepts an optional ``since`` query parameter to resume from a specific
        event index (avoids replaying the full log on reconnect).
        """
        return StreamingResponse(
            app_state.job_tracker.stream(job_id, since_index=since),
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
        Returns the complete scan result once the job reaches DONE or ERROR.
        Resolves via cluster Redis cache fallback if processed on another node.
        """
        job = app_state.job_tracker.get_job(job_id)
        if job and job.result:
            return job.result

        if app_state.use_redis and app_state.redis_client:
            res_str = app_state.redis_client.get(f"job:{job_id}:result")
            if res_str:
                try:
                    return RepoScanResult.parse_raw(res_str)
                except Exception as e:
                    print(f"[RepoScanner] Error parsing result from Redis: {e}")

            status = app_state.redis_client.get(f"job:{job_id}:status")
            if status and status not in ("DONE", "ERROR"):
                raise HTTPException(
                    status_code=404,
                    detail=f"Result not ready yet. Scan is currently in step: {status}",
                )

        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    # ── GET /v1/repo-scan/jobs ──────────────────────────────────────────────
    @router.get(
        "/v1/repo-scan/jobs",
        tags=["Repo Scanner"],
        summary="List all repo-scan jobs — API-key auth (enables CLI→UI sync)",
        dependencies=[Depends(validate_token)],
    )
    async def list_repo_scan_jobs():
        """
        Lists all repo-scan jobs from in-memory tracker and Redis.
        Uses API-key Bearer authentication — same as POST /v1/repo-scan.

        The frontend polls this endpoint every 5 seconds so that scans triggered
        externally (via CLI or API) automatically appear in the UI with live
        real-time status and report rendering, without requiring the user to
        submit through the browser.
        """
        import json as _json

        jobs = []
        seen_ids: set = set()

        # ── 1. In-memory active jobs ───────────────────────────────────────
        for jid, job in app_state.job_tracker._jobs.items():
            if job.job_type != "repo-scan":
                continue
            jobs.append(
                {
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "status": job.step,
                    "progress": (
                        100
                        if job.step in ("DONE", "ERROR")
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
            seen_ids.add(jid)

        # ── 2. Redis historical jobs (multi-pod / crash recovery) ──────────
        if app_state.use_redis and app_state.redis_client:
            r = app_state.redis_client
            try:
                status_keys = r.keys("job:*:status")
                for key in status_keys:
                    parts = key.split(":")
                    if len(parts) < 3:
                        continue
                    jid = parts[1]
                    if jid in seen_ids:
                        continue

                    metadata_str = r.get(f"job:{jid}:metadata")
                    if not metadata_str:
                        continue

                    metadata = _json.loads(metadata_str)
                    if metadata.get("job_type") != "repo-scan":
                        continue

                    status = r.get(key)
                    events_len = r.llen(f"job:{jid}:events")

                    last_msg = ""
                    last_ev_str = r.lindex(f"job:{jid}:events", -1)
                    if last_ev_str:
                        try:
                            last_msg = _json.loads(last_ev_str).get("message", "")
                        except Exception:
                            pass

                    jobs.append(
                        {
                            "job_id": jid,
                            "job_type": "repo-scan",
                            "status": status,
                            "progress": 100 if status in ("DONE", "ERROR") else 10,
                            "stepMessage": last_msg,
                            "eventIndex": events_len,
                            "metadata": metadata,
                            "summary": metadata.get("summary"),
                            "result": None,
                            "submittedAt": metadata.get("submitted_at", ""),
                            "completedAt": metadata.get("completed_at", ""),
                        }
                    )
                    seen_ids.add(jid)
            except Exception as exc:
                print(f"[RepoScanner] list_repo_scan_jobs Redis error: {exc}")

        # Return newest first
        jobs.sort(key=lambda j: j.get("submittedAt", ""), reverse=True)
        return jobs

    return router
