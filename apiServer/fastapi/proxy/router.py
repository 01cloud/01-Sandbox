from __future__ import annotations

import asyncio
import json
import uuid
from typing import Callable

import httpx
from config import opensandbox_base_url, opensandbox_headers
from core.docs import render_swagger_ui
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import JSONResponse, StreamingResponse
from sandboxes.models import ScanJobRequest, ScanJobResponse


async def check_backend_subscription(backend_id: str, user_data: dict, state):
    user_id = user_data.get("sub")
    if not user_id:
        return

    b_id_upper = backend_id.upper()
    if b_id_upper.replace("_", "").replace("-", "") in (
        "01SBX",
        "Z1SANDBOX",
        "OPENSANDBOX",
        "Z1_SANDBOX",
    ):
        b_id_normalized = "Z1_SANDBOX"
    else:
        b_id_normalized = b_id_upper

    # 1. Scope restriction for API keys
    token_backend = user_data.get("backend")
    if token_backend:
        token_b_upper = token_backend.upper()
        if token_b_upper.replace("_", "").replace("-", "") in (
            "01SBX",
            "Z1SANDBOX",
            "OPENSANDBOX",
            "Z1_SANDBOX",
        ):
            token_b_normalized = "Z1_SANDBOX"
        else:
            token_b_normalized = token_b_upper

        if token_b_normalized != b_id_normalized:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API Key is scoped to backend '{token_backend}', but requested '{backend_id}'.",
            )

    # 2. Universal accessibility check for the default sandbox
    if b_id_normalized == "Z1_SANDBOX":
        return

    # 3. Active subscription database validation
    conn = state.get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status FROM user_subscriptions WHERE LOWER(user_id) = LOWER(%s) AND backend_id = %s",
        (user_id, b_id_normalized),
    )
    row = cursor.fetchone()
    conn.close()

    if not row or row[0] != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Subscription to backend '{backend_id}' is required.",
        )


