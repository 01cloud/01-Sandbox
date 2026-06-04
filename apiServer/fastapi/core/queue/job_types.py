from dataclasses import dataclass

EXCHANGE_NAME = "scan_jobs"


@dataclass(frozen=True)
class ScanJobType:
    job_type: str
    queue_name: str
    routing_key: str
    prefetch_count: int


QUICK_SCAN = ScanJobType("quick-scan", "scan.quick", "scan.quick", 5)
REPO_SCAN = ScanJobType("repo-scan", "scan.repo", "scan.repo", 3)

ALL_SCAN_JOB_TYPES = [QUICK_SCAN, REPO_SCAN]
