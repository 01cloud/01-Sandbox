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

import asyncio
import base64
import json
import logging
import os
import re
from typing import Any, List, Optional
from uuid import uuid4

import httpx
from fastapi import APIRouter, Body, Header, Query, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from src.api.schema import (
    PVC,
    CreateSandboxRequest,
    CreateSandboxResponse,
    Endpoint,
    ErrorResponse,
    ImageSpec,
    ListSandboxesRequest,
    ListSandboxesResponse,
    PaginationRequest,
    RenewSandboxExpirationRequest,
    RenewSandboxExpirationResponse,
)
from src.api.schema import ResourceLimits as SchemaResourceLimits
from src.api.schema import (
    Sandbox,
    SandboxFilter,
    ScanJobRequest,
    ScanJobResponse,
    Volume,
)
from src.services.factory import create_sandbox_service

logger = logging.getLogger(__name__)

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
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        return "json"

    # Check for YAML & K8s
    if content.startswith("---") or re.search(
        r"^(apiVersion|metadata|version|services|spec|kind):", content, re.MULTILINE
    ):
        # We return 'yaml' even for K8s manifests to ensure scanner tools (kube-score, etc.)
        # correctly identify the file type by extension. The orchestrator will still
        # classify it as 'k8s' based on content.
        return "yaml"

    # Check for Go
    if content.startswith("package ") or "func main()" in content:
        return "go"

    # PRIORITIZE PYTHON: Check for Python-specific markers
    if (
        re.search(r"^\s*(#|def |from .* import |if __name__ ==)", content, re.MULTILINE)
        or "print(" in content
    ):
        return "py"

    # Check for Javascript
    if (
        re.search(r"\b(const|let|var|function|console\.log)\s", content)
        or "require(" in content
    ):
        return "js"

    return "py"  # Default to Python


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


def log_job_event(job_id: str, message: str, parent_job_id: Optional[str] = None):
    """Appends a timestamped message to the job's unified process log."""
    import glob
    from datetime import datetime

    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")

    # Candidate directories for process.log
    candidate_dirs = [
        os.path.join(data_root, job_id, "reports"),
    ]
    if parent_job_id:
        candidate_dirs.insert(
            0, os.path.join(data_root, parent_job_id, job_id, "reports")
        )

    # Search existing subdirectories under data_root
    pattern = os.path.join(data_root, "*", job_id, "reports")
    matches = glob.glob(pattern)
    if matches:
        candidate_dirs.insert(0, matches[0])

    log_dir = candidate_dirs[0]
    log_path = os.path.join(log_dir, "process.log")
    try:
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a") as f:
            f.write(f"[{timestamp}] {message}\n")
    except Exception as e:
        print(f"[SERVER] Failed to write process.log for job {job_id}: {e}")


