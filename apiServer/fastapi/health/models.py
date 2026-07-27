from __future__ import annotations

from pydantic import BaseModel


# Status Response
class StatusResponse(BaseModel):
    """Reports configuration matching backend instances health correctly."""

    backend: str
    healthy: bool


class DependencyStatus(BaseModel):
    status: str
    details: str


class HealthResponse(BaseModel):
    status_code: int
    status: str
    backend: str
    healthy: bool
    dependencies: dict[str, DependencyStatus]
