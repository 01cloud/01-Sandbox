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
    ):
        """
        Intercepts proxy requests to scan-jobs to enforce background async execution
        with full job-tracker lifecycle (QUEUED → PROVISIONING → SCANNING → DONE).
        """
        import datetime

        job_id = str(uuid.uuid4())
        state.latest_job_id = job_id

        if req.metadata is None:
            req.metadata = {}
        req.metadata["job_id"] = job_id

        submitted_at = datetime.datetime.now(datetime.UTC).isoformat()

        # Register the job in the tracker so SSE / result endpoints work
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
        dependencies=[Depends(validate_token)],
    )
    async def dynamic_versioned_proxy(
        version: str, backend_id: str, proxy_path: str, request: Request
    ):
        """
        Catch-all for URLs like /api/v1/01sbx/scan-jobs
        Funnels directly to the backend while preserving the full path.
        """
        full_proxy_path = f"/api/{version}/{backend_id}/{proxy_path}"
        return await _do_proxy(backend_id, full_proxy_path, request)

    @router.api_route(
        "/api/{backend_id}/{proxy_path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        tags=["Proxy Backend"],
        summary="Legacy Dynamic Proxy Request",
        dependencies=[Depends(validate_token)],
    )
    async def dynamic_proxy(backend_id: str, proxy_path: str, request: Request):
        """
        Legacy support for /api/z1sandbox/docs style URLs
        """
        full_proxy_path = f"/api/{backend_id}/{proxy_path}"
        return await _do_proxy(backend_id, full_proxy_path, request)

    return router
