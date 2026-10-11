"""HTTP route group for the media-agent service.

Each module under this package owns one related set of endpoints. The
package-level ``api_router`` mounts them all under
``/internal/media-agent/v1``.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import action, download, health, indexer, router, search

api_router = APIRouter(prefix="/internal/media-agent/v1")
for module in (health, search, download, indexer, action, router):
    api_router.include_router(module.router)


__all__ = ["api_router"]
