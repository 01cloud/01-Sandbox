from __future__ import annotations

import aio_pika

DLX_EXCHANGE_NAME = "scan_jobs.dlx"
DLQ_QUEUE_NAME = "scan.failed"
DLQ_ROUTING_KEY = "scan.failed"


async def declare_dlq(channel: aio_pika.Channel) -> None:
    """Declares the Dead Letter Exchange and Queue topologies."""
    # Declare Direct Exchange for DLX
    dlx_ex = await channel.declare_exchange(
        DLX_EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
    )
    # Declare durable queue for failed scans
    dlq_queue = await channel.declare_queue(DLQ_QUEUE_NAME, durable=True)
    # Bind queue to exchange
    await dlq_queue.bind(dlx_ex, routing_key=DLQ_ROUTING_KEY)
    print(
        f"[RabbitMQ] DLQ setup complete: {DLQ_QUEUE_NAME} bound to {DLX_EXCHANGE_NAME}"
    )
