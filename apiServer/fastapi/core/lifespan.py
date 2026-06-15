from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .app_state import cleanup_expired_keys_task, state
from .queue.connection import close_rabbitmq, connect_rabbitmq
from .queue.consumer import start_all_consumers


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Bootstrapping hook logs startup variables for transparency securely."""
    # Launch the key janitor to purge expired keys automatically
    asyncio.create_task(cleanup_expired_keys_task())

    # Launch the expiring key notifier task
    from services.expiry_checker import check_expiring_keys_task

    asyncio.create_task(check_expiring_keys_task(state))

    try:
        conn = await connect_rabbitmq()
        if conn:
            asyncio.create_task(start_all_consumers(state))
    except Exception as e:
        print(f"[RabbitMQ] Startup warning: {e} — running in fallback mode.")

    # Start Redis Pub/Sub cancellation listener
    from .queue.cancellation import setup_cancellation_listener

    asyncio.create_task(setup_cancellation_listener(state))

    yield
    print("[shutdown] Ceasing operations successfully...")
    await close_rabbitmq()
