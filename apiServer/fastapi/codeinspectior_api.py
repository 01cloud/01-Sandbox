"""
codeinspectior_api.py
====================

A clean, simplified proxy API specifically designed to forward traffic
to the internal OpenSandbox backend.

This main entrypoint is now a lightweight wire-up file.
All domain-specific logic has been modularized into separate packages.
"""

from api_keys import get_api_keys_router
from auth import validate_token

# Core & Auth modularized imports
from core import AppState, lifespan, state
from core.docs import register_docs_routes
from core.middleware import cookie_auth_redirect_middleware, log_headers
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Router factories
from health import get_health_router
from proxy import get_proxy_router
from sandboxes import get_sandboxes_router
from scan_repository import get_repo_scan_router

# Initialize central database
state.init_db()

app = FastAPI(
    title="CodeInspector API Manager",
    description="A centralized proxy relaying connections mapping standard interaction seamlessly to the underlying actual code-evaluation clusters locally natively successfully.",
    version="2.1.0",
    docs_url=None,  # Custom Swagger endpoint registered below
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom Http middlewares
app.middleware("http")(log_headers)
app.middleware("http")(cookie_auth_redirect_middleware)

# Register Swagger and OpenAPI routes
register_docs_routes(app)

# Register domain-specific routers
app.include_router(get_health_router(state, validate_token))
app.include_router(get_api_keys_router(state, validate_token))
app.include_router(get_sandboxes_router(state, validate_token))
app.include_router(get_proxy_router(state, validate_token))
app.include_router(get_repo_scan_router(state, validate_token))

# Register Generic Jobs Infrastructure router
from core.jobs.router import router as jobs_router

app.include_router(jobs_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("codeinspectior_api:app", host="0.0.0.0", port=8000, reload=True)
