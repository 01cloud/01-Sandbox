from __future__ import annotations

import json

import aio_pika

from .connection import get_connection
from .job_types import EXCHANGE_NAME


async def publish(routing_key: str, payload: dict) -> None:
    conn = get_connection()
    if not conn or conn.is_closed:
        raise RuntimeError("RabbitMQ not connected.")
    async with conn.channel() as ch:
        ex = await ch.declare_exchange(
            EXCHANGE_NAME, aio_pika.ExchangeType.DIRECT, durable=True
        )
        msg = aio_pika.Message(
            body=json.dumps(payload).encode(),
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
        )
        await ex.publish(msg, routing_key=routing_key)
        print(
            f"[RabbitMQ] Published [{routing_key}] job={payload.get('job_id', '?')[:8]}"
        )
