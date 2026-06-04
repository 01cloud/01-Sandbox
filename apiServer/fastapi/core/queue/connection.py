from __future__ import annotations

import os

import aio_pika

_connection = None
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "")


async def connect_rabbitmq():
    global _connection
    if not RABBITMQ_URL:
        print("[RabbitMQ] RABBITMQ_URL not set — fallback mode active.")
        return None
    _connection = await aio_pika.connect_robust(RABBITMQ_URL)
    print(f"[RabbitMQ] Connected: {RABBITMQ_URL.split('@')[-1]}")
    return _connection


async def close_rabbitmq():
    global _connection
    if _connection and not _connection.is_closed:
        await _connection.close()
        print("[RabbitMQ] Connection closed.")


def get_connection():
    return _connection


def is_available():
    return _connection is not None and not _connection.is_closed
