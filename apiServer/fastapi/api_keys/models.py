from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class GenerateAPIResponse(BaseModel):
    """Response returned upon successfully generating a new API key."""

    api_key: str
    api_key_id: str
    status: str


class APIKeyBackend(str, Enum):
    """Supported backends for key scoping."""

    Z1_SANDBOX = "Z1_SANDBOX"


class APIKeyCreateRequest(BaseModel):
    """Payload to create a new manageable API key."""

    name: str = Field(..., example="Prod-Scanner-Key")
    backend: APIKeyBackend = Field(APIKeyBackend.Z1_SANDBOX)
    ttl_hours: float = Field(1.0, ge=-1.0)  # Default 1 hour, -1 means never expire
    user_email: Optional[str] = None


class APIKeyRecord(BaseModel):
    """Metadata record for a stored API key."""

    id: str  # JTI
    name: str
    backend: str
    user_id: str
    user_email: Optional[str] = None
    created_at: str
    expires_at: str
    last_used_at: Optional[str] = None
    is_revoked: bool = False
    prefix: str  # Partial key for identification (e.g. ci_...)


class APIKeyListResponse(BaseModel):
    """Response containing a list of masked API key records."""

    keys: list[APIKeyRecord]
