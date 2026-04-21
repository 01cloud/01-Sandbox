from __future__ import annotations
import abc
import time
import uuid
import httpx
from typing import Optional

from models import RunResponse, SessionResponse, CreateSandboxRequest, SandboxResponse
from config import opensandbox_headers


class SandboxBackend(abc.ABC):
    """Abstract interface exposing generic operation mapping routines natively."""
    
    @abc.abstractmethod
    def run(self, code: str, language: str, timeout: int) -> RunResponse:
        pass

    @abc.abstractmethod
    def open_session(self) -> SessionResponse:
        pass

    @abc.abstractmethod
    def close_session(self, session_id: str) -> None:
        pass

    @abc.abstractmethod
    def health_check(self) -> bool:
        pass

    @abc.abstractmethod
    def create_sandbox(self, req: CreateSandboxRequest) -> SandboxResponse:
        pass

    @abc.abstractmethod
    async def create_scan_job(self, req_body: dict) -> dict:
        pass

    @abc.abstractmethod
    def get_scan_report(self, job_id: str) -> dict:
        pass

    @abc.abstractmethod
    def get_scan_status(self, job_id: str) -> dict:
        pass

    @abc.abstractmethod
    def list_sandboxes(self) -> list[SandboxResponse]:
        pass

    @property
    @abc.abstractmethod
    def name(self) -> str:
        pass


class GenericHTTPBackend(SandboxBackend):
    """
    Implements standard HTTP interfacing specifically hard-wired explicitly targeting
    REST API backend resources routing dynamically.
    """
    def __init__(self, name: str, url: str):
        self._name = name
        self._url = url

    @property
    def name(self):
        return self._name

    def run(self, code: str, language: str, timeout: int) -> RunResponse:
        """Transmits JSON payload explicitly dictating remote execution instructions."""
        payload = {"code": code, "language": language, "timeout": timeout}
        t0 = time.perf_counter()
        
        try:
            with httpx.Client(timeout=timeout + 5) as client:
                r = client.post(f"{self._url}/run", json=payload, headers=opensandbox_headers())
                r.raise_for_status()
                data = r.json()
                
                return RunResponse(
                    stdout=data.get("stdout", ""),
                    stderr=data.get("stderr", ""),
                    exit_code=data.get("exit_code", 0),
                    duration_ms=(time.perf_counter() - t0) * 1000,
                    sandbox_id=data.get("sandbox_id", str(uuid.uuid4())),
                    backend=self.name,
                )
        except Exception as e:
            return RunResponse(
                stdout="",
                stderr=f"Generic HTTP Backend Error ({self.name}): {e}",
                exit_code=1,
                duration_ms=0,
                sandbox_id="",
                backend=self.name,
            )

    def open_session(self) -> SessionResponse:
        """Simulates dynamic instantiation locally allocating logical tracking contexts."""
        return SessionResponse(session_id=str(uuid.uuid4()), backend=self.name)

    def close_session(self, session_id: str) -> None:
        pass

    def health_check(self) -> bool:
        """Validates upstream responsiveness mapping explicit endpoint checks dynamically."""
        try:
            with httpx.Client(timeout=3) as client:
                r = client.get(f"{self._url}/health", headers=opensandbox_headers())
                return r.status_code < 500
        except Exception:
            return False

    def create_sandbox(self, req: CreateSandboxRequest) -> SandboxResponse:
        """Directly provisions a sandbox on the remote OpenSandbox server."""
        with httpx.Client(timeout=30) as client:
            r = client.post(
                f"{self._url}/v1/sandboxes",
                json=req.dict(exclude_none=True),
                headers=opensandbox_headers()
            )
            r.raise_for_status()
            data = r.json()
            return SandboxResponse(**data)

    async def create_scan_job(self, req_body: dict) -> dict:
        """Triggers the isolated scan pipeline and blocks asynchronously for the final result."""
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(
                f"{self._url}/v1/scan-jobs",
                json=req_body,
                headers=opensandbox_headers()
            )
            r.raise_for_status()
            return r.json()

    def get_scan_report(self, job_id: str) -> dict:
        """Retrieves a persistent scan report from the remote OpenSandbox PVC."""
        with httpx.Client(timeout=10) as client:
            r = client.get(
                f"{self._url}/scan-jobs/{job_id}/report",
                headers=opensandbox_headers()
            )
            r.raise_for_status()
            return r.json()

    def get_scan_status(self, job_id: str) -> dict:
        """Retrieves the live scanning status mapped to the backend OpenSandbox service."""
        with httpx.Client(timeout=5) as client:
            r = client.get(
                f"{self._url}/scan-status/{job_id}",
                headers=opensandbox_headers()
            )
            r.raise_for_status()
            return r.json()

    def list_sandboxes(self) -> list[SandboxResponse]:
        """Retrieves active sandboxes from the remote OpenSandbox server."""
        with httpx.Client(timeout=10) as client:
            r = client.get(f"{self._url}/v1/sandboxes", headers=opensandbox_headers())
            r.raise_for_status()
            data = r.json()
            items = data if isinstance(data, list) else data.get("items", [])
            return [SandboxResponse(**item) for item in items]
