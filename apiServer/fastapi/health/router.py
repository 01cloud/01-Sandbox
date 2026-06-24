from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Response, status

from .models import DependencyStatus, HealthResponse


def check_postgresql_health(state) -> tuple[bool, str]:
    """Lightweight and production-safe health check for the PostgreSQL database."""
    try:
        conn = state.get_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1;")
        cursor.fetchone()
        conn.close()
        return (
            True,
            "PostgreSQL Connected",
        )
    except Exception as e:
        return False, f"Database error: {str(e)}"


def check_redis_health(state) -> tuple[bool, str]:
    """Lightweight and production-safe health check for the Redis cache/queue dependency."""
    if not state.use_redis:
        return True, "Disabled"
    try:
        if state.redis_client and state.redis_client.ping():
            return True, "Redis Connected"
        return False, "Redis client connection failed"
    except Exception as e:
        return False, f"Redis error: {str(e)}"


def check_opensandbox_server_health(state) -> tuple[bool, str]:
    """Lightweight and production-safe health check for the upstream OpenSandbox backend service."""
    try:
        healthy = state.backend.health_check()
        return (
            healthy,
            f"Backend name: {state.backend.name} is responsive"
            if healthy
            else "Upstream service unresponsive",
        )
    except Exception as e:
        return False, f"Upstream service error: {str(e)}"


def get_health_router(state, validate_token) -> APIRouter:
    """
    Router factory that injects global application state and token validation dependency
    to configure and register aggregate and individual health endpoints without circular imports.
    """
    router = APIRouter()

    @router.get(
        "/health",
        response_model=HealthResponse,
        summary="Retrieve active connection tracking properties",
        tags=["System"],
    )
    async def health(response: Response):
        """
        Perform a lightweight and production-safe health check on all critical internal and external dependencies.
        Returns 200 OK if all checks pass, and 500 Internal Server Error if any critical dependency fails.
        """
        db_healthy, db_details = check_postgresql_health(state)
        redis_healthy, redis_details = check_redis_health(state)
        sandbox_healthy, sandbox_details = check_opensandbox_server_health(state)

        overall_healthy = db_healthy and sandbox_healthy
        if state.use_redis:
            overall_healthy = overall_healthy and redis_healthy

        status_code = (
            status.HTTP_200_OK
            if overall_healthy
            else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        response.status_code = status_code

        # Format details to ensure complete backward compatibility with existing monitors
        cache_details = "Disabled"
        queue_details = "Disabled"
        if state.use_redis:
            if redis_healthy:
                cache_details = "Redis Cache Connected"
                queue_details = "Redis Queue Connected"
            else:
                cache_details = redis_details
                queue_details = redis_details

        return HealthResponse(
            status_code=status_code,
            status="healthy" if overall_healthy else "unhealthy",
            backend=state.backend.name,
            healthy=overall_healthy,
            dependencies={
                "database": DependencyStatus(
                    status="healthy" if db_healthy else "unhealthy", details=db_details
                ),
                "cache": DependencyStatus(
                    status="healthy" if redis_healthy else "unhealthy",
                    details=cache_details,
                ),
                "queue": DependencyStatus(
                    status="healthy" if redis_healthy else "unhealthy",
                    details=queue_details,
                ),
                "opensandbox": DependencyStatus(
                    status="healthy" if sandbox_healthy else "unhealthy",
                    details=sandbox_details,
                ),
            },
        )

    @router.get(
        "/v1/health",
        response_model=HealthResponse,
        summary="Retrieve active connection tracking properties (V1)",
        tags=["System"],
        dependencies=[Depends(validate_token)],
    )
    async def health_v1(response: Response):
        """Alias for /health scoped to /v1 for gateway compatibility."""
        return await health(response)

    @router.get(
        "/api/v1/01sbx/postgresql/health",
        tags=["System"],
        dependencies=[Depends(validate_token)],
    )
    async def postgresql_health(response: Response):
        """
        Lightweight and production-safe health check for the PostgreSQL database.
        """
        healthy, details = check_postgresql_health(state)
        status_code = (
            status.HTTP_200_OK if healthy else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        response.status_code = status_code
        return {
            "status_code": status_code,
            "status": "healthy" if healthy else "unhealthy",
            "dependency": "postgresql",
            "healthy": healthy,
            "details": details,
        }

    @router.get(
        "/api/v1/01sbx/redis/health",
        tags=["System"],
        dependencies=[Depends(validate_token)],
    )
    async def redis_health(response: Response):
        """
        Lightweight and production-safe health check for the Redis cache/queue dependency.
        """
        healthy, details = check_redis_health(state)
        status_code = (
            status.HTTP_200_OK if healthy else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        response.status_code = status_code
        return {
            "status_code": status_code,
            "status": "healthy" if healthy else "unhealthy",
            "dependency": "redis",
            "healthy": healthy,
            "details": details,
        }

    @router.get(
        "/api/v1/01sbx/01sandbox/health",
        tags=["System"],
        dependencies=[Depends(validate_token)],
    )
    async def sandbox_core_health(response: Response):
        """
        Lightweight and production-safe health check for the upstream OpenSandbox backend service.
        """
        healthy, details = check_opensandbox_server_health(state)
        status_code = (
            status.HTTP_200_OK if healthy else status.HTTP_500_INTERNAL_SERVER_ERROR
        )
        response.status_code = status_code
        return {
            "status_code": status_code,
            "status": "healthy" if healthy else "unhealthy",
            "dependency": "01sandbox",
            "healthy": healthy,
            "details": details,
        }

    return router