def stream_pod_logs_to_file(
    sandbox_id: str,
    job_id: str,
    namespace: str,
    stop_event,
    parent_job_id: Optional[str] = None,
) -> None:
    """
    Background thread: streams the sandbox pod's stdout/stderr line-by-line
    into the job's process.log so the SSE polling loop picks it up in real time.

    Pod naming convention: BatchSandbox/StatefulSet pods are named <sandbox_id>-0.
    We fall back to label-selector lookup (opensandbox.io/id=<sandbox_id>) if the
    direct name lookup fails (e.g. the pod isn't ready yet).
    """
    import threading
    import time as _time

    from kubernetes import client as k8s_client
    from kubernetes import watch as k8s_watch
    from kubernetes.client.exceptions import ApiException

    tag = f"[PodLogStream][{sandbox_id[:8]}]"

    def _get_v1() -> k8s_client.CoreV1Api:
        """Return a CoreV1Api using the already-loaded in-cluster config."""
        return k8s_client.CoreV1Api()

    def _wait_for_pod(
        v1: k8s_client.CoreV1Api, pod_name: str, timeout: int = 90
    ) -> bool:
        """Poll until the pod exists and is Running/Succeeded (or timeout)."""
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            if stop_event.is_set():
                return False
            try:
                pod = v1.read_namespaced_pod(name=pod_name, namespace=namespace)
                phase = pod.status.phase if pod.status else None
                if phase in ("Running", "Succeeded"):
                    return True
                if phase in ("Failed", "Unknown"):
                    log_job_event(
                        job_id,
                        f"{tag} Pod entered terminal phase '{phase}' — stopping log stream",
                    )
                    return False
            except ApiException as exc:
                if exc.status != 404:
                    log_job_event(
                        job_id, f"{tag} Waiting for pod (API error {exc.status})..."
                    )
            _time.sleep(2)
        log_job_event(job_id, f"{tag} Timed out waiting for pod to become Running")
        return False

    def _find_pod_name(v1: k8s_client.CoreV1Api) -> str:
        """Resolve the actual pod name for this sandbox_id.

        Tries <sandbox_id>-0 first (StatefulSet convention used by BatchSandbox
        provider), then falls back to a label selector.
        """
        primary = f"{sandbox_id}-0"
        try:
            v1.read_namespaced_pod(name=primary, namespace=namespace)
            return primary
        except ApiException:
            pass

        # Fallback: label-selector lookup
        try:
            pods = v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"opensandbox.io/id={sandbox_id}",
            )
            if pods.items:
                return pods.items[0].metadata.name
        except ApiException:
            pass

        return primary  # best-effort — may fail, will be caught by the caller

    try:
        v1 = _get_v1()
        pod_name = _find_pod_name(v1)
        log_job_event(job_id, f"{tag} Waiting for pod '{pod_name}' to start...")

        if not _wait_for_pod(v1, pod_name):
            return

        log_job_event(job_id, f"{tag} Streaming live logs from pod '{pod_name}'...")

        try:
            in_report = False
            report_lines = []
            import glob
            import json
            import os

            def _process_line(line_str: str):
                nonlocal in_report, report_lines
                if not line_str.strip():
                    return
                if "---SCAN_REPORT_START---" in line_str:
                    in_report = True
                    report_lines = []
                    return
                elif "---SCAN_REPORT_END---" in line_str:
                    in_report = False
                    report_str = "".join(report_lines)
                    try:
                        report_data = json.loads(report_str)
                        data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
                        reports_dir = os.path.join(data_root, job_id, "reports")
                        if not os.path.exists(reports_dir):
                            pattern = os.path.join(data_root, "*", job_id, "reports")
                            matches = glob.glob(pattern)
                            if matches:
                                reports_dir = matches[0]
                        os.makedirs(reports_dir, exist_ok=True)
                        report_path = os.path.join(
                            reports_dir, "security_scan_report.json"
                        )
                        with open(report_path, "w") as f:
                            json.dump(report_data, f, indent=2)
                        log_job_event(
                            job_id,
                            f"{tag} Intercepted and saved scan report to host PVC at {report_path}",
                        )
                    except Exception as parse_err:
                        log_job_event(
                            job_id,
                            f"{tag} Failed to parse/save intercepted scan report: {parse_err}",
                        )
                    return

                if in_report:
                    report_lines.append(line_str)
                else:
                    log_job_event(
                        job_id,
                        f"[{sandbox_id[:8]}] {line_str}",
                        parent_job_id=parent_job_id,
                    )

            resp = v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container="sandbox",
                follow=True,
                _preload_content=False,
            )
            for line_bytes in resp:
                if stop_event.is_set():
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                _process_line(line)

        except Exception as stream_err:
            log_job_event(job_id, f"{tag} Log stream ended: {stream_err}")

    except Exception as outer_err:
        log_job_event(job_id, f"{tag} Pod log streamer error: {outer_err}")


# ============================================================================
# Sandbox CRUD Operations
# ============================================================================


