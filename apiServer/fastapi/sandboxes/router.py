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
