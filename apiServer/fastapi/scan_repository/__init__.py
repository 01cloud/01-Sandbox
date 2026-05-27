"""
scan_repository — GitHub Repository Scanner sub-package.

Exposes a factory function get_repo_scan_router(state, validate_token)
that mirrors the health.py pattern — same as get_health_router().
"""

from .scan_repository import get_repo_scan_router

__all__ = ["get_repo_scan_router"]
