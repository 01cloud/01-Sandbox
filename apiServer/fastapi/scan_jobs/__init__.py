"""
scan_jobs — Scan Jobs (Quick Scan) Pipeline and Management package.

Exposes a factory function get_scan_jobs_router(state, validate_token)
to manage scan job submission, reports, status, and cancellation/deletion.
"""

from .router import get_scan_jobs_router

__all__ = ["get_scan_jobs_router"]
