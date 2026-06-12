from __future__ import annotations

import json

import aio_pika
from core.queue.connection import get_connection
from core.queue.dlq import DLQ_QUEUE_NAME
from core.queue.job_types import EXCHANGE_NAME


async def requeue_failed_jobs(job_id: str | None = None) -> dict[str, int | bool]:
    """
    Re-queues failed jobs from the DLQ (scan.failed) back to the main scan_jobs exchange.
    If job_id is provided, only that specific job is re-queued, leaving other messages in the DLQ.
    Otherwise, all jobs in the DLQ are re-queued.
    """
    conn = get_connection()
    if not conn or conn.is_closed:
        raise RuntimeError("RabbitMQ not connected.")

    requeued_count = 0
    retained_count = 0
    non_matching_messages = []

    async with conn.channel() as ch:
        # Declare DLQ passively to check depth
        dlq = await ch.declare_queue(DLQ_QUEUE_NAME, durable=True)
        # Declare Main Exchange
        main_ex = await ch.declare_exchange(
            EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
        )

        # Retrieve current message count to inspect
        queue_depth = dlq.declaration_result.message_count

        for _ in range(queue_depth):
            message = await dlq.get(fail=False)
            if message is None:
                break

            try:
                body_dict = json.loads(message.body.decode())
            except Exception:
                # If payload is corrupted, keep it in the DLQ
                non_matching_messages.append(message)
                retained_count += 1
                continue

            current_job_id = body_dict.get("job_id")

            should_requeue = False
            if job_id is None:
                should_requeue = True
            elif current_job_id == job_id:
                should_requeue = True

            if should_requeue:
                # Reset retries count so it gets retried again in active queue
                body_dict["retry_count"] = 0

                # Determine the original routing key from x-death header
                original_routing_key = None
                if message.headers and "x-death" in message.headers:
                    x_death = message.headers["x-death"]
                    if x_death and isinstance(x_death, list):
                        # The first entry contains the latest routing keys
                        routing_keys = x_death[0].get("routing-keys")
                        if routing_keys and isinstance(routing_keys, list):
                            original_routing_key = routing_keys[0]

                # Fallback based on job type if no original routing key was resolved
                if not original_routing_key:
                    job_type = body_dict.get("job_type")
                    if job_type == "quick-scan":
                        original_routing_key = "scan.quick"
                    else:
                        original_routing_key = "scan.repo"

                # Publish back to main exchange
                new_msg = aio_pika.Message(
                    body=json.dumps(body_dict).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    content_type="application/json",
                    headers=message.headers,
                )
                await main_ex.publish(new_msg, routing_key=original_routing_key)

                # Acknowledge the old message in the DLQ
                await message.ack()
                requeued_count += 1
            else:
                non_matching_messages.append(message)
                retained_count += 1

        # Put back non-matching messages
        for msg in non_matching_messages:
            # Reject with requeue=True puts the message back at the end of the queue
            # (which retains the exact headers and original properties)
            await msg.reject(requeue=True)

    return {
        "success": True,
        "requeued": requeued_count,
        "retained": retained_count,
    }
