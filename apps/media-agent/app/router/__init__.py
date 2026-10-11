"""Conversational router package.

The orchestrator's ``dispatch`` is the single entry point for the
``/router`` endpoint. The other modules expose pure helpers for unit tests
(``intent``, ``parser``, ``post_grab``, ``smoke``) and the JSON-file
session store (``session``).
"""

from __future__ import annotations

from .orchestrator import RouterContext, dispatch

__all__ = ["dispatch", "RouterContext"]
