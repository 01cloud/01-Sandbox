"""
models.py — Pydantic models for the GitHub Repository Scanner feature.

All models are specific to repo scanning and do not conflict with
the main models.py in the parent package.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field, validator

# ─────────────────────────────────────────────
# Request Models
# ─────────────────────────────────────────────


class RepoScanRequest(BaseModel):
    """Payload to submit a new GitHub repository scan job."""

    repo_url: str = Field(
        ...,
        example="https://github.com/owner/repo",
        description="Full public GitHub repository URL",
    )

    @validator("repo_url")
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()


# ─────────────────────────────────────────────
# Status / Step Enums
# ─────────────────────────────────────────────


class ScanStep(str, Enum):
    """Ordered scan pipeline steps streamed via SSE."""

    QUEUED = "QUEUED"
    PROVISIONING = "PROVISIONING"
    CLONING = "CLONING"
    DETECTING = "DETECTING"
    SCANNING = "SCANNING"
    DONE = "DONE"
    ERROR = "ERROR"


class DetectionTool(str, Enum):
    """Which language-detection tool was used."""

    LINGUIST = "linguist"
    TOKEI = "tokei"
    ENRY = "enry"
    UNKNOWN = "unknown"


# ─────────────────────────────────────────────
# Result Models
# ─────────────────────────────────────────────


class FindingItem(BaseModel):
    """A single scanner finding (vulnerability / lint issue)."""

    severity: str = "INFO"
    file: str = ""
    line: Optional[Union[int, str]] = None
    issue: str = ""
    tool: str = ""
    remediation: Optional[str] = None


class LanguageScanResult(BaseModel):
    """Aggregated results for one detected language."""

    language: str
    file_count: int = 0
    lines_of_code: int = 0
    percentage: float = 0.0  # share of total repo bytes/LoC
    findings: List[FindingItem] = Field(default_factory=list)


class RepoScanResult(BaseModel):
    """Final aggregated result returned by GET /v1/repo-scan/{job_id}/result."""

    job_id: str
    repo_url: str
    owner: str = ""
    repo: str = ""
    status: ScanStep
    languages: Dict[str, LanguageScanResult] = Field(default_factory=dict)
    detection_tool: DetectionTool = DetectionTool.UNKNOWN
    total_files: int = 0
    total_findings: int = 0
    scan_duration_seconds: float = 0.0
    error: Optional[str] = None


# ─────────────────────────────────────────────
# SSE Event Model
# ─────────────────────────────────────────────


class ScanEvent(BaseModel):
    """Payload for each Server-Sent Event during a scan."""

    job_id: str
    step: ScanStep
    message: str
    progress: int = 0  # 0-100
    detail: Optional[dict] = None  # optional extra payload (final result, etc.)


# ─────────────────────────────────────────────
# API Response Models
# ─────────────────────────────────────────────


class RepoScanSubmitResponse(BaseModel):
    """Immediate response after POST /v1/repo-scan."""

    job_id: str
    status: ScanStep = ScanStep.QUEUED
    status_url: str
    result_url: str
