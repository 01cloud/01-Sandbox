from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends

from .models import CreateSandboxRequest, RunRequest, RunResponse, SandboxResponse


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

    return router


# def start_log_polling(job_id: str):
#     import asyncio
#     from core import state

#     done_event = asyncio.Event()

#     async def poll_logs():
#         last_offset = 0
#         # Give the sandbox a brief moment to boot up and generate log entries
#         await asyncio.sleep(2.0)
#         while not done_event.is_set():
#             try:
#                 loop = asyncio.get_running_loop()
#                 status_res = await loop.run_in_executor(
#                     None, state.backend.get_scan_status, job_id
#                 )
#                 if isinstance(status_res, str) and status_res:
#                     if len(status_res) > last_offset:
#                         new_content = status_res[last_offset:]
#                         last_offset = len(status_res)
#                         for line in new_content.splitlines():
#                             if line.strip():
#                                 await state.job_tracker.push_event(
#                                     job_id,
#                                     "LOG_LINE",
#                                     line,
#                                     60,
#                                 )
#             except Exception:
#                 pass
#             await asyncio.sleep(1.0)

#     poller_task = asyncio.create_task(poll_logs())
#     return done_event, poller_task


# async def stop_log_polling(done_event, poller_task):
#     import asyncio

#     done_event.set()
#     poller_task.cancel()
#     try:
#         await poller_task
#     except asyncio.CancelledError:
#         pass


# async def run_scan_in_background(job_id: str, req_dict: dict):
#     """Background worker — mirrors sandboxes router's full job-tracker lifecycle."""
#     import asyncio
#     import json
#     from core import state

#     try:
#         # Simulation check for file content error triggers
#         files = req_dict.get("files", {})
#         for filename, content in files.items():
#             if "# SIMULATE_RETRY" in content or "# SIMULATE_ERROR" in content:
#                 raise ValueError(
#                     f"Simulated error triggered by content in file {filename}"
#                 )

#         await state.job_tracker.push_event(
#             job_id,
#             "PROVISIONING",
#             "Provisioning remote sandbox and uploading files...",
#             25,
#         )
#         await asyncio.sleep(1.0)
#         await state.job_tracker.push_event(
#             job_id, "SCANNING", "Running Semgrep security analysis...", 60
#         )
#         done_event, poller_task = start_log_polling(job_id)
#         try:
#             data = await state.backend.create_scan_job(req_dict)
#         finally:
#             await stop_log_polling(done_event, poller_task)

#         critical_count = high_count = medium_count = low_count = info_count = 0
#         findings = data.get("findings", [])
#         for f in findings:
#             sev = str(f.get("severity", "INFO")).upper()
#             if "CRITICAL" in sev:
#                 critical_count += 1
#             elif "HIGH" in sev:
#                 high_count += 1
#             elif "MEDIUM" in sev:
#                 medium_count += 1
#             elif "LOW" in sev:
#                 low_count += 1
#             elif "INFO" in sev:
#                 info_count += 1

#         job_record = state.job_tracker.get_job(job_id)
#         summary_data = {
#             "critical": critical_count,
#             "high": high_count,
#             "medium": medium_count,
#             "low": low_count,
#             "info": info_count,
#         }
#         if job_record:
#             job_record.metadata["summary"] = summary_data
#             if state.use_redis and state.redis_client:
#                 try:
#                     metadata_copy = dict(job_record.metadata)
#                     metadata_copy["job_type"] = "quick-scan"
#                     state.redis_client.set(
#                         f"job:{job_id}:metadata",
#                         json.dumps(metadata_copy),
#                         ex=86400,
#                     )
#                 except Exception:
#                     pass
#         elif state.use_redis and state.redis_client:
#             try:
#                 meta_str = state.redis_client.get(f"job:{job_id}:metadata")
#                 if meta_str:
#                     metadata_copy = json.loads(meta_str)
#                     metadata_copy["summary"] = summary_data
#                     state.redis_client.set(
#                         f"job:{job_id}:metadata",
#                         json.dumps(metadata_copy),
#                         ex=86400,
#                     )
#             except Exception:
#                 pass

#         detail_dict = dict(data)
#         detail_dict["critical_count"] = critical_count
#         detail_dict["high_count"] = high_count
#         detail_dict["medium_count"] = medium_count
#         detail_dict["low_count"] = low_count
#         detail_dict["info_count"] = info_count

#         await state.job_tracker.push_event(
#             job_id,
#             "DONE",
#             f"Scan complete — found {len(findings)} security findings.",
#             100,
#             detail=detail_dict,
#         )
#     except Exception as e:
#         print(f"[BACKGROUND TASK ERROR] Scan job {job_id} failed: {e}")
#         await state.job_tracker.push_event(job_id, "ERROR", f"Scan failed: {e}", 0)
