from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from .api.auth import new_request_id
from .api.dependencies import close_http_client, init_http_client
from .api.destructive_policy import assert_http_method_allowed
from .api.errors import register_exception_handlers
from .api.routes import api_router
from .config import get_settings
from .logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    init_http_client()
    yield
    close_http_client()


app = FastAPI(
    title="media-agent",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
register_exception_handlers(app)


@app.middleware("http")
async def destructive_http_method_guard(request: Request, call_next):
    if request.url.path.startswith("/internal/media-agent/"):
        request_id = new_request_id()
        try:
            settings = get_settings()
        except RuntimeError:
            return await call_next(request)
        confirm = request.headers.get("X-Media-Agent-Destructive-Confirm")
        blocked = assert_http_method_allowed(
            settings,
            request_id,
            request.method,
            destructive_confirm=confirm,
        )
        if blocked is not None:
            return blocked
    return await call_next(request)


app.include_router(api_router)


__all__ = ["app"]
