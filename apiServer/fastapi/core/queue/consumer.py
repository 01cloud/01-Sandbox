from __future__ import annotations

import asyncio
import json
import os

import aio_pika

from .cancellation import is_job_cancelled_or_deleted
from .connection import get_connection
from .dlq import DLQ_ROUTING_KEY, DLX_EXCHANGE_NAME, declare_dlq
from .job_types import ALL_SCAN_JOB_TYPES, EXCHANGE_NAME, ScanJobType
from .retry import declare_retry_topology, handle_worker_failure


async def _start_single_consumer(jt: ScanJobType, app_state) -> None:
    # Look for PREFETCH_<JOB_TYPE> first, then fall back to MAX_<JOB_TYPE>_WORKERS for backward compatibility
    prefetch_env_key = f"PREFETCH_{jt.job_type.upper().replace('-', '_')}"
    legacy_env_key = f"MAX_{jt.job_type.upper().replace('-', '_')}_WORKERS"
    prefetch_val = os.environ.get(prefetch_env_key) or os.environ.get(legacy_env_key)

    if prefetch_val is not None:
        try:
            prefetch = int(prefetch_val)
        except ValueError:
            print(
                f"[RabbitMQ] Invalid prefetch value {prefetch_val!r} for {jt.job_type}, using default."
            )
            prefetch = jt.prefetch_count
    else:
        prefetch = jt.prefetch_count

    conn = get_connection()
    if not conn:
        return

    ch = await conn.channel()
    await ch.set_qos(prefetch_count=prefetch)
    ex = await ch.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
    )

    try:
        q = await ch.declare_queue(
            jt.queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX_EXCHANGE_NAME,
                "x-dead-letter-routing-key": DLQ_ROUTING_KEY,
            },
        )
    except Exception:
        # Recreate channel and delete existing queue if arguments mismatch
        ch = await conn.channel()
        await ch.queue_delete(jt.queue_name)
        q = await ch.declare_queue(
            jt.queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": DLX_EXCHANGE_NAME,
                "x-dead-letter-routing-key": DLQ_ROUTING_KEY,
            },
        )

    await q.bind(ex, routing_key=jt.routing_key)
    for rk in jt.retry_routing_keys:
        await q.bind(ex, routing_key=rk)

    print(f"[RabbitMQ] Consumer ready: queue={jt.queue_name} prefetch={prefetch}")

    async def on_message(msg: aio_pika.IncomingMessage):
        import time

        from observability import set_correlation_id
        from observability.metrics import (
            job_execution_duration_seconds,
            job_execution_total,
        )

        corr_id = msg.headers.get("correlation_id")
        set_correlation_id(corr_id)

        start_time = time.time()
        status = "success"
        job_id = "?"
        p = {}

        try:
            p = json.loads(msg.body)
            job_id = p.get("job_id", "?")

            # Only check for cancellation/deletion on execution tasks (quick-scan, repo-scan).
            # Checking this on delete-scan would cause the deletion task itself to be discarded.
            if jt.job_type in (
                "quick-scan",
                "repo-scan",
            ) and is_job_cancelled_or_deleted(app_state, job_id):
                print(
                    f"[Cancellation] Job {job_id[:8]} was cancelled or deleted before execution. Discarding message."
                )
                await msg.ack()
                status = "cancelled"
                return

            if jt.job_type in ("quick-scan", "repo-scan"):
                current_task = asyncio.current_task()
                app_state.active_tasks[job_id] = current_task

            if jt.job_type == "quick-scan":
                print(f"[RabbitMQ][quick-scan] job={job_id[:8]}")
                from sandboxes.router import run_scan_in_background

                await run_scan_in_background(job_id, p["req_dict"])
            elif jt.job_type == "repo-scan":
                print(
                    f"[RabbitMQ][repo-scan] job={job_id[:8]} {p['owner']}/{p['repo']}"
                )
                from scan_repository.scan_repository import _run_scan_pipeline

                git_token = p.get("git_token")
                ssh_key = p.get("ssh_key")
                await _run_scan_pipeline(
                    job_id,
                    p["repo_url"],
                    p["owner"],
                    p["repo"],
                    app_state,
                    git_token=git_token,
                    ssh_key=ssh_key,
                )
            elif jt.job_type == "email-notification":
                print(
                    f"[RabbitMQ][email-notification] job={job_id[:8]} recipient={p.get('recipient')}"
                )
                from services.email import send_expiry_email

                await send_expiry_email(p)
            elif jt.job_type == "delete-scan":
                print(
                    f"[RabbitMQ][delete-scan] job={job_id[:8]} purge={p.get('purge')}"
                )
                from core.queue.delete_handler import handle_delete_job

                await handle_delete_job(app_state, job_id, p.get("purge", False))

            await msg.ack()

        except asyncio.CancelledError:
            print(f"[Cancellation] Message processing cancelled for job {job_id[:8]}")
            status = "cancelled"
            try:
                await msg.ack()
            except Exception:
                pass

        except Exception as e:
            print(f"[RabbitMQ] Worker execution failure: {e}")
            status = "failure"
            try:
                await handle_worker_failure(msg, p, e, jt.routing_key, app_state)
            except Exception as retry_err:
                print(f"[RabbitMQ] Error while executing retry handler: {retry_err}")
                await msg.reject(requeue=False)
                app_state.queue_stats.record_processed("scan.failed")

        finally:
            if "job_id" in locals() and job_id != "?":
                app_state.active_tasks.pop(job_id, None)
            app_state.queue_stats.record_processed(jt.queue_name)

            duration = time.time() - start_time
            # Record Prometheus metrics
            job_execution_duration_seconds.labels(job_type=jt.job_type).observe(
                duration
            )
            job_execution_total.labels(job_type=jt.job_type, status=status).inc()

            # Clean context correlation ID
            set_correlation_id(None)

    await q.consume(on_message)


async def start_all_consumers(app_state) -> None:
    conn = get_connection()
    if conn:
        async with conn.channel() as ch:
            await declare_dlq(ch)
            await declare_retry_topology(ch)

    await asyncio.gather(
        *[_start_single_consumer(jt, app_state) for jt in ALL_SCAN_JOB_TYPES]
    )
    print(f"[RabbitMQ] All {len(ALL_SCAN_JOB_TYPES)} consumers active.")
