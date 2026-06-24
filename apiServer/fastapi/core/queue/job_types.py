from dataclasses import dataclass

EXCHANGE_NAME = "scan_jobs"


@dataclass(frozen=True)
class ScanJobType:
    job_type: str
    queue_name: str
    routing_key: str
    prefetch_count: int
    retry_routing_keys: tuple[str, ...] = ()


QUICK_SCAN = ScanJobType(
    "quick-scan",
    "scan.quick",
    "scan.quick",
    5,
    ("scan.quick.5s", "scan.quick.30s", "scan.quick.2m"),
)
REPO_SCAN = ScanJobType(
    "repo-scan",
    "scan.repo",
    "scan.repo",
    3,
    ("scan.repo.5s", "scan.repo.30s", "scan.repo.2m"),
)
EMAIL_NOTIFICATION = ScanJobType(
    "email-notification",
    "notification.email",
    "notification.email",
    5,
    ("notification.email.5s", "notification.email.30s", "notification.email.2m"),
)
DELETE_SCAN = ScanJobType(
    "delete-scan",
    "scan.delete",
    "scan.delete",
    5,
    ("scan.delete.5s", "scan.delete.30s", "scan.delete.2m"),
)

ALL_SCAN_JOB_TYPES = [QUICK_SCAN, REPO_SCAN, EMAIL_NOTIFICATION, DELETE_SCAN]
