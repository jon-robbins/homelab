"""HTTP route group for the homelab-agent service."""

from __future__ import annotations

from .chat import router as chat_router
from .health import router as health_router
from .models import router as models_router

__all__ = ["chat_router", "health_router", "models_router"]
