from __future__ import annotations

import logging
import os

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging() -> None:
    """Initialize stdlib logging once per process.

    Level is read from the ``LOG_LEVEL`` environment variable and defaults to
    ``INFO``. Safe to call multiple times; ``force=True`` lets lifespan restarts
    reset handlers cleanly during tests.
    """
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format=_LOG_FORMAT, force=True)
