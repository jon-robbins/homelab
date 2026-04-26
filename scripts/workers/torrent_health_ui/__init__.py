"""Compatibility package forwarding to homelab_workers.torrent_health_ui."""

from __future__ import annotations

from arr_retry import _ensure_package_source_on_path

_ensure_package_source_on_path()

from homelab_workers.torrent_health_ui import main

__all__ = ["main"]