@router.post(
    "/sandboxes",
    response_model=CreateSandboxResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"description": "Sandbox creation accepted for asynchronous provisioning"},
        400: {
            "model": ErrorResponse,
            "description": "The request was invalid or malformed",
        },
        401: {
            "model": ErrorResponse,
            "description": "Authentication credentials are missing or invalid",
        },
        409: {
            "model": ErrorResponse,
            "description": "The operation conflicts with the current state",
        },
        500: {
            "model": ErrorResponse,
            "description": "An unexpected server error occurred",
        },
    },
)
async def create_sandbox(
    request: CreateSandboxRequest,
    x_request_id: Optional[str] = Header(
        None, alias="X-Request-ID", description="Unique request identifier for tracing"
    ),
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
        400: {
            "model": ErrorResponse,
            "description": "The request was invalid or malformed",
        },
        500: {
            "model": ErrorResponse,
            "description": "An unexpected server error occurred",
        },
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "string",
                        "description": "A JSON ScanJobRequest or a raw Python code snippet.",
                        "example": "print('Hello, World!')",
                    }
                }
            }
        }
    },
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
    import asyncio
    import base64
    import json
    import os
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

    log_job_event(job_id, f"[SERVER] Starting security scan job: {job_id}")

    parent_job_id = metadata.get("parent_job_id")
    subpath_prefix = f"{parent_job_id}/{job_id}" if parent_job_id else job_id

    # Base path for shared storage (mounted via PVC in Helm)
    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
    job_dir = os.path.join(data_root, subpath_prefix, "workspace")
    reports_dir = os.path.join(data_root, subpath_prefix, "reports")

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
                if "." not in base_name or base_name.lower().startswith(
                    ("main", "code")
                ):
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
        log_job_event(
            job_id,
            f"[SERVER] Writing {len(files_to_save)} source file(s) to PVC workspace...",
        )
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
            detail={
                "code": "FILE_SYSTEM_ERROR",
                "message": f"Failed to write scan files: {str(e)}",
            },
        )

    # Allow target image override from request metadata or auto-detect from files
    target_image = metadata.get("image") or metadata.get("sandboxImage")

    if not target_image:
        detected_image = None
        for filename in files_to_save.keys():
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext in ("py", "python"):
                detected_image = os.environ.get(
                    "SCANNER_IMAGE_PYTHON", "199012118961/01sandbox-scanner-python:dev"
                )
                break
            elif ext in ("go", "golang"):
                detected_image = os.environ.get(
                    "SCANNER_IMAGE_GO", "199012118961/01sandbox-scanner-go:dev"
                )
                break
            elif ext in ("java", "class"):
                detected_image = os.environ.get(
                    "SCANNER_IMAGE_JAVA", "199012118961/01sandbox-scanner-java:dev"
                )
                break
            elif ext in ("js", "ts", "jsx", "tsx", "json"):
                detected_image = os.environ.get(
                    "SCANNER_IMAGE_NODE", "199012118961/01sandbox-scanner-node:dev"
                )
                break
            elif ext in ("yaml", "yml"):
                detected_image = os.environ.get(
                    "SCANNER_IMAGE_K8S", "199012118961/01sandbox-scanner-k8s:dev"
                )
                break
            elif ext in ("rs", "rust"):
                detected_image = os.environ.get(
                    "SCANNER_IMAGE_RUST", "199012118961/01sandbox-scanner-rust:dev"
                )
                break
            elif ext in ("c", "cpp", "cc", "h", "hpp"):
                detected_image = os.environ.get(
                    "SCANNER_IMAGE_CPP", "199012118961/01sandbox-scanner-cpp:dev"
                )
                break
            elif ext in ("rb", "ruby"):
                detected_image = os.environ.get(
                    "SCANNER_IMAGE_RUBY", "199012118961/01sandbox-scanner-ruby:dev"
                )
                break

        target_image = detected_image or os.environ.get(
            "SANDBOX_IMAGE", "199012118961/01sandbox-codeinterpreter:dev"
        )

    if not target_image:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "MISSING_CONFIGURATION",
                "message": "SANDBOX_IMAGE environment variable is not set.",
            },
        )

    extensions = {}
    metadata = metadata or {}
    metadata["job_id"] = job_id
    metadata["image"] = target_image
    extensions["image"] = target_image
    if parent_job_id:
        metadata["parent_job_id"] = parent_job_id

    if metadata.get("runtime"):
        extensions["runtimeClassName"] = metadata.get("runtime")
    elif metadata.get("runtime_class"):
        extensions["runtimeClassName"] = metadata.get("runtime_class")
    # Ensure all metadata values are primitive strings so Kubernetes labels/annotations and schema validation succeed
    sanitized_metadata = {}
    if metadata:
        for k, v in metadata.items():
            if isinstance(v, (dict, list)):
                sanitized_metadata[str(k)] = json.dumps(v)
            else:
                sanitized_metadata[str(k)] = str(v) if v is not None else ""

    sandbox_req = CreateSandboxRequest(
        image=ImageSpec(uri=target_image),
        resourceLimits=SchemaResourceLimits(
            root={
                "cpu": os.environ.get("SANDBOX_CPU", "200m"),
                "memory": os.environ.get("SANDBOX_MEMORY", "512Mi"),
            }
        ),
        entrypoint=["/opt/opensandbox/code-interpreter.sh"],
        timeout=scan_request.timeout if scan_request and scan_request.timeout else 600,
        env={
            "SCAN_DIR": f"/data/{subpath_prefix}/workspace",
            "SCAN_REPORT": f"/data/{subpath_prefix}/reports/security_scan_report.json",
            "SCAN_TOOLS": (
                ",".join(scan_request.tools)
                if scan_request and scan_request.tools
                else ""
            ),
        },
        volumes=[
            Volume(
                name="scan-data-volume",
                pvc=PVC(claimName="scan-pvc"),
                mountPath="/data",
            ),
        ],
        metadata=sanitized_metadata,
        extensions=extensions if extensions else None,
    )

    log_job_event(job_id, "[SERVER] Provisioning code-interpreter sandbox...")
    created_sandbox = sandbox_service.create_sandbox(sandbox_req)
    sandbox_id = created_sandbox.id

    log_job_event(
        job_id,
        f"[SERVER] Sandbox created (ID: {sandbox_id[:8]}...). Starting live log stream...",
    )

    # ── Start real-time pod log streaming ────────────────────────────────────
    # Determine the namespace from the sandbox_service config if available,
    # defaulting to the well-known opensandbox-system namespace.
    import threading

    _sandbox_namespace = os.environ.get("SANDBOX_NAMESPACE", "opensandbox-system")
    try:
        if hasattr(sandbox_service, "namespace"):
            _sandbox_namespace = sandbox_service.namespace
    except Exception:
        pass

    _stop_log_stream = threading.Event()
    _log_thread = threading.Thread(
        target=stream_pod_logs_to_file,
        args=(sandbox_id, job_id, _sandbox_namespace, _stop_log_stream, parent_job_id),
        daemon=True,
        name=f"pod-log-stream-{job_id[:8]}",
    )
    _log_thread.start()
    # ─────────────────────────────────────────────────────────────────────────

    import glob

    possible_report_paths = [
        os.path.join(data_root, subpath_prefix, "reports", "security_scan_report.json"),
        os.path.join(data_root, job_id, "reports", "security_scan_report.json"),
    ]
    if parent_job_id:
        possible_report_paths.append(
            os.path.join(
                data_root, parent_job_id, "reports", "security_scan_report.json"
            )
        )

    timeout_seconds = sandbox_req.timeout if sandbox_req.timeout else 600
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    final_response = None

    try:
        while asyncio.get_event_loop().time() < deadline:
            # 1. Check candidate report paths — return immediately when found
            found_report = None
            for rpath in possible_report_paths:
                if os.path.exists(rpath):
                    try:
                        with open(rpath, "r") as f:
                            found_report = json.load(f)
                        if found_report:
                            break
                    except (json.JSONDecodeError, OSError):
                        pass

            if found_report is not None:
                final_response = ScanJobResponse(
                    job_id=job_id,
                    sandbox_id=sandbox_id,
                    status="COMPLETED",
                    report=found_report,
                )
                break

            # 2. Yield control to the async event loop (non-blocking wait)
            await asyncio.sleep(1)

            # 3. Check sandbox state for early terminal failures
            #    Run in executor so the blocking K8s API call doesn't freeze the event loop
            try:
                loop = asyncio.get_event_loop()
                sb = await loop.run_in_executor(
                    None, sandbox_service.get_sandbox, sandbox_id
                )
                state = str(sb.status.state if sb.status else "Unknown")
                if state in (
                    "Completed",
                    "Succeeded",
                    "Failed",
                    "Terminated",
                    "Stopped",
                ):
                    # Give filesystem 0.5s buffer for mid-write report sync
                    existing_rpath = next(
                        (p for p in possible_report_paths if os.path.exists(p)), None
                    )
                    if not existing_rpath:
                        await asyncio.sleep(0.5)
                        existing_rpath = next(
                            (p for p in possible_report_paths if os.path.exists(p)),
                            None,
                        )

                    if existing_rpath:
                        try:
                            with open(existing_rpath, "r") as f:
                                report_data = json.load(f)
                            final_response = ScanJobResponse(
                                job_id=job_id,
                                sandbox_id=sandbox_id,
                                status="COMPLETED",
                                report=report_data,
                            )
                            break
                        except Exception:
                            pass

                    final_response = ScanJobResponse(
                        job_id=job_id,
                        sandbox_id=sandbox_id,
                        status="COMPLETED"
                        if state in ("Completed", "Succeeded")
                        else "FAILED",
                        report={},
                        error=None
                        if state in ("Completed", "Succeeded")
                        else f"Sandbox reached state '{state}' without report.",
                    )
                    break
            except HTTPException as he:
                if he.status_code == 404:
                    final_response = ScanJobResponse(
                        job_id=job_id,
                        sandbox_id=sandbox_id,
                        status="FAILED",
                        error="Sandbox was deleted or expired before the scan report was written.",
                    )
                    break
            except Exception:
                pass  # Ignore transient look-up errors; keep waiting

    finally:
        # Always stop the log streamer thread when the job ends
        _stop_log_stream.set()
        _log_thread.join(timeout=3)

    # Schedule automatic pod termination after scan completion (configurable via AUTO_TERMINATION_SECONDS env var)
    if final_response is not None:
        try:
            auto_term_secs = float(os.environ.get("AUTO_TERMINATION_SECONDS", "0"))

            def _cleanup_sandbox(sb_id: str):
                try:
                    sandbox_service.delete_sandbox(sb_id)
                    logger.info(
                        f"[SERVER] Auto-terminated sandbox {sb_id} {auto_term_secs}s after scan completion."
                    )
                except Exception as del_err:
                    logger.warning(
                        f"[SERVER] Cleanup error for sandbox {sb_id}: {del_err}"
                    )

            if auto_term_secs <= 0:
                import threading

                cleanup_thread = threading.Thread(
                    target=_cleanup_sandbox, args=[sandbox_id], daemon=True
                )
                cleanup_thread.start()
            else:
                import threading

                timer = threading.Timer(
                    auto_term_secs, _cleanup_sandbox, args=[sandbox_id]
                )
                timer.daemon = True
                timer.start()
        except Exception as t_err:
            logger.warning(
                f"[SERVER] Failed to schedule auto cleanup for sandbox {sandbox_id}: {t_err}"
            )

        return final_response

    # Deadline exceeded without a report
    # Also clean up sandbox if 10-minute deadline is exceeded
    try:
        sandbox_service.delete_sandbox(sandbox_id)
    except Exception:
        pass

    return ScanJobResponse(
        job_id=job_id,
        sandbox_id=sandbox_id,
        status="FAILED",
        error="Scan timed out waiting for the report to be written to the PVC.",
    )


