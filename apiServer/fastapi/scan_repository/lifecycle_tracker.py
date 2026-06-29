import asyncio

from .file_scanner import current_parent_job_id


def register_pipeline_task(job_id: str, app_state):
    """
    Sets the parent job ID context and registers the task in app_state.active_tasks.
    Returns the context token to be used for unregistering.
    """
    # 1. Set the current parent job ID context
    token = current_parent_job_id.set(job_id)

    # 2. Register the current task in active_tasks for cancellation support
    try:
        current_task = asyncio.current_task()
        if current_task and hasattr(app_state, "active_tasks"):
            app_state.active_tasks[job_id] = current_task
            print(f"[RepoScanner][Lifecycle] Registered task for job {job_id}")
    except Exception as e:
        print(f"[RepoScanner][Lifecycle] Failed to register task for job {job_id}: {e}")

    return token


def unregister_pipeline_task(job_id: str, app_state, token) -> None:
    """
    Unregisters the task from app_state.active_tasks and resets parent job context.
    """
    # 3. Clean up the task registry on exit
    try:
        if hasattr(app_state, "active_tasks"):
            app_state.active_tasks.pop(job_id, None)
            print(f"[RepoScanner][Lifecycle] Unregistered task for job {job_id}")
    except Exception as e:
        print(
            f"[RepoScanner][Lifecycle] Failed to unregister task for job {job_id}: {e}"
        )

    # 4. Reset the parent job ID context
    try:
        current_parent_job_id.reset(token)
        print(f"[RepoScanner][Lifecycle] Reset parent job context for job {job_id}")
    except Exception as e:
        print(
            f"[RepoScanner][Lifecycle] Failed to reset context token for job {job_id}: {e}"
        )
