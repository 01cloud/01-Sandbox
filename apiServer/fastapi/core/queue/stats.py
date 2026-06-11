from __future__ import annotations

import time
from collections import deque
from typing import Dict


class QueueStatsTracker:
    """Tracks completed job processing timestamps to calculate rolling throughput."""

    def __init__(self):
        # Rolling deques to track message completion timestamps for the last 60 seconds
        self._processed_timestamps: Dict[str, deque[float]] = {
            "scan.quick": deque(),
            "scan.repo": deque(),
            "scan.failed": deque(),
        }

    def record_processed(self, queue_name: str):
        """Records a processed message timestamp for the specified queue."""
        now = time.time()
        if queue_name not in self._processed_timestamps:
            self._processed_timestamps[queue_name] = deque()
        self._processed_timestamps[queue_name].append(now)

    def get_throughput(self, queue_name: str, window_seconds: float = 60.0) -> float:
        """Trims older entries and returns messages processed per second in the window."""
        now = time.time()
        timestamps = self._processed_timestamps.get(queue_name)
        if not timestamps:
            return 0.0

        # Prune elements older than window_seconds
        cutoff = now - window_seconds
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        rate = len(timestamps) / window_seconds
        return round(rate, 2)