@router.get("/scan-jobs/{job_id}/report", tags=["Security Scan Pipeline"])
async def get_scan_report(job_id: str):
    """
    Retrieves the persistent security scan report for a specific job ID.
    This report is stored on the PVC and lives beyond the sandbox lifecycle.
    """
    import glob
    import json
    import os

    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
    report_path = os.path.join(
        data_root, job_id, "reports", "security_scan_report.json"
    )

    if not os.path.exists(report_path):
        # Try searching in child directories under parent jobs
        pattern = os.path.join(
            data_root, "*", job_id, "reports", "security_scan_report.json"
        )
        matches = glob.glob(pattern)
        if matches:
            report_path = matches[0]

    if not os.path.exists(report_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "REPORT_NOT_FOUND",
                "message": f"No report found for job {job_id}. The scan may still be in progress.",
            },
        )

    try:
        with open(report_path, "r") as f:
            content = json.load(f)
        return content
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "FILE_READ_ERROR", "message": str(e)},
        )


@router.delete(
    "/scan-jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Security Scan Pipeline"],
)
async def delete_scan_job(job_id: str, terminate: bool = Query(False)):
    """
    Deletes the persistent scan reports and workspace files for a job ID from the PVC,
    and terminates associated sandboxes if terminate parameter is True.
    """
    import glob
    import os
    import shutil

    # 1. Terminate associated sandboxes if requested
    if terminate:
        try:
            list_req = ListSandboxesRequest(
                filter=SandboxFilter(),
                pagination=PaginationRequest(page=1, pageSize=200),
            )
            sandbox_list = sandbox_service.list_sandboxes(list_req)
            for sb in sandbox_list.items:
                if sb.metadata and (
                    sb.metadata.get("job_id") == job_id
                    or sb.metadata.get("parent_job_id") == job_id
                ):
                    print(f"[SERVER] Terminating sandbox {sb.id} for job {job_id}")
                    try:
                        sandbox_service.delete_sandbox(sb.id)
                    except Exception as del_err:
                        print(f"[SERVER] Failed to delete sandbox {sb.id}: {del_err}")
        except Exception as e:
            print(f"[SERVER] Failed to list sandboxes for job {job_id} cleanup: {e}")

    # 2. Delete PVC directories
    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
    job_dir = os.path.join(data_root, job_id)

    if not os.path.exists(job_dir):
        # Try searching in child directories under parent jobs
        pattern = os.path.join(data_root, "*", job_id)
        matches = glob.glob(pattern)
        if matches:
            job_dir = matches[0]

    if os.path.exists(job_dir):
        import asyncio
        import time

        # Retry loop to allow active volume mounts to release as pods terminate
        max_retries = 10
        delay = 1.0
        success = False
        last_err = None

        for attempt in range(max_retries):
            try:
                if os.path.exists(job_dir):
                    shutil.rmtree(job_dir)
                success = True
                print(
                    f"[SERVER] Deleted scan job directory from PVC on attempt {attempt + 1}: {job_dir}"
                )
                break
            except Exception as e:
                last_err = e
                print(
                    f"[SERVER] Attempt {attempt + 1} to delete directory {job_dir} failed: {e}. Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, 5.0)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "code": "FILE_SYSTEM_ERROR",
                    "message": f"Failed to delete scan job directory from PVC after {max_retries} attempts: {str(last_err)}",
                },
            )

        # Check if the parent directory (parent_job_id) is empty, and if so, delete it
        parent_dir = os.path.dirname(job_dir)
        if parent_dir != data_root and os.path.exists(parent_dir):
            try:
                # Remove any empty subdirectories if present (e.g. if deletion left them empty)
                if os.path.isdir(parent_dir) and not os.listdir(parent_dir):
                    os.rmdir(parent_dir)
                    print(
                        f"[SERVER] Deleted empty parent directory from PVC: {parent_dir}"
                    )
            except Exception as e:
                print(
                    f"[SERVER] Failed to check/delete parent directory {parent_dir}: {e}"
                )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/scan-jobs/{job_id}/workspace/{file_path:path}", tags=["Security Scan Pipeline"]
)
async def get_scan_source(job_id: str, file_path: str):
    """
    Retrieves a specific source file uploaded during a scan job.
    """
    import glob
    import os

    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
    safe_file = os.path.basename(file_path)  # Basic safety
    full_path = os.path.join(data_root, job_id, "workspace", safe_file)

    if not os.path.exists(full_path):
        # Try searching in child directories under parent jobs
        pattern = os.path.join(data_root, "*", job_id, "workspace", safe_file)
        matches = glob.glob(pattern)
        if matches:
            full_path = matches[0]

    if not os.path.exists(full_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "FILE_NOT_FOUND",
                "message": f"Source file {file_path} not found for job {job_id}.",
            },
        )

    return FileResponse(full_path)


