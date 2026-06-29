import asyncio

from core.queue.cancellation import cancel_active_task


async def perform_job_deletion(state, job_id: str, purge: bool = False) -> None:
    """
    Modular utility to handle job cancellation and PVC/sandbox cleanup.
    Can be reused by RabbitMQ handlers, HTTP routers, or any other scan type (e.g. Quick Scan or Repo Scan).
    """
    # 1. Gather all child job IDs associated with this parent job (for cascading deletion)
    child_job_ids = set()
    try:
        from scan_repository.file_scanner import (
            active_child_jobs_by_parent,
            all_child_jobs_by_parent,
        )

        if job_id in all_child_jobs_by_parent:
            child_job_ids.update(all_child_jobs_by_parent[job_id])
        if job_id in active_child_jobs_by_parent:
            child_job_ids.update(active_child_jobs_by_parent[job_id])
    except Exception as err:
        print(f"[Delete Job] Failed to import child job tracking maps: {err}")

    if state.use_redis and state.redis_client:
        try:
            redis_children = state.redis_client.smembers(f"job:{job_id}:child_jobs")
            if redis_children:
                child_job_ids.update(
                    c.decode("utf-8") if isinstance(c, bytes) else c
                    for c in redis_children
                )
        except Exception as redis_err:
            print(f"[Delete Job] Failed to fetch child job IDs from Redis: {redis_err}")

    # 2. Publish cancellation/deletion to Redis for cross-pod coordination
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

    # 3. Cancel the local active task running in memory
    await cancel_active_task(state, job_id, purge=purge)

    # 4. Request remote PVC and sandbox container cleanup
    try:
        await asyncio.to_thread(state.backend.delete_scan_job, job_id, terminate=True)
        print(f"[Delete Job] Deleted PVC and sandboxes for job {job_id}")
    except Exception as e:
        print(f"[Delete Job] PVC/sandbox deletion failed for job {job_id}: {e}")

    # 5. Cascading deletion of all identified child jobs
    for cid in child_job_ids:
        print(f"[Delete Job] Cascading cancellation/deletion to child job: {cid}")
        await cancel_active_task(state, cid, purge=purge)
        try:
            await asyncio.to_thread(state.backend.delete_scan_job, cid, terminate=True)
            print(f"[Delete Job] Cascaded deletion of child job {cid}")
        except Exception as e:
            print(f"[Delete Job] Cascaded deletion failed for child job {cid}: {e}")

        if purge:
            state.job_tracker.delete_job(cid)
            if state.use_redis and state.redis_client:
                try:
                    state.redis_client.delete(f"job:{cid}:status")
                    state.redis_client.delete(f"job:{cid}:metadata")
                    state.redis_client.delete(f"job:{cid}:result")
                    state.redis_client.delete(f"job:{cid}:events")
                except Exception:
                    pass

    # 6. If purge=True, remove job records from the local/in-memory job tracker
    if purge:
        state.job_tracker.delete_job(job_id)
        print(f"[Delete Job] Purged job {job_id} from job tracker.")

        # Clean up Redis and in-memory child tracking info
        if state.use_redis and state.redis_client:
            try:
                state.redis_client.delete(f"job:{job_id}:child_jobs")
            except Exception:
                pass
        try:
            from scan_repository.file_scanner import (
                active_child_jobs_by_parent,
                all_child_jobs_by_parent,
            )

            all_child_jobs_by_parent.pop(job_id, None)
            active_child_jobs_by_parent.pop(job_id, None)
        except Exception:
            pass
