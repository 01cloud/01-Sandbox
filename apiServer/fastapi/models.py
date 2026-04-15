from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

class Language(str, Enum):
    """Supported execution languages."""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    BASH = "bash"


class RunRequest(BaseModel):
    """Payload representing a code execution request structure for the backend."""
    code: str = Field(..., example="print('hello')")
    language: Language = Field(Language.PYTHON)
    timeout: int = Field(30, ge=1, le=120)


class RunResponse(BaseModel):
    """Response defining code execution results dynamically."""
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: float
    sandbox_id: str
    backend: str


class SessionResponse(BaseModel):
    """Identifying metadata correlating linked session context internally."""
    session_id: str
    backend: str
    metadata: dict = {}


class StatusResponse(BaseModel):
    """Reports configuration matching backend instances health correctly."""
    backend: str
    healthy: bool


# --- OpenSandbox Specific Models ---

class ImageSpec(BaseModel):
    """Container image specification."""
    uri: Optional[str] = None
    repository: Optional[str] = None
    tag: Optional[str] = "latest"


class ResourceLimits(BaseModel):
    """Hardware resource constraints."""
    cpu: str = "500m"
    memory: str = "512Mi"


class CreateSandboxRequest(BaseModel):
    """Request payload to provision a new isolated sandbox."""
    image: ImageSpec
    entrypoint: list[str]
    timeout: int = Field(60, ge=1, le=3600)
    env: dict[str, str] = {}
    resourceLimits: ResourceLimits = Field(default_factory=ResourceLimits)
    metadata: dict[str, str] = {}


class SandboxResponse(BaseModel):
    """Standardized metadata representing a provisioned sandbox instance."""
    id: str
    status: str
    image: ImageSpec
    metadata: dict[str, str] = {}


class ScanJobRequest(BaseModel):
    """Payload representing a request to submit files for security scanning."""
    files: Optional[dict[str, str]] = Field(None, description="Map of filename to content")
    code: Optional[str] = Field(None, description="Single code snippet for auto-detection")
    tools: Optional[list[str]] = Field(None, description="Optional list of tools to run")
    timeout: Optional[int] = Field(None, ge=1, le=3600)
    metadata: dict[str, str] = {}


class ScanJobResponse(BaseModel):
    """Response returned upon successfully scheduling a scan job."""
    job_id: str
    sandbox_id: Optional[str] = None
    status: Optional[str] = None
    report: Optional[dict] = None
    error: Optional[str] = None