@router.get("/scan-status/{job_id}", tags=["Security Scan Pipeline"])
@router.get("/scan-status", tags=["Security Scan Pipeline"])
async def get_scan_status(job_id: Optional[str] = None):
    """
    Retrieves the current sandbox status for a given scan job ID.
    If job_id is omitted, retrieves the status of the most recently initiated job.
    """
    if not job_id:
        if not _latest_job_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "NO_JOBS_FOUND",
                    "message": "No scan jobs have been initiated yet in this session.",
                },
            )
        job_id = _latest_job_id

    # 1. Try to fetch process logs first if available (works even if sandbox is completed/deleted)
    data_root = os.environ.get("SCAN_DATA_ROOT", "/data")
    log_path = os.path.join(data_root, job_id, "reports", "process.log")
    if not os.path.exists(log_path):
        import glob

        matches = glob.glob(
            os.path.join(data_root, "*", job_id, "reports", "process.log")
        )
        if matches:
            log_path = matches[0]

    logs = ""
    if os.path.exists(log_path):
        try:
            with open(log_path, "r") as f:
                logs = f.read()
        except Exception:
            logs = "Error reading process logs."

    if logs:
        return Response(content=logs, media_type="text/plain")

    # 2. Fall back to active sandboxes if log file does not exist or is empty
    request = ListSandboxesRequest(
        filter=SandboxFilter(metadata={"job_id": job_id}),
        pagination=PaginationRequest(page=1, pageSize=1),
    )
    res = sandbox_service.list_sandboxes(request)

    if not res.items:
        return {
            "job_id": job_id,
            "status": "NOT_FOUND",
            "message": "No active sandbox found for this job ID and no process logs exist.",
        }

    sandbox = res.items[0]
    return {
        "job_id": job_id,
        "sandbox_id": sandbox.id,
        "status": sandbox.status.state if sandbox.status else "Unknown",
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
            detail={
                "code": "NO_JOBS_FOUND",
                "message": "No scan jobs have been initiated yet in this session.",
            },
        )
    return {"job_id": _latest_job_id}


