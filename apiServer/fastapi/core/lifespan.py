from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .app_state import cleanup_expired_keys_task


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrapping hook logs startup variables for transparency securely."""
    # Launch the key janitor to purge expired keys automatically
    asyncio.create_task(cleanup_expired_keys_task())
    yield
    print("[shutdown] Ceasing operations successfully...")
