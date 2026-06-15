from __future__ import annotations

import json

import aio_pika

from .connection import get_connection
from .job_types import EXCHANGE_NAME

RETRY_EXCHANGE_NAME = "scan_jobs.retry"


async def declare_retry_topology(channel: aio_pika.Channel) -> None:
    """Declares retry exchange and delay queues with message TTL and DLX back to main exchange."""
    # 1. Declare Direct Exchange for Retry
    retry_ex = await channel.declare_exchange(
        RETRY_EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
    )

    # 2. Define delay queues with TTL and DLX parameters
    delays = [
        (
            "scan.retry.5s",
            5000,
            ["scan.quick.5s", "scan.repo.5s", "notification.email.5s"],
        ),
        (
            "scan.retry.30s",
            30000,
            ["scan.quick.30s", "scan.repo.30s", "notification.email.30s"],
        ),
        (
            "scan.retry.2m",
            120000,
            ["scan.quick.2m", "scan.repo.2m", "notification.email.2m"],
        ),
    ]

    for q_name, ttl_ms, binding_keys in delays:
        q = await channel.declare_queue(
            q_name,
            durable=True,
            arguments={
                "x-message-ttl": ttl_ms,
                "x-dead-letter-exchange": EXCHANGE_NAME,  # DLX back to main exchange
            },
        )
        for key in binding_keys:
            await q.bind(retry_ex, routing_key=key)
            print(
                f"[RabbitMQ] Bound delay queue {q_name} to {RETRY_EXCHANGE_NAME} with key {key}"
            )


async def handle_worker_failure(
    msg: aio_pika.IncomingMessage,
    payload: dict,
    error: Exception,
    routing_key: str,
    app_state,
) -> None:
    """Interceptor to handle worker failures: schedules retries with backoff or rejects to DLQ."""
    job_id = payload.get("job_id", "unknown")
    retry_count = payload.get("retry_count", 0) + 1
    payload["retry_count"] = retry_count

    if retry_count <= 3:
        if retry_count == 1:
            suffix = "5s"
            progress = 15
        elif retry_count == 2:
            suffix = "30s"
            progress = 20
        else:
            suffix = "2m"
            progress = 25

        retry_routing_key = f"{routing_key}.{suffix}"

        await app_state.job_tracker.push_event(
            job_id,
            "RETRYING",
            f"Retry {retry_count}/3 (backing off): {error}",
            progress,
        )

        conn = get_connection()
        if not conn:
            raise RuntimeError("RabbitMQ connection not available for retry")

        async with conn.channel() as ch:
            retry_ex = await ch.declare_exchange(
                RETRY_EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
            )
            retry_msg = aio_pika.Message(
                body=json.dumps(payload).encode(),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
            )
            await retry_ex.publish(retry_msg, routing_key=retry_routing_key)
            print(
                f"[RabbitMQ] Re-routed job={job_id[:8]} to delay queue via key={retry_routing_key}"
            )

        # Acknowledge the original message because we have successfully rescheduled it
        await msg.ack()
    else:
        print(f"[RabbitMQ] Retry limit exceeded for job={job_id[:8]}. Routing to DLQ.")
        await app_state.job_tracker.push_event(
            job_id,
            "ERROR",
            f"Scan failed after 3 retries. Final error: {error}",
            0,
        )
        # Reject the message to route it to the DLX
        await msg.reject(requeue=False)
        app_state.queue_stats.record_processed("scan.failed")