@router.get("/job_status", tags=["Security Scan Pipeline"])
async def get_latest_job_status_alias():
    """
    Alias for /v1/scan-status to retrieve the latest job status.
    """
    return await get_scan_status()


# Search endpoint
@router.get(
    "/sandboxes",
    response_model=ListSandboxesResponse,
    responses={
        200: {"description": "Paginated collection of sandboxes"},
        400: {
            "model": ErrorResponse,
            "description": "The request was invalid or malformed",
        },
        401: {
            "model": ErrorResponse,
            "description": "Authentication credentials are missing or invalid",
        },
        500: {
            "model": ErrorResponse,
            "description": "An unexpected server error occurred",
        },
    },
)
async def list_sandboxes(
    state: Optional[List[str]] = Query(
        None, description="Filter by lifecycle state. Pass multiple times for OR logic."
    ),
    metadata: Optional[str] = Query(
        None,
        description="Arbitrary metadata key-value pairs for filtering (URL encoded).",
    ),
    page: int = Query(1, ge=1, description="Page number for pagination"),
    page_size: int = Query(
        20, ge=1, le=200, alias="pageSize", description="Number of items per page"
    ),
    x_request_id: Optional[str] = Header(
        None, alias="X-Request-ID", description="Unique request identifier for tracing"
    ),
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
                detail={
                    "code": "INVALID_METADATA_FORMAT",
                    "message": f"Invalid metadata format: {str(e)}",
                },
            )

    # Construct request object
    request = ListSandboxesRequest(
        filter=SandboxFilter(
            state=state, metadata=metadata_dict if metadata_dict else None
        ),
        pagination=PaginationRequest(page=page, pageSize=page_size),
    )

    logger.info("ListSandboxes: %s", request.filter)

    # Delegate to the service layer for filtering and pagination
    return sandbox_service.list_sandboxes(request)


