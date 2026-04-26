from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.dependencies import close_http_client, init_http_client
from .api.errors import register_exception_handlers
from .api.routes import api_router
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
app.include_router(api_router)


__all__ = ["app"]
