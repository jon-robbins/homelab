from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.routes import chat_router, health_router, models_router
from app.config import get_settings
from app.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    get_settings()
    yield


app = FastAPI(
    title="homelab-agent",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
register_exception_handlers(app)
app.include_router(chat_router)
app.include_router(health_router)
app.include_router(models_router)


__all__ = ["app"]
