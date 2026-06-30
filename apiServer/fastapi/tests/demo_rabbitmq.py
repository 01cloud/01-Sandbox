#!/usr/bin/env python3
import asyncio
import os
import sys
import time
import uuid

import aio_pika

# ANSI Escape Codes for colored terminal output
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://admin:changeme@localhost:5672/")

# Demo Exchange and Queue Names
EXCHANGE_NAME = "demo_scan_jobs"
RETRY_EXCHANGE_NAME = "demo_scan_jobs.retry"
DLX_EXCHANGE_NAME = "demo_scan_jobs.dlx"

QUEUE_MAIN = "demo.scan.repo"
QUEUE_RETRY_2S = "demo.scan.retry.2s"
QUEUE_RETRY_5S = "demo.scan.retry.5s"
QUEUE_DLQ = "demo.scan.failed"


async def setup_topology(channel):
    """Declares exchanges, queues, and bindings mirroring production topology."""
    # 1. Declare Exchanges
    main_exchange = await channel.declare_exchange(
        EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
    )
    retry_exchange = await channel.declare_exchange(
        RETRY_EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
    )
    dlx_exchange = await channel.declare_exchange(
        DLX_EXCHANGE_NAME, aio_pika.ExchangeType.TOPIC, durable=True
    )

    # 2. Declare Queues
    # Main Queue with DLX configuration (routes rejected messages to DLX)
    main_queue = await channel.declare_queue(
        QUEUE_MAIN,
        durable=True,
        arguments={
            "x-dead-letter-exchange": DLX_EXCHANGE_NAME,
        },
    )
    await main_queue.bind(main_exchange, routing_key="scan.repo")

    # Retry/Delay Queues (TTL expires and dead-letters back to main exchange)
    retry_2s_queue = await channel.declare_queue(
        QUEUE_RETRY_2S,
        durable=True,
        arguments={
            "x-message-ttl": 2000,  # 2 seconds (accelerated for demo)
            "x-dead-letter-exchange": EXCHANGE_NAME,
        },
    )
    await retry_2s_queue.bind(retry_exchange, routing_key="scan.repo.2s")

    retry_5s_queue = await channel.declare_queue(
        QUEUE_RETRY_5S,
        durable=True,
        arguments={
            "x-message-ttl": 5000,  # 5 seconds (accelerated for demo)
            "x-dead-letter-exchange": EXCHANGE_NAME,
        },
    )
    await retry_5s_queue.bind(retry_exchange, routing_key="scan.repo.5s")

    # Dead Letter Queue (DLQ)
    dlq = await channel.declare_queue(QUEUE_DLQ, durable=True)
    await dlq.bind(dlx_exchange, routing_key="scan.repo")

    return main_exchange, retry_exchange, dlx_exchange, main_queue, dlq


async def run_bulk_demo(connection):
    print(
        f"\n{BOLD}{CYAN}=== DEMO 1: BULK SCAN QUEUING & PREFETCH CONCURRENCY ==={RESET}"
    )
    print("Scenario: A client submits 20 repositories for scanning at once.")
    print("Resource Constraint: Server specs limit concurrency to 3 parallel scans.")
    print("Solution: RabbitMQ buffers the excess jobs, protecting the server.\n")

    channel = await connection.channel()
    main_exchange, _, _, main_queue, _ = await setup_topology(channel)

    # Clear queue first
    await main_queue.purge()

    # 1. Publish 20 jobs
    print(f"{BOLD}[1/3] Submitting 20 scan jobs to RabbitMQ...{RESET}")
    for i in range(1, 21):
        payload = f'{{"job_id": "{uuid.uuid4()}", "repo": "github.com/client-demo/repo-{i:02d}"}}'.encode()
        await main_exchange.publish(
            aio_pika.Message(
                body=payload, delivery_mode=aio_pika.DeliveryMode.PERSISTENT
            ),
            routing_key="scan.repo",
        )

    # Check depth
    q_declare = await channel.declare_queue(QUEUE_MAIN, passive=True)
    print(
        f"{GREEN}✔ Successfully queued {q_declare.declaration_result.message_count} scan jobs in queue: '{QUEUE_MAIN}'{RESET}\n"
    )

    # 2. Start Worker with Prefetch Limit = 3
    print(f"{BOLD}[2/3] Starting Mock Worker (Prefetch Limit = 3)...{RESET}")
    await channel.set_qos(prefetch_count=3)

    active_jobs = set()
    completed_count = 0

    async def process_message(message: aio_pika.IncomingMessage):
        nonlocal completed_count
        async with message.process():
            job_name = message.body.decode().split('"repo": "')[1].split('"')[0]
            active_jobs.add(job_name)

            # Print current snapshot
            print(
                f"  ⚡ {YELLOW}Processing:{RESET} {list(active_jobs)} | {CYAN}Queued Buffer:{RESET} {20 - completed_count - len(active_jobs)} jobs remaining"
            )

            # Simulate scan taking 1.5 seconds
            await asyncio.sleep(1.5)

            active_jobs.remove(job_name)
            completed_count += 1
            print(f"  ✔ {GREEN}Completed:{RESET} {job_name}")

    # Start consuming
    consumer_tag = await main_queue.consume(process_message)
    print(f"{GREEN}✔ Worker connected and consuming scans...{RESET}\n")

    # Let it run for 10 seconds to showcase the flow
    await asyncio.sleep(10.0)

    # Clean up
    await main_queue.cancel(consumer_tag)
    # Wait for any active jobs to finish to avoid asyncio.CancelledError on channel close
    while active_jobs:
        await asyncio.sleep(0.1)
    await channel.close()
    print(f"\n{BOLD}{GREEN}=== DEMO 1 COMPLETE ==={RESET}")
    print(
        f"Result: The server handled the load gracefully. No crashes, no scheduling timeouts."
    )
    print(f"Queue buffer absorbed the spike, and only 3 sandboxes ran concurrently.")


