# Copyright 2025 Alibaba Group Holding Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
API routes for OpenSandbox Lifecycle API.

This module defines FastAPI routes that map to the OpenAPI specification endpoints.
All business logic is delegated to the service layer that backs each operation.
"""

from typing import List, Optional, Any
import os
import re
import json
import base64
import asyncio
from uuid import uuid4
import httpx
from fastapi import APIRouter, Header, Query, Request, status, Body
from fastapi.exceptions import HTTPException
from fastapi.responses import Response, StreamingResponse, JSONResponse, FileResponse

from src.api.schema import (
    CreateSandboxRequest,
    CreateSandboxResponse,
    Endpoint,
    ErrorResponse,
    ListSandboxesRequest,
    ListSandboxesResponse,
    PaginationRequest,
    RenewSandboxExpirationRequest,
    RenewSandboxExpirationResponse,
    Sandbox,
    SandboxFilter,
    ScanJobRequest,
    ScanJobResponse,
    ImageSpec,
    Volume,
    PVC,
    ResourceLimits as SchemaResourceLimits,
)
from src.services.factory import create_sandbox_service

# RFC 2616 Section 13.5.1
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

def detect_extension(content: str) -> str:
    """Detects the best file extension for a code snippet using heuristics."""
    if not content:
        return "py"
    
    # Check for JSON first (strict structure)
    stripped = content.strip()
    if (stripped.startswith("{") and stripped.endswith("}")) or (stripped.startswith("[") and stripped.endswith("]")):
        return "json"

    # Check for YAML
    if content.startswith("---") or re.search(r"^(apiVersion|metadata|version|services|spec):", content, re.MULTILINE):
        return "yaml"
    
    # Check for Go
    if content.startswith("package ") or "func main()" in content:
        return "go"

    # PRIORITIZE PYTHON: Check for Python-specific markers
    if re.search(r"^\s*(#|def |from .* import |if __name__ ==)", content, re.MULTILINE) or "print(" in content:
        return "py"
    
    # Check for Javascript
    if re.search(r"\b(const|let|var|function|console\.log)\s", content) or "require(" in content:
        return "js"
        
    return "py" # Default to Python

# Headers that shouldn't be forwarded to untrusted/internal backends
SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
}

# Initialize router
router = APIRouter(tags=["Sandboxes"])

# Initialize service based on configuration from config.toml (defaults to docker)
sandbox_service = create_sandbox_service()

# Track the most recently initiated scan job ID for instant retrieval
_latest_job_id = None


# ============================================================================
# Sandbox CRUD Operations
# ============================================================================

@router.post(
    "/sandboxes",
    response_model=CreateSandboxResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Sandbox creation accepted for asynchronous provisioning"},
        400: {"model": ErrorResponse, "description": "The request was invalid or malformed"},
        401: {"model": ErrorResponse, "description": "Authentication credentials are missing or invalid"},
        409: {"model": ErrorResponse, "description": "The operation conflicts with the current state"},
        500: {"model": ErrorResponse, "description": "An unexpected server error occurred"},
    },
)
async def create_sandbox(
    request: CreateSandboxRequest,
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID", description="Unique request identifier for tracing"),
) -> CreateSandboxResponse:
    """
    Create a sandbox from a container image.

    Creates a new sandbox from a container image with optional resource limits,
    environment variables, and metadata. Sandboxes are provisioned directly from
    the specified image without requiring a pre-created template.

    Args:
        request: Sandbox creation request
        x_request_id: Unique request identifier for tracing (optional; server generates if omitted).

    Returns:
        CreateSandboxResponse: Accepted sandbox creation request

    Raises:
        HTTPException: If sandbox creation scheduling fails
    """

    return sandbox_service.create_sandbox(request)


@router.post(
    "/scan-jobs",
    response_model=ScanJobResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        201: {"description": "Scan job successfully completed and report retrieved"},
        400: {"model": ErrorResponse, "description": "The request was invalid or malformed"},
        500: {"model": ErrorResponse, "description": "An unexpected server error occurred"},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "string",
                        "description": "A JSON ScanJobRequest or a raw Python code snippet.",
                        "example": "print('Hello, World!')"
                    }
                }
            }
        }
    }
)
async def create_scan_job(
    request: Request,
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID"),
) -> ScanJobResponse:
    """
    High-level API to submit a set of files for security scanning.
    
    This endpoint:
    1. Writes files to a shared PVC.
    2. Provisions a code-interpreter sandbox.
    3. Blocks synchronously until the scan finishes and returns the full JSON report.
    """
    import os
    import base64
    import json
    import asyncio
    from uuid import uuid4

    # Robust parsing: Try JSON first, fallback to raw text as main.py
    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8")
    
    scan_request = None
    try:
        # Check if it's a valid JSON object matching our schema
        data = json.loads(body_str)
        if isinstance(data, dict):
            scan_request = ScanJobRequest(**data)
    except Exception:
        # Fallback: create an empty request object if parsing fails
        scan_request = ScanJobRequest()

    # Check metadata for pre-generated job_id from gateway
    metadata = {}
    if scan_request and scan_request.metadata:
        metadata = scan_request.metadata
        
    job_id = metadata.get("job_id", str(uuid4()))
    metadata["job_id"] = job_id

    # Update latest job ID globally for instant retrieval from other tabs
    global _latest_job_id
    _latest_job_id = job_id

    # Base path for shared storage (mounted via PVC in Helm)
    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
    job_dir = os.path.join(data_root, job_id, "workspace")
    reports_dir = os.path.join(data_root, job_id, "reports")

    # Logic for auto-detecting language if using the simplified 'code' field
    # or if a generic filename like 'code' or 'main' is provided.
    files_to_save = {}
    if scan_request:
        if scan_request.code:
            ext = detect_extension(scan_request.code)
            files_to_save[f"main.{ext}"] = scan_request.code
        elif scan_request.files:
            for filename, content in scan_request.files.items():
                base_name = os.path.basename(filename)
                # If the filename has no extension or is generic, attempt to fix it
                if "." not in base_name or base_name.lower().startswith(("main", "code")):
                    ext = detect_extension(content)
                    name_without_ext = base_name.split(".")[0]
                    files_to_save[f"{name_without_ext}.{ext}"] = content
                else:
                    files_to_save[filename] = content
    
    # If no files have been identified yet, treat the entire body text
    if not files_to_save:
        ext = detect_extension(body_str)
        files_to_save[f"main.{ext}"] = body_str

    try:
        os.makedirs(job_dir, exist_ok=True)
        os.makedirs(reports_dir, exist_ok=True)
        
        for filename, content in files_to_save.items():
            safe_filename = os.path.basename(filename)
            file_path = os.path.join(job_dir, safe_filename)
            
            try:
                decoded_content = base64.b64decode(content, validate=True)
                with open(file_path, "wb") as f:
                    f.write(decoded_content)
            except Exception:
                with open(file_path, "w") as f:
                    f.write(content)
                    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "FILE_SYSTEM_ERROR", "message": f"Failed to write scan files: {str(e)}"}
        )

    sandbox_req = CreateSandboxRequest(
        image=ImageSpec(uri="codeinterpreter:3.1.0"),
        resourceLimits=SchemaResourceLimits(root={"cpu": "1", "memory": "2Gi"}),
        entrypoint=["/opt/opensandbox/code-interpreter.sh"],
        timeout=scan_request.timeout if scan_request and scan_request.timeout else 300,
        env={
            "SCAN_DIR": "/workspace", 
            "SCAN_REPORT": "/reports/security_scan_report.json",
            "SCAN_TOOLS": ",".join(scan_request.tools) if scan_request and scan_request.tools else ""
        },
        volumes=[
            Volume(
                name="workspace",
                pvc=PVC(claimName="scan-pvc"),
                mountPath="/workspace", 
                subPath=f"{job_id}/workspace"
            ),
            Volume(
                name="reports",
                pvc=PVC(claimName="scan-pvc"),
                mountPath="/reports", 
                subPath=f"{job_id}/reports"
            )
        ],
        metadata=metadata
    )

    created_sandbox = sandbox_service.create_sandbox(sandbox_req)
    sandbox_id = created_sandbox.id
    
    report_path = os.path.join(reports_dir, "security_scan_report.json")
    timeout_seconds = sandbox_req.timeout if sandbox_req.timeout else 300
    deadline = asyncio.get_event_loop().time() + timeout_seconds

    while asyncio.get_event_loop().time() < deadline:
        # 1. Check if the final report exists — return immediately when found
        if os.path.exists(report_path):
            try:
                with open(report_path, "r") as f:
                    report_data = json.load(f)
                return ScanJobResponse(
                    job_id=job_id,
                    sandbox_id=sandbox_id,
                    status="COMPLETED",
                    report=report_data
                )
            except (json.JSONDecodeError, OSError):
                pass # File may still be mid-write; retry next cycle

        # 2. Yield control to the async event loop (non-blocking wait)
        await asyncio.sleep(1)

        # 3. Check sandbox state for early terminal failures
        #    Run in executor so the blocking K8s API call doesn't freeze the event loop
        try:
            loop = asyncio.get_event_loop()
            sb = await loop.run_in_executor(None, sandbox_service.get_sandbox, sandbox_id)
            state = sb.status.state if sb.status else "Unknown"
            if state in ("Failed", "Terminated", "Stopped") and not os.path.exists(report_path):
                return ScanJobResponse(
                    job_id=job_id,
                    sandbox_id=sandbox_id,
                    status="FAILED",
                    error=f"Sandbox reached terminal state '{state}' before scan report was written."
                )
        except Exception:
            pass  # Ignore transient look-up errors; keep waiting

    # Deadline exceeded without a report
    return ScanJobResponse(
        job_id=job_id,
        sandbox_id=sandbox_id,
        status="FAILED",
        error="Scan timed out waiting for the report to be written to the PVC."
    )


@router.get("/scan-jobs/{job_id}/report", tags=["Security Scan Pipeline"])
async def get_scan_report(job_id: str):
    """
    Retrieves the persistent security scan report for a specific job ID.
    This report is stored on the PVC and lives beyond the sandbox lifecycle.
    """
    import os
    import json
    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
    report_path = os.path.join(data_root, job_id, "reports", "security_scan_report.json")
    
    if not os.path.exists(report_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "REPORT_NOT_FOUND", "message": f"No report found for job {job_id}. The scan may still be in progress."}
        )
        
    try:
        with open(report_path, "r") as f:
            content = json.load(f)
        return content
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "FILE_READ_ERROR", "message": str(e)}
        )


@router.get("/scan-jobs/{job_id}/workspace/{file_path:path}", tags=["Security Scan Pipeline"])
async def get_scan_source(job_id: str, file_path: str):
    """
    Retrieves a specific source file uploaded during a scan job.
    """
    import os
    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
    safe_file = os.path.basename(file_path) # Basic safety
    full_path = os.path.join(data_root, job_id, "workspace", safe_file)
    
    if not os.path.exists(full_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "FILE_NOT_FOUND", "message": f"Source file {file_path} not found for job {job_id}."}
        )
        
    return FileResponse(full_path)


@router.get("/scan-status/{job_id}", tags=["Security Scan Pipeline"])
async def get_scan_status(job_id: str):
    """
    Retrieves the current sandbox status for a given scan job ID.
    Looks up the active sandbox using the job_id injected into its metadata.
    """
    request = ListSandboxesRequest(
        filter=SandboxFilter(metadata={"job_id": job_id}),
        pagination=PaginationRequest(page=1, pageSize=1)
    )
    res = sandbox_service.list_sandboxes(request)
    
    if not res.items:
        return {"job_id": job_id, "status": "NOT_FOUND", "message": "No active sandbox found for this job ID. It may have been garbage collected, or never existed."}
        
    sandbox = res.items[0]
    return {
        "job_id": job_id,
        "sandbox_id": sandbox.id,
        "status": sandbox.status.state if sandbox.status else "Unknown"
    }


@router.get("/job_id", tags=["Security Scan Pipeline"])
async def get_latest_job_id():
    """
    Retrieves the job_id of the most recently initiated scan job in the current session.
    Useful when a /v1/scan-jobs request is blocking and you need the job_id from another context.
    """
    if not _latest_job_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NO_JOBS_FOUND", "message": "No scan jobs have been initiated yet in this session."}
        )
    return {"job_id": _latest_job_id}


@router.get("/job_status", tags=["Security Scan Pipeline"])
async def get_latest_job_status():
    """
    Retrieves the status of the most recently initiated scan job in the current session.
    """
    if not _latest_job_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NO_JOBS_FOUND", "message": "No scan jobs have been initiated yet in this session."}
        )
    return await get_scan_status(_latest_job_id)


# Search endpoint
@router.get(
    "/sandboxes",
    response_model=ListSandboxesResponse,
    responses={
        200: {"description": "Paginated collection of sandboxes"},
        400: {"model": ErrorResponse, "description": "The request was invalid or malformed"},
        401: {"model": ErrorResponse, "description": "Authentication credentials are missing or invalid"},
        500: {"model": ErrorResponse, "description": "An unexpected server error occurred"},
    },
)
async def list_sandboxes(
    state: Optional[List[str]] = Query(None, description="Filter by lifecycle state. Pass multiple times for OR logic."),
    metadata: Optional[str] = Query(None, description="Arbitrary metadata key-value pairs for filtering (URL encoded)."),
    page: int = Query(1, ge=1, description="Page number for pagination"),
    page_size: int = Query(20, ge=1, le=200, alias="pageSize", description="Number of items per page"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID", description="Unique request identifier for tracing"),
) -> ListSandboxesResponse:
    """
    List sandboxes with optional filtering and pagination.

    List all sandboxes with optional filtering and pagination using query parameters.
    All filter conditions use AND logic. Multiple `state` parameters use OR logic within states.

    Args:
        state: Filter by lifecycle state.
        metadata: Arbitrary metadata key-value pairs for filtering.
        page: Page number for pagination.
        page_size: Number of items per page.
        x_request_id: Unique request identifier for tracing (optional; server generates if omitted).

    Returns:
        ListSandboxesResponse: Paginated list of sandboxes
    """
    # Parse metadata query string into dictionary
    metadata_dict = {}
    if metadata:
        from urllib.parse import parse_qsl
        try:
            # Parse query string format: key=value&key2=value2
            # strict_parsing=True rejects malformed segments like "a=1&broken"
            parsed = parse_qsl(metadata, keep_blank_values=True, strict_parsing=True)
            metadata_dict = dict(parsed)
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_METADATA_FORMAT", "message": f"Invalid metadata format: {str(e)}"}
            )

    # Construct request object
    request = ListSandboxesRequest(
        filter=SandboxFilter(state=state, metadata=metadata_dict if metadata_dict else None),
        pagination=PaginationRequest(page=page, pageSize=page_size)
    )

    import logging
    logger = logging.getLogger(__name__)
    logger.info("ListSandboxes: %s", request.filter)

    # Delegate to the service layer for filtering and pagination
    return sandbox_service.list_sandboxes(request)


@router.get(
    "/sandboxes/{sandbox_id}",
    response_model=Sandbox,
    responses={
        200: {"description": "Sandbox current state and metadata"},
        401: {"model": ErrorResponse, "description": "Authentication credentials are missing or invalid"},
        403: {"model": ErrorResponse, "description": "The authenticated user lacks permission for this operation"},
        404: {"model": ErrorResponse, "description": "The requested resource does not exist"},
        500: {"model": ErrorResponse, "description": "An unexpected server error occurred"},
    },
)
async def get_sandbox(
    sandbox_id: str,
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID", description="Unique request identifier for tracing"),
) -> Sandbox:
    """
    Fetch a sandbox by id.

    Returns the complete sandbox information including image specification,
    status, metadata, and timestamps.

    Args:
        sandbox_id: Unique sandbox identifier
        x_request_id: Unique request identifier for tracing (optional; server generates if omitted).

    Returns:
        Sandbox: Complete sandbox information

    Raises:
        HTTPException: If sandbox not found or access denied
    """
    # Delegate to the service layer for sandbox lookup
    return sandbox_service.get_sandbox(sandbox_id)


@router.delete(
    "/sandboxes/{sandbox_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        204: {"description": "Sandbox successfully deleted"},
        401: {"model": ErrorResponse, "description": "Authentication credentials are missing or invalid"},
        403: {"model": ErrorResponse, "description": "The authenticated user lacks permission for this operation"},
        404: {"model": ErrorResponse, "description": "The requested resource does not exist"},
        409: {"model": ErrorResponse, "description": "The operation conflicts with the current state"},
        500: {"model": ErrorResponse, "description": "An unexpected server error occurred"},
    },
)
async def delete_sandbox(
    sandbox_id: str,
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID", description="Unique request identifier for tracing"),
) -> Response:
    """
    Delete a sandbox.

    Terminates sandbox execution. The sandbox will transition through Stopping state to Terminated.

    Args:
        sandbox_id: Unique sandbox identifier
        x_request_id: Unique request identifier for tracing (optional; server generates if omitted).

    Returns:
        Response: 204 No Content

    Raises:
        HTTPException: If sandbox not found or deletion fails
    """
    # Delegate to the service layer for deletion
    sandbox_service.delete_sandbox(sandbox_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============================================================================
# Sandbox Lifecycle Operations
# ============================================================================

@router.post(
    "/sandboxes/{sandbox_id}/pause",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Pause operation accepted"},
        401: {"model": ErrorResponse, "description": "Authentication credentials are missing or invalid"},
        403: {"model": ErrorResponse, "description": "The authenticated user lacks permission for this operation"},
        404: {"model": ErrorResponse, "description": "The requested resource does not exist"},
        409: {"model": ErrorResponse, "description": "The operation conflicts with the current state"},
        500: {"model": ErrorResponse, "description": "An unexpected server error occurred"},
    },
)
async def pause_sandbox(
    sandbox_id: str,
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID", description="Unique request identifier for tracing"),
) -> Response:
    """
    Pause execution while retaining state.

    Pauses a running sandbox while preserving its state.
    Poll GET /sandboxes/{sandboxId} to track state transition to Paused.

    Args:
        sandbox_id: Unique sandbox identifier
        x_request_id: Unique request identifier for tracing (optional; server generates if omitted).

    Returns:
        Response: 202 Accepted

    Raises:
        HTTPException: If sandbox not found or cannot be paused
    """
    # Delegate to the service layer for pause orchestration
    sandbox_service.pause_sandbox(sandbox_id)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/sandboxes/{sandbox_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Resume operation accepted"},
        401: {"model": ErrorResponse, "description": "Authentication credentials are missing or invalid"},
        403: {"model": ErrorResponse, "description": "The authenticated user lacks permission for this operation"},
        404: {"model": ErrorResponse, "description": "The requested resource does not exist"},
        409: {"model": ErrorResponse, "description": "The operation conflicts with the current state"},
        500: {"model": ErrorResponse, "description": "An unexpected server error occurred"},
    },
)
async def resume_sandbox(
    sandbox_id: str,
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID", description="Unique request identifier for tracing"),
) -> Response:
    """
    Resume a paused sandbox.

    Resumes execution of a paused sandbox.
    Poll GET /sandboxes/{sandboxId} to track state transition to Running.

    Args:
        sandbox_id: Unique sandbox identifier
        x_request_id: Unique request identifier for tracing (optional; server generates if omitted).

    Returns:
        Response: 202 Accepted

    Raises:
        HTTPException: If sandbox not found or cannot be resumed
    """
    # Delegate to the service layer for resume orchestration
    sandbox_service.resume_sandbox(sandbox_id)
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/sandboxes/{sandbox_id}/renew-expiration",
    response_model=RenewSandboxExpirationResponse,
    response_model_exclude_none=True,
    responses={
        200: {"description": "Sandbox expiration updated successfully"},
        400: {"model": ErrorResponse, "description": "The request was invalid or malformed"},
        401: {"model": ErrorResponse, "description": "Authentication credentials are missing or invalid"},
        403: {"model": ErrorResponse, "description": "The authenticated user lacks permission for this operation"},
        404: {"model": ErrorResponse, "description": "The requested resource does not exist"},
        409: {"model": ErrorResponse, "description": "The operation conflicts with the current state"},
        500: {"model": ErrorResponse, "description": "An unexpected server error occurred"},
    },
)
async def renew_sandbox_expiration(
    sandbox_id: str,
    request: RenewSandboxExpirationRequest,
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID", description="Unique request identifier for tracing"),
) -> RenewSandboxExpirationResponse:
    """
    Renew sandbox expiration.

    Renews the absolute expiration time of a sandbox.
    The new expiration time must be in the future and after the current expiresAt time.

    Args:
        sandbox_id: Unique sandbox identifier
        request: Renewal request with new expiration time
        x_request_id: Unique request identifier for tracing (optional; server generates if omitted).

    Returns:
        RenewSandboxExpirationResponse: Updated expiration time

    Raises:
        HTTPException: If sandbox not found or renewal fails
    """
    # Delegate to the service layer for expiration updates
    return sandbox_service.renew_expiration(sandbox_id, request)


# ============================================================================
# Sandbox Endpoints
# ============================================================================

@router.get(
    "/sandboxes/{sandbox_id}/endpoints/{port}",
    response_model=Endpoint,
    response_model_exclude_none=True,
    responses={
        200: {"description": "Endpoint retrieved successfully"},
        401: {"model": ErrorResponse, "description": "Authentication credentials are missing or invalid"},
        403: {"model": ErrorResponse, "description": "The authenticated user lacks permission for this operation"},
        404: {"model": ErrorResponse, "description": "The requested resource does not exist"},
        500: {"model": ErrorResponse, "description": "An unexpected server error occurred"},
    },
)
async def get_sandbox_endpoint(
    request: Request,
    sandbox_id: str,
    port: int,
    use_server_proxy: bool = Query(False, description="Whether to return a server-proxied URL"),
    x_request_id: Optional[str] = Header(None, alias="X-Request-ID", description="Unique request identifier for tracing"),
) -> Endpoint:
    """
    Get sandbox access endpoint.

    Returns the public access endpoint URL for accessing a service running on a specific port
    within the sandbox. The service must be listening on the specified port inside the sandbox
    for the endpoint to be available.

    Args:
        request: FastAPI request object
        sandbox_id: Unique sandbox identifier
        port: Port number where the service is listening inside the sandbox (1-65535)
        use_server_proxy: Whether to return a server-proxied URL
        x_request_id: Unique request identifier for tracing (optional; server generates if omitted).

    Returns:
        Endpoint: Public endpoint URL

    Raises:
        HTTPException: If sandbox not found or endpoint not available
    """
    # Delegate to the service layer for endpoint resolution
    endpoint = sandbox_service.get_endpoint(sandbox_id, port)

    if use_server_proxy:
        # Construct proxy URL
        base_url = str(request.base_url).rstrip("/")
        base_url = base_url.replace("https://", "").replace("http://", "")
        endpoint.endpoint = f"{base_url}/sandboxes/{sandbox_id}/proxy/{port}"

    return endpoint


@router.api_route(
    "/sandboxes/{sandbox_id}/proxy/{port}/{full_path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
)
async def proxy_sandbox_endpoint_request(request: Request, sandbox_id: str, port: int, full_path: str):
    """
    Receives all incoming requests, determines the target sandbox from path parameter,
    and asynchronously proxies the request to it.
    """

    endpoint = sandbox_service.get_endpoint(sandbox_id, port, resolve_internal=True)

    target_host = endpoint.endpoint
    query_string = request.url.query

    client: httpx.AsyncClient = request.app.state.http_client

    try:
        upgrade_header = request.headers.get("Upgrade", "")
        if upgrade_header.lower() == "websocket":
            raise HTTPException(status_code=400, detail="Websocket upgrade is not supported yet")

        # Filter headers
        hop_by_hop = set(HOP_BY_HOP_HEADERS)
        connection_header = request.headers.get("connection")
        if connection_header:
            hop_by_hop.update(
                header.strip().lower()
                for header in connection_header.split(",")
                if header.strip()
            )
        headers = {}
        for key, value in request.headers.items():
            key_lower = key.lower()
            if (
                key_lower != "host"
                and key_lower not in hop_by_hop
                and key_lower not in SENSITIVE_HEADERS
            ):
                headers[key] = value

        req = client.build_request(
            method=request.method,
            url=f"http://{target_host}/{full_path}",
            params=query_string if query_string else None,
            headers=headers,
            content=request.stream() if request.method in ("POST", "PUT", "PATCH", "DELETE") else None,
        )

        resp = await client.send(req, stream=True)

        hop_by_hop = set(HOP_BY_HOP_HEADERS)
        connection_header = resp.headers.get("connection")
        if connection_header:
            hop_by_hop.update(
                header.strip().lower()
                for header in connection_header.split(",")
                if header.strip()
            )
        response_headers = {
            key: value
            for key, value in resp.headers.items()
            if key.lower() not in hop_by_hop
        }

        return StreamingResponse(
            content=resp.aiter_bytes(),
            status_code=resp.status_code,
            headers=response_headers,
        )
    except httpx.ConnectError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Could not connect to the backend sandbox {endpoint}: {e}",
        )
    except HTTPException:
        # Preserve explicit HTTP exceptions raised above (e.g. websocket upgrade not supported).
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"An internal error occurred in the proxy: {e}"
        )
