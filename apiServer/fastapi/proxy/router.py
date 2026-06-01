from __future__ import annotations

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
from fastapi.responses import JSONResponse
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

    async def run_scan_in_background(req_dict: dict):
        """Background worker to trigger scan without blocking HTTP requests."""
        try:
            await state.backend.create_scan_job(req_dict)
        except Exception as e:
            print(f"[BACKGROUND TASK ERROR] Scan job failed: {e}")

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
        Intercepts proxy requests to scan-jobs to enforce background async execution.
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
