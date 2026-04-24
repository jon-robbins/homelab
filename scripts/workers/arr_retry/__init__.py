"""Compatibility package forwarding to homelab_workers.arr_retry."""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_package_source_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    package_src = repo_root / "src" / "homelab_workers" / "src"
    package_src_str = str(package_src)
    if package_src.exists() and package_src_str not in sys.path:
        sys.path.insert(0, package_src_str)


_ensure_package_source_on_path()

from homelab_workers.arr_retry import main

__all__ = ["main"]
