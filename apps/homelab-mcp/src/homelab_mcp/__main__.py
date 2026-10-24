from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse

from homelab_mcp.auth import BearerAuthMiddleware
from homelab_mcp.config import load_settings
from homelab_mcp.server import mcp


@mcp.custom_route("/health", methods=["GET"])
async def health(_request) -> PlainTextResponse:
    return PlainTextResponse("ok")


def _route_key(route: object) -> str | None:
    path = getattr(route, "path", None)
    if isinstance(path, str):
        return path
    return type(route).__name__


def build_app() -> Starlette:
    """Streamable HTTP at /mcp plus legacy SSE at /sse and /messages/."""
    settings = load_settings()
    streamable = mcp.streamable_http_app()
    legacy_sse = mcp.sse_app()

    existing = {_route_key(route) for route in streamable.routes}
    insert_at = 0
    for route in legacy_sse.routes:
        key = _route_key(route)
        if key in existing:
            continue
        streamable.routes.insert(insert_at, route)
        insert_at += 1
        existing.add(key)

    # Preserve streamable lifespan while adding SSE routes on the same app.
    original_lifespan = streamable.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with original_lifespan(app):
            yield

    streamable.router.lifespan_context = lifespan
    streamable.add_middleware(BearerAuthMiddleware, token=settings.mcp_api_token)
    return streamable


def main() -> None:
    settings = load_settings()
    uvicorn.run(build_app(), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