async def run_retry_demo(connection):
    print(
        f"\n{BOLD}{CYAN}=== DEMO 2: RETRY BACKOFF & DEAD LETTER QUEUE (DLQ) ISOLATION ==={RESET}"
    )
    print("Scenario: A scan fails (e.g., repository authentication or server crash).")
    print(
        "Solution: Automatic exponential retry (2s -> 5s delays), then quarantine to DLQ.\n"
    )

    channel = await connection.channel()
    main_exchange, retry_exchange, _, main_queue, dlq = await setup_topology(channel)

    # Clear queues
    await main_queue.purge()
    await dlq.purge()

    # 1. Publish 1 failing job
    print(f"{BOLD}[1/4] Submitting 1 faulty repository for scanning...{RESET}")
    payload = f'{{"job_id": "{uuid.uuid4()}", "repo": "github.com/bad-auth/private-repo", "retry_count": 0}}'.encode()
    await main_exchange.publish(
        aio_pika.Message(body=payload, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
        routing_key="scan.repo",
    )

    # 2. Start Worker that simulates failure
    async def process_failing_message(message: aio_pika.IncomingMessage):
        import json

        data = json.loads(message.body.decode())
        current_retry = data.get("retry_count", 0)
        repo = data.get("repo")

        print(
            f"  ⚡ {YELLOW}Worker received scan job for:{RESET} {repo} (Attempt {current_retry + 1})"
        )
        print(
            f"  ❌ {RED}Scan failed: Private repository requires authentication credentials.{RESET}"
        )

        if current_retry < 2:
            # Re-route to Retry Exchange with backoff key
            next_retry = current_retry + 1
            data["retry_count"] = next_retry
            new_payload = json.dumps(data).encode()

            # Decide backoff (2s then 5s)
            delay_key = "scan.repo.2s" if next_retry == 1 else "scan.repo.5s"
            delay_time = "2s" if next_retry == 1 else "5s"

            print(
                f"  ↳ {CYAN}Scheduling retry {next_retry}/3 with {delay_time} backoff...{RESET}"
            )

            # Publish to retry exchange
            await retry_exchange.publish(
                aio_pika.Message(
                    body=new_payload, delivery_mode=aio_pika.DeliveryMode.PERSISTENT
                ),
                routing_key=delay_key,
            )

            # Acknowledge original message to remove it from main queue
            await message.ack()
        else:
            # Final failure: Reject without requeue, moving it to DLQ via RabbitMQ topology
            print(
                f"  ↳ ☠ {RED}Max retries exceeded! Rejecting message to move it to DLQ...{RESET}"
            )
            await message.reject(requeue=False)

    consumer_tag = await main_queue.consume(process_failing_message)

    # Watch the flow run (takes ~8-10 seconds total)
    await asyncio.sleep(12.0)

    # 3. Verify DLQ
    print(f"\n{BOLD}[3/4] Checking Dead Letter Queue (DLQ)...{RESET}")
    q_declare = await channel.declare_queue(QUEUE_DLQ, passive=True)
    print(
        f"  → Queue '{QUEUE_DLQ}' current depth: {q_declare.declaration_result.message_count} message(s)"
    )

    if q_declare.declaration_result.message_count > 0:
        print(
            f"  ✔ {GREEN}Confirmed: The failing message was successfully isolated in the DLQ.{RESET}"
        )
    else:
        print(f"  ❌ {RED}Error: Message not found in DLQ.{RESET}")

    # Clean up
    await main_queue.cancel(consumer_tag)
    await channel.close()
    print(f"\n{BOLD}{GREEN}=== DEMO 2 COMPLETE ==={RESET}")
    print(
        "Result: Failed jobs do not block active pipelines. They back off gracefully and quarantine automatically."
    )


async def main():
    print(f"{BOLD}{CYAN}----------------------------------------------------")
    print("      RabbitMQ Enterprise Architecture Simulator    ")
    print(f"----------------------------------------------------{RESET}")

    try:
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        print(f"{GREEN}✔ Connected to RabbitMQ Broker successfully!{RESET}")
    except Exception as e:
        print(
            f"{RED}Error: Could not connect to RabbitMQ broker at {RABBITMQ_URL}: {e}{RESET}"
        )
        print(
            "Please ensure your RabbitMQ server/pod is running and RABBITMQ_URL is set correctly."
        )
        sys.exit(1)

    try:
        await run_bulk_demo(connection)
        await asyncio.sleep(2.0)
        await run_retry_demo(connection)
    finally:
        await connection.close()
        print(
            f"\n{BOLD}{CYAN}Simulation completed. All temporary channels closed safely.{RESET}"
        )


if __name__ == "__main__":
    if len(sys.argv) > 1:
        RABBITMQ_URL = sys.argv[1]
    asyncio.run(main())
