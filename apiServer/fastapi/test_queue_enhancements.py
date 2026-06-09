#!/usr/bin/env python3
import os
import sys

# Set default env vars before importing core modules (which evaluate them at load time)
if not os.environ.get("RABBITMQ_URL"):
    os.environ["RABBITMQ_URL"] = "amqp://admin:changeme@rabbitmq-service:5672/"
if not os.environ.get("REDIS_HOST"):
    os.environ["REDIS_HOST"] = "redis-service"

import asyncio
import json
import uuid

# Inject current directory into python path to load core modules correctly
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import aio_pika
from core.app_state import state
from core.queue.connection import close_rabbitmq, connect_rabbitmq, get_connection
from core.queue.consumer import start_all_consumers
from core.queue.dlq import DLQ_QUEUE_NAME
from core.queue.publisher import publish
from core.queue.retry import RETRY_EXCHANGE_NAME


# Mock handlers to simulate execution failure or cancellation
async def run_scan_in_background_mock(job_id: str, req_dict: dict):
    if req_dict.get("simulate_error"):
        raise ValueError("Simulated processing error for retry testing")
    if req_dict.get("simulate_cancel"):
        # Simulate long running task that gets cancelled
        await asyncio.sleep(10.0)


async def test_retry_and_dlq(conn, app_state):
    print("\n--- Test 1: Testing Retry with Backoff and DLQ routing ---")
    job_id_retry = str(uuid.uuid4())
    payload_retry = {"job_id": job_id_retry, "req_dict": {"simulate_error": True}}

    app_state.job_tracker.create_job(job_id_retry, "quick-scan", {})

    # 1. Publish message that will fail and verify the first retry backoff
    print(f"Publishing failing job to test retry backoff: {job_id_retry[:8]}")
    await publish("scan.quick", payload_retry)

    # We wait about 7 seconds to see the first retry finish (5s delay)
    print("Waiting 7 seconds to observe the first retry (5s backoff)...")
    for i in range(7):
        await asyncio.sleep(1)
        job = app_state.job_tracker.get_job(job_id_retry)
        if job:
            print(f"Time {i+1}s: Job Step = {job.step}")

    # 2. Publish message that already has 3 retries to test DLQ routing immediately
    job_id_dlq = str(uuid.uuid4())
    payload_dlq = {
        "job_id": job_id_dlq,
        "req_dict": {"simulate_error": True},
        "retry_count": 3,
    }
    app_state.job_tracker.create_job(job_id_dlq, "quick-scan", {})

    print(
        f"\nPublishing failing job with 3 existing retries to test DLQ: {job_id_dlq[:8]}"
    )
    await publish("scan.quick", payload_dlq)

    # Wait 2 seconds for it to fail and route to DLQ
    await asyncio.sleep(2)

    job = app_state.job_tracker.get_job(job_id_dlq)
    print(f"DLQ Job Step: {job.step if job else 'None'} (Expected: ERROR)")

    # Verify DLQ
    ch = await conn.channel()
    dlq_queue = await ch.declare_queue(DLQ_QUEUE_NAME, durable=True)
    msg = await dlq_queue.get(no_ack=True)
    if msg:
        dlq_payload = json.loads(msg.body)
        print(f"SUCCESS: DLQ contains failed job {dlq_payload.get('job_id')[:8]}")
    else:
        print(
            "WARNING: DLQ is empty (perhaps delay not finished or queue arguments mismatch)"
        )


async def test_cancellation(conn, app_state):
    print("\n--- Test 2: Testing Job Cancellation ---")
    job_id = str(uuid.uuid4())
    payload = {"job_id": job_id, "req_dict": {"simulate_cancel": True}}

    app_state.job_tracker.create_job(job_id, "quick-scan", {})

    # Start consumer
    print(f"Publishing cancellable job: {job_id[:8]}")
    await publish("scan.quick", payload)

    await asyncio.sleep(2)  # Let it start

    # Set cancel flag and publish cancellation
    print(f"Cancelling job {job_id[:8]}")
    if app_state.use_redis and app_state.redis_client:
        app_state.redis_client.set(f"job:{job_id}:cancelled", "true", ex=60)
        app_state.redis_client.publish("job:cancellations", job_id)
    else:
        # Local fallback
        from core.queue.cancellation import cancel_active_task

        await cancel_active_task(app_state, job_id)

    await asyncio.sleep(2)
    job = app_state.job_tracker.get_job(job_id)
    print(f"Final Job Step: {job.step if job else 'None'} (Expected: CANCELLED)")


async def main():
    # Patch the real function to use mock for testing
    import sandboxes.router as router

    router.run_scan_in_background = run_scan_in_background_mock

    # Setup connection
    print("Connecting to RabbitMQ...")
    conn = await connect_rabbitmq()
    if not conn:
        print("ERROR: RabbitMQ connection failed. Ensure RABBITMQ_URL env is set.")
        return

    # Start consumers
    print("Starting workers...")
    await start_all_consumers(state)

    # Start Redis Pub/Sub cancellation listener if Redis is active
    if state.use_redis and state.redis_client:
        from core.queue.cancellation import setup_cancellation_listener

        asyncio.create_task(setup_cancellation_listener(state))

    # Run tests
    try:
        await test_retry_and_dlq(conn, state)
        await test_cancellation(conn, state)
    finally:
        await close_rabbitmq()
        print("Closed connection.")


if __name__ == "__main__":
    asyncio.run(main())
