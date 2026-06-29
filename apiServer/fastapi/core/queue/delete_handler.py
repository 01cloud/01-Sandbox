import asyncio

from core.delete_job import perform_job_deletion


async def handle_delete_job(state, job_id: str, purge: bool = False) -> None:
    """
    Handles cancellation, remote PVC/sandbox cleanup, and state purging for a job.
    Called asynchronously when a scan.delete event is consumed.
    """
    await perform_job_deletion(state, job_id, purge=purge)
