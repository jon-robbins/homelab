from __future__ import annotations

from . import _ensure_package_source_on_path

_ensure_package_source_on_path()

from homelab_workers.arr_retry.main import main

__all__ = ["main"]