def get_proxy_router(state, validate_token: Callable) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/{backend_id}/docs",
        include_in_schema=False,
        dependencies=[Depends(validate_token)],
    )
    async def get_backend_docs(backend_id: str):
        """
        Renders actual upstream OpenSandbox Swagger API with custom authentication logic.
        """
        return render_swagger_ui(
            f"/api/{backend_id}/openapi.json", f"{backend_id.upper()} — Remote API Docs"
        )

    @router.get(
        "/api/{backend_id}/openapi.json",
        include_in_schema=False,
        dependencies=[Depends(validate_token)],
    )
    async def get_backend_openapi_spec(backend_id: str):
        """Translates and patches explicitly upstream OpenAPI spec."""
        base_url = opensandbox_base_url(backend_id)

        for spec_path in ["/openapi.json", "/v1/openapi.json", "/docs/openapi.json"]:
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    r = await client.get(f"{base_url}{spec_path}")
                    if r.status_code == 200:
                        spec = r.json()
                        spec["servers"] = [{"url": f"/api/{backend_id}"}]
                        spec.setdefault("components", {})
                        spec["components"].setdefault("securitySchemes", {})
                        spec["components"]["securitySchemes"]["BearerAuth"] = {
                            "type": "apiKey",
                            "name": "Authorization",
                            "in": "header",
                            "description": "Automatically populated via session binding.",
                        }
                        spec["security"] = [{"BearerAuth": []}]
                        return JSONResponse(content=spec)
            except Exception:
                continue

        raise HTTPException(
            status_code=404,
            detail=f"Target upstream openapi.json not found on {base_url} for backend {backend_id}",
        )

    async def _do_proxy(backend_id: str, proxy_path: str, request: Request):
        """Internal proxy routing logic forwarding transparently upstream."""
        base_url = opensandbox_base_url(backend_id)
        normalized_path = proxy_path if proxy_path.startswith("/") else f"/{proxy_path}"
        target_url = f"{base_url.rstrip('/')}{normalized_path}"

        params = dict(request.query_params)
        body = await request.body()

        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ["host", "content-length"]
        }

        # Auto-inject OpenSandbox authorization safely
        headers.update(opensandbox_headers())

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.request(
                    method=request.method,
                    url=target_url,
                    params=params,
                    content=body,
                    headers=headers,
                    timeout=900.0,
                )
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    headers={
                        k: v
                        for k, v in resp.headers.items()
                        if k.lower() not in ["content-encoding", "transfer-encoding"]
                    },
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Proxy routing failed targeting {target_url}: {type(exc).__name__} - {exc}",
                )

    def start_log_polling(job_id: str):
        import asyncio

        done_event = asyncio.Event()

        async def poll_logs():
            last_offset = 0
            # Give the sandbox a brief moment to boot up and generate log entries
            await asyncio.sleep(2.0)
            while not done_event.is_set():
                try:
                    loop = asyncio.get_running_loop()
                    status_res = await loop.run_in_executor(
                        None, state.backend.get_scan_status, job_id
                    )
                    if isinstance(status_res, str) and status_res:
                        if len(status_res) > last_offset:
                            new_content = status_res[last_offset:]
                            last_offset = len(status_res)
                            for line in new_content.splitlines():
                                if line.strip():
                                    await state.job_tracker.push_event(
                                        job_id,
                                        "LOG_LINE",
                                        line,
                                        60,
                                    )
                except Exception:
                    pass
                await asyncio.sleep(1.0)

        poller_task = asyncio.create_task(poll_logs())
        return done_event, poller_task

    async def stop_log_polling(done_event, poller_task):
        import asyncio

        done_event.set()
        poller_task.cancel()
        try:
            await poller_task
        except asyncio.CancelledError:
            pass

    async def run_scan_in_background(job_id: str, req_dict: dict):
        """Background worker — mirrors sandboxes router's full job-tracker lifecycle."""
        import asyncio

        try:
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
            done_event, poller_task = start_log_polling(job_id)
            try:
                data = await state.backend.create_scan_job(req_dict)
            finally:
                await stop_log_polling(done_event, poller_task)

            critical_count = high_count = medium_count = low_count = info_count = 0
            findings = data.get("findings", [])
            for f in findings:
                sev = str(f.get("severity", "INFO")).upper()
                if "CRITICAL" in sev:
                    critical_count += 1
                elif "HIGH" in sev:
                    high_count += 1
                elif "MEDIUM" in sev:
                    medium_count += 1
                elif "LOW" in sev:
                    low_count += 1
                elif "INFO" in sev:
                    info_count += 1

            job_record = state.job_tracker.get_job(job_id)
            if job_record:
                job_record.metadata["summary"] = {
                    "critical": critical_count,
                    "high": high_count,
                    "medium": medium_count,
                    "low": low_count,
                    "info": info_count,
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
            detail_dict["critical_count"] = critical_count
            detail_dict["high_count"] = high_count
            detail_dict["medium_count"] = medium_count
            detail_dict["low_count"] = low_count
            detail_dict["info_count"] = info_count

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
        "/api/{version}/{backend_id}/scan-jobs",
        response_model=ScanJobResponse,
        tags=["Security Scan Pipeline"],
        dependencies=[Depends(validate_token)],
    )
    async def create_scan_job_alias(
        version: str,
        backend_id: str,
        req: ScanJobRequest,
        background_tasks: BackgroundTasks,
        is_async: bool = Query(False, alias="async"),
        user_data: dict = Depends(validate_token),
    ):
        """
        Intercepts proxy requests to scan-jobs to enforce background async execution
        with full job-tracker lifecycle (QUEUED → PROVISIONING → SCANNING → DONE).
        """
        await check_backend_subscription(backend_id, user_data, state)
        import datetime

        job_id = str(uuid.uuid4())
        state.latest_job_id = job_id

        if req.metadata is None:
            req.metadata = {}
        req.metadata["job_id"] = job_id

        submitted_at = datetime.datetime.now(datetime.UTC).isoformat()
        user_id = user_data.get("sub")

        # Register the job in the tracker so SSE / result endpoints work
        state.job_tracker.create_job(
            job_id,
            "quick-scan",
            {
                "submitted_at": submitted_at,
                "files_count": len(req.files) if req.files else 0,
                "user_id": user_id,
            },
        )
        await state.job_tracker.push_event(
            job_id, "QUEUED", "Job queued — preparing files...", 5
        )

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
                done_event, poller_task = start_log_polling(job_id)
                try:
                    data = await state.backend.create_scan_job(
                        req.dict(exclude_none=True)
                    )
                finally:
                    await stop_log_polling(done_event, poller_task)

                critical_count = 0
                high_count = 0
                medium_count = 0
                low_count = 0
                info_count = 0
                findings = data.get("findings", [])
                for f in findings:
                    sev = str(f.get("severity", "INFO")).upper()
                    if "CRITICAL" in sev:
                        critical_count += 1
                    elif "HIGH" in sev:
                        high_count += 1
                    elif "MEDIUM" in sev:
                        medium_count += 1
                    elif "LOW" in sev:
                        low_count += 1
                    elif "INFO" in sev:
                        info_count += 1

                job_record = state.job_tracker.get_job(job_id)
                if job_record:
                    job_record.metadata["summary"] = {
                        "critical": critical_count,
                        "high": high_count,
                        "medium": medium_count,
                        "low": low_count,
                        "info": info_count,
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
                detail_dict["critical_count"] = critical_count
                detail_dict["high_count"] = high_count
                detail_dict["medium_count"] = medium_count
                detail_dict["low_count"] = low_count
                detail_dict["info_count"] = info_count

                await state.job_tracker.push_event(
                    job_id,
                    "DONE",
                    f"Scan complete — found {len(findings)} security findings.",
                    100,
                    detail=detail_dict,
                )
                return ScanJobResponse(**data)
            except Exception as e:
                print(f"[PROXY SCAN ERROR] Synchronous scan job {job_id} failed: {e}")
                await state.job_tracker.push_event(
                    job_id, "ERROR", f"Scan failed: {e}", 0
                )
                raise HTTPException(status_code=500, detail=str(e))

    @router.get(
        "/api/{version}/{backend_id}/v1/jobs/{job_id}/status",
        tags=["Generic Jobs Infrastructure"],
        dependencies=[Depends(validate_token)],
    )
    async def proxy_job_status_stream(
        version: str, backend_id: str, job_id: str, since: int = 0
    ):
        """
        Alias for the generic jobs SSE stream, intercepted before the catch-all
        proxy forwards it upstream. Streams live events from the local job tracker.
        """
        return StreamingResponse(
            state.job_tracker.stream(job_id, since_index=since),
            media_type="text/event-stream",
        )

    @router.get(
        "/api/{version}/{backend_id}/v1/jobs/{job_id}/result",
        tags=["Generic Jobs Infrastructure"],
        dependencies=[Depends(validate_token)],
    )
    async def proxy_job_result(version: str, backend_id: str, job_id: str):
        """
        Alias for the generic jobs result endpoint, intercepted before the catch-all
        proxy forwards it upstream. Returns the completed scan result from local memory,
        Redis, or the PVC report.
        """
        # 1. In-memory active job result
        job = state.job_tracker.get_job(job_id)
        if job and job.result:
            return job.result

        # 2. Redis cached result
        if state.use_redis and state.redis_client:
            cached = state.redis_client.get(f"job:{job_id}:result")
            if cached:
                return json.loads(cached)

        # 3. PVC report via backend
        try:
            report = state.backend.get_scan_report(job_id)
            if report:
                return report
        except Exception as e:
            print(f"[proxy alias] PVC report retrieval failed for {job_id}: {e}")

        raise HTTPException(
            status_code=404,
            detail="Scan result not found or job still running.",
        )

    @router.delete(
        "/api/{version}/{backend_id}/v1/jobs/{job_id}",
        tags=["Generic Jobs Infrastructure"],
        dependencies=[Depends(validate_token)],
        summary="Cancel a queued or running scan job, or permanently purge a completed job (via proxy)",
    )
    async def proxy_cancel_or_delete_job(
        version: str, backend_id: str, job_id: str, purge: bool = Query(False)
    ):
        from core.queue import is_available, publish

        if not is_available():
            raise HTTPException(
                status_code=503,
                detail="RabbitMQ service is unavailable. Cannot process job deletion.",
            )

        payload = {"job_id": job_id, "purge": purge}
        await publish("scan.delete", payload)

        return {"job_id": job_id, "status": "DELETE_QUEUED"}

    @router.get(
        "/api/{version}/{backend_id}/jobs",
        tags=["Generic Jobs Infrastructure"],
        summary="List jobs by type (intercepted, API-key auth)",
        dependencies=[Depends(validate_token)],
    )
    async def list_jobs_via_proxy(
        version: str,
        backend_id: str,
        job_type: str = Query(
            ..., description="Filter by job type: quick-scan | repo-scan"
        ),
    ):
        """
        Intercepted before the catch-all proxy. Lists all jobs of the given type from
        the in-memory job tracker and Redis. Enables the UI to discover jobs that were
        triggered externally (e.g. via CLI/API) rather than through the browser.

        Mirrors the logic in core/jobs/router.py but accepts API-key Bearer auth so
        SecurityScanner and RepoScanner components can reach it without an Auth0 JWT.
        """
        import json as _json

        jobs = []
        seen_ids: set = set()

        stale_ids = []
        for jid, job in state.job_tracker._jobs.items():
            if job.job_type != job_type:
                continue

            # Sync with Redis if it has completed elsewhere
            redis_status = None
            if state.use_redis and state.redis_client:
                try:
                    r_val = state.redis_client.get(f"job:{jid}:status")
                    if r_val:
                        redis_status = (
                            r_val.decode() if isinstance(r_val, bytes) else r_val
                        )
                except Exception:
                    pass

            if redis_status in ("DONE", "ERROR"):
                stale_ids.append(jid)
                continue

            job_status = redis_status if redis_status else job.step

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
            seen_ids.add(jid)

        # Clean up stale local references
        for jid in stale_ids:
            if jid in state.job_tracker._jobs:
                del state.job_tracker._jobs[jid]

        # ── 2. Redis historical jobs (crash recovery / multi-pod) ───────────
        if state.use_redis and state.redis_client:
            r = state.redis_client
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
                    if metadata.get("job_type") != job_type:
                        continue

                    status_val = r.get(key)
                    status = (
                        status_val.decode()
                        if isinstance(status_val, bytes)
                        else status_val
                    )
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
                            "job_type": job_type,
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
                print(f"[proxy list_jobs] Redis error: {exc}")

        # Return newest first
        jobs.sort(key=lambda j: j.get("submittedAt", ""), reverse=True)
        return jobs

    @router.api_route(
        "/api/{version}/{backend_id}/{proxy_path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        tags=["Proxy Backend"],
        summary="Dynamic Versioned Proxy Request",
    )
    async def dynamic_versioned_proxy(
        version: str,
        backend_id: str,
        proxy_path: str,
        request: Request,
        user_data: dict = Depends(validate_token),
    ):
        """
        Catch-all for URLs like /api/v1/01sbx/scan-jobs
        Funnels directly to the backend while preserving the full path.
        """
        await check_backend_subscription(backend_id, user_data, state)
        full_proxy_path = f"/api/{version}/{backend_id}/{proxy_path}"
        return await _do_proxy(backend_id, full_proxy_path, request)

    @router.api_route(
        "/api/{backend_id}/{proxy_path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        tags=["Proxy Backend"],
        summary="Legacy Dynamic Proxy Request",
    )
    async def dynamic_proxy(
        backend_id: str,
        proxy_path: str,
        request: Request,
        user_data: dict = Depends(validate_token),
    ):
        """
        Legacy support for /api/z1sandbox/docs style URLs
        """
        await check_backend_subscription(backend_id, user_data, state)
        full_proxy_path = f"/api/{backend_id}/{proxy_path}"
        return await _do_proxy(backend_id, full_proxy_path, request)

    return router
