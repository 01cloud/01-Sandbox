import asyncio

from core.queue.cancellation import cancel_active_task


async def handle_delete_job(state, job_id: str, purge: bool = False) -> None:
    """
    Handles cancellation, remote PVC/sandbox cleanup, and state purging for a job.
    Called asynchronously when a scan.delete event is consumed.
    """
    # 1. Trigger local active task cancellation
    await cancel_active_task(state, job_id)

    # 2. Trigger backend PVC/sandbox deletion in a thread executor
    try:
        await asyncio.to_thread(state.backend.delete_scan_job, job_id, terminate=True)
        print(f"[RabbitMQ Delete] Deleted PVC and sandboxes for job {job_id}")
    except Exception as e:
        print(f"[RabbitMQ Delete] PVC/sandbox deletion failed for job {job_id}: {e}")

    # 3. If purge=True, delete metadata, status, events from memory-based job_tracker
    if purge:
        state.job_tracker.delete_job(job_id)
        print(f"[RabbitMQ Delete] Purged job {job_id} from job tracker.")
