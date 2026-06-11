from __future__ import annotations

import asyncio
import os

import aio_pika

_connection = None
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "")


async def connect_rabbitmq():
    global _connection
    if not RABBITMQ_URL:
        print("[RabbitMQ] RABBITMQ_URL not set — fallback mode active.")
        return None

    max_retries = 10
    retry_delay = 3

    for attempt in range(1, max_retries + 1):
        try:
            _connection = await aio_pika.connect_robust(RABBITMQ_URL)
            print(
                f"[RabbitMQ] Connected on attempt {attempt}: {RABBITMQ_URL.split('@')[-1]}"
            )
            return _connection
        except Exception as e:
            if attempt == max_retries:
                print(f"[RabbitMQ] Failed to connect after {max_retries} attempts: {e}")
                raise
            print(
                f"[RabbitMQ] Connection attempt {attempt}/{max_retries} failed: {e}. Retrying in {retry_delay}s..."
            )
            await asyncio.sleep(retry_delay)


async def close_rabbitmq():
    global _connection
    if _connection and not _connection.is_closed:
        await _connection.close()
        print("[RabbitMQ] Connection closed.")


def get_connection():
    return _connection


def is_available():
    return _connection is not None and not _connection.is_closed
