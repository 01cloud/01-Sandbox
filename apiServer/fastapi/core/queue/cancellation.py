from __future__ import annotations

import asyncio

import redis


async def setup_cancellation_listener(app_state) -> None:
    """Listens to Redis Pub/Sub for job cancellation and deletion requests."""
    if not app_state.use_redis or not app_state.redis_client:
        print(
            "[Cancellation] Redis not available — cross-pod job cancellation/deletion disabled."
        )
        return

    print("[Cancellation] Starting Redis Pub/Sub cancellation/deletion listener...")
    pubsub = app_state.redis_client.pubsub()
    pubsub.subscribe("job:cancellations", "job:deletions")

    while True:
        try:
            # Polling get_message with timeout to keep it non-blocking
            msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg:
                channel = msg.get("channel")
                job_id = msg.get("data")
                if channel == "job:cancellations":
                    print(
                        f"[Cancellation] Received cancellation request for job: {job_id}"
                    )
                    await cancel_active_task(app_state, job_id)
                elif channel == "job:deletions":
                    print(f"[Deletion] Received deletion request for job: {job_id}")
                    # 1. Cancel task first
                    await cancel_active_task(app_state, job_id)
                    # 2. Purge from in-memory tracker
                    if job_id in app_state.job_tracker._jobs:
                        del app_state.job_tracker._jobs[job_id]
        except Exception as e:
            print(f"[Cancellation] Listener error: {e}")
        await asyncio.sleep(0.5)


async def cancel_active_task(app_state, job_id: str) -> None:
    """Cancels a running asyncio task for a given job ID if it exists on this pod."""
    task = app_state.active_tasks.get(job_id)
    if task:
        print(f"[Cancellation] Cancelling running task for job {job_id}")
        task.cancel()
        await app_state.job_tracker.push_event(
            job_id, "CANCELLED", "Job was cancelled by the user.", 0
        )
    else:
        # If it is not running on this pod, it might be in the queue or on another pod.
        # We update the tracker's status just in case we own the tracker.
        job = app_state.job_tracker.get_job(job_id)
        if job and job.step != "CANCELLED":
            await app_state.job_tracker.push_event(
                job_id, "CANCELLED", "Job was cancelled by the user.", 0
            )


def is_job_cancelled(app_state, job_id: str) -> bool:
    """Checks if a job has been flagged as cancelled in Redis."""
    if app_state.use_redis and app_state.redis_client:
        try:
            return app_state.redis_client.get(f"job:{job_id}:cancelled") == "true"
        except Exception as e:
            print(f"[Cancellation] Failed to check status in Redis: {e}")
    return False
