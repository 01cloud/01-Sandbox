import asyncio

from core.queue.cancellation import cancel_active_task


async def handle_delete_job(state, job_id: str, purge: bool = False) -> None:
    """
    Handles cancellation, remote PVC/sandbox cleanup, and state purging for a job.
    Called asynchronously when a scan.delete event is consumed.
    """
    # 1. Set Redis cancellation flag and publish to Redis Pub/Sub channels for cross-pod coordination
    if state.use_redis and state.redis_client:
        try:
            state.redis_client.set(f"job:{job_id}:cancelled", "true", ex=86400)
            if purge:
                state.redis_client.publish("job:deletions", job_id)
            else:
                state.redis_client.publish("job:cancellations", job_id)
            print(
                f"[RabbitMQ Delete] Published cancellation/deletion to Redis for job {job_id}"
            )
        except Exception as e:
            print(
                f"[RabbitMQ Delete] Redis cancellation publish failed for job {job_id}: {e}"
            )

    # 2. Trigger local active task cancellation
    await cancel_active_task(state, job_id)

    # 3. Trigger backend PVC/sandbox deletion in a thread executor
    try:
        await asyncio.to_thread(state.backend.delete_scan_job, job_id, terminate=True)
        print(f"[RabbitMQ Delete] Deleted PVC and sandboxes for job {job_id}")
    except Exception as e:
        print(f"[RabbitMQ Delete] PVC/sandbox deletion failed for job {job_id}: {e}")

    # 4. If purge=True, delete metadata, status, events from memory-based job_tracker
    if purge:
        state.job_tracker.delete_job(job_id)
        print(f"[RabbitMQ Delete] Purged job {job_id} from job tracker.")
