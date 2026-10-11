"""Per-action handler package.

Importing this package populates ``app.actions.registry._REGISTRY`` with
every handler. Adding a new agent-callable capability = create a new module
under ``app/actions/`` and import it from this file.
"""

from __future__ import annotations

from . import (  # noqa: F401  — populates registry as a side effect
    download_movie,
    download_tv,
    grab_movie,
    grab_tv,
    indexer_grab,
    indexer_search,
    registry,
    search,
)
from .registry import ActionContext, ActionHandler, register_action

__all__ = [
    "ActionContext",
    "ActionHandler",
    "register_action",
    "registry",
]
