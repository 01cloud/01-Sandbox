from auth import validate_token
from core import state
from core.queue.connection import get_connection
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/v1/queue", tags=["Queue Metrics"])


@router.get("/stats")
async def get_queue_stats(user_data: dict = Depends(validate_token)):
    """
    Returns live statistics for the RabbitMQ queues:
    - Message depth (live queue depth)
    - Active consumer count
    - Throughput (messages processed per second in the last 60 seconds)
    """
    conn = get_connection()
    if not conn or conn.is_closed:
        return {
            "available": False,
            "queues": {},
        }

    queues_to_query = ["scan.quick", "scan.repo", "scan.failed"]
    stats = {}

    for q_name in queues_to_query:
        depth = 0
        consumers = 0
        try:
            # Open a short-lived channel to declare the queue passively.
            # This is robust because if one queue fails to declare, it doesn't affect others.
            async with conn.channel() as ch:
                q = await ch.declare_queue(q_name, passive=True)
                depth = q.declaration_result.message_count
                consumers = q.declaration_result.consumer_count
        except Exception as e:
            # Queue might not exist yet or connection issue
            print(f"[Queue Stats] Failed to query queue '{q_name}' passively: {e}")
            pass

        throughput = state.queue_stats.get_throughput(q_name)
        stats[q_name] = {
            "depth": depth,
            "consumers": consumers,
            "throughput": throughput,
        }

    return {
        "available": True,
        "queues": stats,
    }
