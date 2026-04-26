"""qBittorrent low-level auth + file helpers.

Domain-level workflows (season-only filtering, completed-download matching,
existing-torrent reuse) live in ``app.services.qb_files``. This module
re-exports them so historical import paths (``app.integrations.qbittorrent``)
keep working.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx


def qb_login(http: httpx.Client, s: Any) -> httpx.Cookies | None:
    if not s.qbittorrent_configured:
        return None
    auth = http.post(
        urljoin(f"{s.qbittorrent_base}/", "api/v2/auth/login"),
        data={
            "username": s.qbittorrent_username,
            "password": s.qbittorrent_password,
        },
        timeout=s.upstream_timeout_s,
    )
    auth.raise_for_status()
    return auth.cookies


def qb_file_id(file_row: dict[str, Any]) -> int | None:
    for key in ("index", "id"):
        value = file_row.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def qb_file_priority(file_row: dict[str, Any]) -> int:
    value = file_row.get("priority")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


from ..services.qb_files import (  # noqa: E402 — must follow low-level defs above
    completed_download_match_for_action,
    find_completed_download_name,
    season_only_selection_after_grab,
    try_enable_requested_season_in_existing_torrent,
)

__all__ = [
    "qb_login",
    "qb_file_id",
    "qb_file_priority",
    "completed_download_match_for_action",
    "find_completed_download_name",
    "season_only_selection_after_grab",
    "try_enable_requested_season_in_existing_torrent",
]
