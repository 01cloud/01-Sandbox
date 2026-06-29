import asyncio

from core.queue.cancellation import cancel_active_task


async def perform_job_deletion(state, job_id: str, purge: bool = False) -> None:
    """
    Modular utility to handle job cancellation and PVC/sandbox cleanup.
    Can be reused by RabbitMQ handlers, HTTP routers, or any other scan type (e.g. Quick Scan or Repo Scan).
    """
    # 1. Publish cancellation/deletion to Redis for cross-pod coordination
    if state.use_redis and state.redis_client:
        try:
            state.redis_client.set(f"job:{job_id}:cancelled", "true", ex=86400)
            if purge:
                state.redis_client.publish("job:deletions", job_id)
            else:
                state.redis_client.publish("job:cancellations", job_id)
            print(
                f"[Delete Job] Published cancellation/deletion to Redis for job {job_id}"
            )
        except Exception as e:
            print(
                f"[Delete Job] Redis cancellation publish failed for job {job_id}: {e}"
            )

    # 2. Cancel the local active task running in memory
    await cancel_active_task(state, job_id, purge=purge)

    # 3. Request remote PVC and sandbox container cleanup
    try:
        await asyncio.to_thread(state.backend.delete_scan_job, job_id, terminate=True)
        print(f"[Delete Job] Deleted PVC and sandboxes for job {job_id}")
    except Exception as e:
        print(f"[Delete Job] PVC/sandbox deletion failed for job {job_id}: {e}")

    # 4. If purge=True, remove job records from the local/in-memory job tracker
    if purge:
        state.job_tracker.delete_job(job_id)
        print(f"[Delete Job] Purged job {job_id} from job tracker.")
