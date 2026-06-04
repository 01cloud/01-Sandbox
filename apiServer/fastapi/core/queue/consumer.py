from __future__ import annotations

import asyncio
import json
import os

import aio_pika

from .connection import get_connection
from .job_types import ALL_SCAN_JOB_TYPES, EXCHANGE_NAME, ScanJobType


async def _start_single_consumer(jt: ScanJobType, app_state) -> None:
    env_key = f"MAX_{jt.job_type.upper().replace('-', '_')}_WORKERS"
    prefetch = int(os.environ.get(env_key, str(jt.prefetch_count)))
    conn = get_connection()
    if not conn:
        return

    ch = await conn.channel()
    await ch.set_qos(prefetch_count=prefetch)
    ex = await ch.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
    )
    q = await ch.declare_queue(jt.queue_name, durable=True)
    await q.bind(ex, routing_key=jt.routing_key)
    print(f"[RabbitMQ] Consumer ready: queue={jt.queue_name} prefetch={prefetch}")

    if jt.job_type == "quick-scan":
        from sandboxes.router import run_scan_in_background

        async def on_message(msg: aio_pika.IncomingMessage):
            async with msg.process():
                try:
                    p = json.loads(msg.body)
                    print(f"[RabbitMQ][quick-scan] job={p['job_id'][:8]}")
                    await run_scan_in_background(p["job_id"], p["req_dict"])
                except Exception as e:
                    print(f"[RabbitMQ][quick-scan] Error: {e}")

    elif jt.job_type == "repo-scan":
        from scan_repository.scan_repository import _run_scan_pipeline

        async def on_message(msg: aio_pika.IncomingMessage):
            async with msg.process():
                try:
                    p = json.loads(msg.body)
                    print(
                        f"[RabbitMQ][repo-scan] job={p['job_id'][:8]} {p['owner']}/{p['repo']}"
                    )
                    await _run_scan_pipeline(
                        p["job_id"], p["repo_url"], p["owner"], p["repo"], app_state
                    )
                except Exception as e:
                    print(f"[RabbitMQ][repo-scan] Error: {e}")

    else:
        print(f"[RabbitMQ] No handler for job_type={jt.job_type}")
        return

    await q.consume(on_message)


async def start_all_consumers(app_state) -> None:
    await asyncio.gather(
        *[_start_single_consumer(jt, app_state) for jt in ALL_SCAN_JOB_TYPES]
    )
    print(f"[RabbitMQ] All {len(ALL_SCAN_JOB_TYPES)} consumers active.")
