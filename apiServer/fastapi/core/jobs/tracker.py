import asyncio
import json
import time
from collections import deque
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class JobEvent(BaseModel):
    job_id: str
    job_type: str
    step: str
    message: str
    progress: int
    detail: Optional[Any] = None


class GenericJobRecord:
    def __init__(self, job_id: str, job_type: str, metadata: dict):
        self.job_id = job_id
        self.job_type = job_type
        self.metadata = metadata
        self.queue: asyncio.Queue = asyncio.Queue()
        self.event_log: deque = deque(maxlen=2000)
        self.step: str = "QUEUED"
        self.result: Optional[Any] = None
        self.finished_at: Optional[float] = None


class ReusableJobTracker:
    def __init__(self, app_state):
        self.app_state = app_state
        self._jobs: Dict[str, GenericJobRecord] = {}

    def create_job(
        self, job_id: str, job_type: str, metadata: dict
    ) -> GenericJobRecord:
        record = GenericJobRecord(job_id, job_type, metadata)
        self._jobs[job_id] = record

        # Save metadata to Redis for persistence (enables multi-pod job listing)
        if self.app_state.use_redis and self.app_state.redis_client:
            r = self.app_state.redis_client
            try:
                metadata_copy = dict(metadata)
                metadata_copy["job_type"] = job_type
                r.set(f"job:{job_id}:metadata", json.dumps(metadata_copy), ex=86400)
                r.set(f"job:{job_id}:status", "QUEUED", ex=86400)
            except Exception as e:
                print(f"[tracker] Failed to save metadata to Redis: {e}")

        return record

    def get_job(self, job_id: str) -> Optional[GenericJobRecord]:
        return self._jobs.get(job_id)

    def delete_job(self, job_id: str):
        if job_id in self._jobs:
            del self._jobs[job_id]

        if self.app_state.use_redis and self.app_state.redis_client:
            r = self.app_state.redis_client
            try:
                r.delete(
                    f"job:{job_id}:metadata",
                    f"job:{job_id}:status",
                    f"job:{job_id}:events",
                    f"job:{job_id}:result",
                    f"job:{job_id}:cancelled",
                )
                r.publish("job:deletions", job_id)
            except Exception as e:
                print(f"[tracker] Failed to delete job from Redis: {e}")

    async def push_event(
        self,
        job_id: str,
        step: str,
        message: str,
        progress: int,
        detail: Optional[Any] = None,
    ):
        job = self._jobs.get(job_id)
        event = JobEvent(
            job_id=job_id,
            job_type=job.job_type if job else "unknown",
            step=step,
            message=message,
            progress=progress,
            detail=detail,
        )

        # Pydantic v1 / v2 compatible serialization
        if hasattr(event, "model_dump_json"):
            event_json = event.model_dump_json()
        else:
            event_json = event.json()

        # 1. Update in-memory job
        if job:
            if step != "LOG_LINE":
                job.step = step
            job.event_log.append(event)
            await job.queue.put(event)
            if step in ("DONE", "ERROR"):
                job.finished_at = time.monotonic()
                job.result = detail
                await job.queue.put(None)

        # 2. Update Redis
        if self.app_state.use_redis and self.app_state.redis_client:
            r = self.app_state.redis_client
            try:
                r.publish(f"job:{job_id}:chan", event_json)
                r.rpush(f"job:{job_id}:events", event_json)
                r.expire(f"job:{job_id}:events", 86400)
                if step != "LOG_LINE":
                    r.set(f"job:{job_id}:status", step, ex=86400)
                if step in ("DONE", "ERROR") and detail:
                    r.set(f"job:{job_id}:result", json.dumps(detail), ex=86400)
            except Exception as e:
                print(f"[tracker] Redis update failed for job {job_id}: {e}")

    async def stream(self, job_id: str, since_index: int = 0):
        job = self._jobs.get(job_id)

        # Redis Hydration & Streaming fallback (Multi-pod / crash recovery)
        if not job:
            if self.app_state.use_redis and self.app_state.redis_client:
                r = self.app_state.redis_client
                try:
                    # Fetch stored events
                    events_json = r.lrange(f"job:{job_id}:events", since_index, -1)
                    for ev_str in events_json:
                        yield f"data: {ev_str}\n\n"

                    # If finished, stop streaming
                    status = r.get(f"job:{job_id}:status")
                    if status in ("DONE", "ERROR"):
                        return

                    # Subscribe to channel for live updates
                    pubsub = r.pubsub()
                    pubsub.subscribe(f"job:{job_id}:chan")
                    try:
                        while True:
                            msg = pubsub.get_message(
                                ignore_subscribe_messages=True, timeout=1.0
                            )
                            if msg:
                                data = msg["data"]
                                yield f"data: {data}\n\n"
                                ev = json.loads(data)
                                if ev.get("step") in ("DONE", "ERROR"):
                                    break
                            else:
                                yield ": ping\n\n"
                                await asyncio.sleep(1.0)
                    finally:
                        pubsub.unsubscribe()
                except Exception as e:
                    yield f'data: {{"step":"ERROR","message":"Redis stream failed: {e}"}}\n\n'
                return
            else:
                yield f'data: {{"step":"ERROR","message":"Job not found"}}\n\n'
                return

        # Regular in-memory fast stream
        for event in list(job.event_log)[since_index:]:
            if hasattr(event, "model_dump_json"):
                yield f"data: {event.model_dump_json()}\n\n"
            else:
                yield f"data: {event.json()}\n\n"

        if job.finished_at:
            return

        while True:
            try:
                event = await asyncio.wait_for(job.queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:
                break

            if hasattr(event, "model_dump_json"):
                yield f"data: {event.model_dump_json()}\n\n"
            else:
                yield f"data: {event.json()}\n\n"

            if event.step in ("DONE", "ERROR"):
                break