@router.get(
    "/sandboxes/{sandbox_id}",
    response_model=Sandbox,
    responses={
        200: {"description": "Sandbox current state and metadata"},
        401: {
            "model": ErrorResponse,
            "description": "Authentication credentials are missing or invalid",
        },
        403: {
            "model": ErrorResponse,
            "description": "The authenticated user lacks permission for this operation",
        },
        404: {
            "model": ErrorResponse,
            "description": "The requested resource does not exist",
        },
        500: {
            "model": ErrorResponse,
            "description": "An unexpected server error occurred",
        },
    },
)
async def get_sandbox(
    sandbox_id: str,
    x_request_id: Optional[str] = Header(
        None, alias="X-Request-ID", description="Unique request identifier for tracing"
    ),
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
        401: {
            "model": ErrorResponse,
            "description": "Authentication credentials are missing or invalid",
        },
        403: {
            "model": ErrorResponse,
            "description": "The authenticated user lacks permission for this operation",
        },
        404: {
            "model": ErrorResponse,
            "description": "The requested resource does not exist",
        },
        409: {
            "model": ErrorResponse,
            "description": "The operation conflicts with the current state",
        },
        500: {
            "model": ErrorResponse,
            "description": "An unexpected server error occurred",
        },
    },
)
async def delete_sandbox(
    sandbox_id: str,
    x_request_id: Optional[str] = Header(
        None, alias="X-Request-ID", description="Unique request identifier for tracing"
    ),
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
        401: {
            "model": ErrorResponse,
            "description": "Authentication credentials are missing or invalid",
        },
        403: {
            "model": ErrorResponse,
            "description": "The authenticated user lacks permission for this operation",
        },
        404: {
            "model": ErrorResponse,
            "description": "The requested resource does not exist",
        },
        409: {
            "model": ErrorResponse,
            "description": "The operation conflicts with the current state",
        },
        500: {
            "model": ErrorResponse,
            "description": "An unexpected server error occurred",
        },
    },
)
async def pause_sandbox(
    sandbox_id: str,
    x_request_id: Optional[str] = Header(
        None, alias="X-Request-ID", description="Unique request identifier for tracing"
    ),
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
        401: {
            "model": ErrorResponse,
            "description": "Authentication credentials are missing or invalid",
        },
        403: {
            "model": ErrorResponse,
            "description": "The authenticated user lacks permission for this operation",
        },
        404: {
            "model": ErrorResponse,
            "description": "The requested resource does not exist",
        },
        409: {
            "model": ErrorResponse,
            "description": "The operation conflicts with the current state",
        },
        500: {
            "model": ErrorResponse,
            "description": "An unexpected server error occurred",
        },
    },
)
async def resume_sandbox(
    sandbox_id: str,
    x_request_id: Optional[str] = Header(
        None, alias="X-Request-ID", description="Unique request identifier for tracing"
    ),
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
        400: {
            "model": ErrorResponse,
            "description": "The request was invalid or malformed",
        },
        401: {
            "model": ErrorResponse,
            "description": "Authentication credentials are missing or invalid",
        },
        403: {
            "model": ErrorResponse,
            "description": "The authenticated user lacks permission for this operation",
        },
        404: {
            "model": ErrorResponse,
            "description": "The requested resource does not exist",
        },
        409: {
            "model": ErrorResponse,
            "description": "The operation conflicts with the current state",
        },
        500: {
            "model": ErrorResponse,
            "description": "An unexpected server error occurred",
        },
    },
)
async def renew_sandbox_expiration(
    sandbox_id: str,
    request: RenewSandboxExpirationRequest,
    x_request_id: Optional[str] = Header(
        None, alias="X-Request-ID", description="Unique request identifier for tracing"
    ),
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
        401: {
            "model": ErrorResponse,
            "description": "Authentication credentials are missing or invalid",
        },
        403: {
            "model": ErrorResponse,
            "description": "The authenticated user lacks permission for this operation",
        },
        404: {
            "model": ErrorResponse,
            "description": "The requested resource does not exist",
        },
        500: {
            "model": ErrorResponse,
            "description": "An unexpected server error occurred",
        },
    },
)
async def get_sandbox_endpoint(
    request: Request,
    sandbox_id: str,
    port: int,
    use_server_proxy: bool = Query(
        False, description="Whether to return a server-proxied URL"
    ),
    x_request_id: Optional[str] = Header(
        None, alias="X-Request-ID", description="Unique request identifier for tracing"
    ),
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
async def proxy_sandbox_endpoint_request(
    request: Request, sandbox_id: str, port: int, full_path: str
):
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
            raise HTTPException(
                status_code=400, detail="Websocket upgrade is not supported yet"
            )

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
            content=(
                request.stream()
                if request.method in ("POST", "PUT", "PATCH", "DELETE")
                else None
            ),
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
