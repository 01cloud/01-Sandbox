from __future__ import annotations

from fastapi import Request


async def log_headers(request: Request, call_next):
    print(
        f"[DEBUG HEADERS] {request.method} {request.url.path} Headers: {dict(request.headers)}"
    )
    response = await call_next(request)
    return response


async def cookie_auth_redirect_middleware(request: Request, call_next):
    if request.url.path in ["/docs", "/redoc"] or (
        request.url.path.startswith("/api/")
        and request.url.path.endswith(("/docs", "/redoc"))
    ):
        # Allow documentation to be public to avoid cookie issues during development
        return await call_next(request)

    return await call_next(request)
