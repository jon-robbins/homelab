"""Library-level removals and Seerr request actions, done through each app's API so
qBittorrent, Radarr/Sonarr and Seerr state stays consistent with what is on disk."""

from __future__ import annotations

import re
from typing import Any

import httpx
from homelab_workers.shared.arr import ArrClient
from homelab_workers.shared.qbittorrent import QBittorrentClient

from homelab_mcp.config import Settings
from homelab_mcp.envelope import error_response
from homelab_mcp.mutation_gate import canonical_arr_delete, canonical_qbt_delete
from homelab_mcp.mutation_runner import Refused, run_mutation

TOOL_TORRENTS_DELETE = "qbt_torrents_delete"
TOOL_ARR_ITEM_DELETE = "arr_item_delete"
TOOL_SEERR_REQUEST_ACTION = "seerr_request_action"
SEERR_ACTIONS = ("approve", "decline", "retry")
_HASH_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")

# Radarr and Sonarr name the "don't re-add" flag differently.
_ARR_DELETE = {
    "movie": ("radarr", "movie", "addImportExclusion"),
    "series": ("sonarr", "series", "addImportListExclusion"),
}


def _bool(value: bool) -> str:
    return "true" if value else "false"


def torrents_delete(
    settings: Settings,
    *,
    hashes: list[str],
    delete_files: bool,
    expected_action: str | None = None,
) -> str:
    if not hashes or not all(isinstance(h, str) and _HASH_RE.match(h.strip()) for h in hashes):
        return error_response(
            TOOL_TORRENTS_DELETE,
            code="INVALID_ARGUMENT",
            message="hashes must be a non-empty list of full 40/64-char info-hashes",
        )
    normalized = sorted({h.strip().lower() for h in hashes})

    def apply() -> dict[str, Any]:
        with QBittorrentClient(settings.qbt_url, settings.qbt_username, settings.qbt_password) as client:
            client.torrents_delete_hashes(normalized, delete_files=delete_files)
        return {"hashes": normalized, "delete_files": delete_files}

    return run_mutation(
        settings,
        tool=TOOL_TORRENTS_DELETE,
        tier=1,
        gate=lambda: canonical_qbt_delete(normalized, delete_files=delete_files),
        provided=expected_action,
        apply=apply,
    )


def arr_item_delete(
    settings: Settings,
    *,
    media_type: str,
    library_id: int,
    delete_files: bool,
    add_exclusion: bool = False,
    expected_action: str | None = None,
) -> str:
    if media_type not in _ARR_DELETE or library_id < 1:
        return error_response(
            TOOL_ARR_ITEM_DELETE,
            code="INVALID_ARGUMENT",
            message='media_type must be "movie" or "series" and library_id >= 1',
        )
    app, resource, exclusion_param = _ARR_DELETE[media_type]
    url, key = (
        (settings.radarr_url, settings.radarr_api_key)
        if app == "radarr"
        else (settings.sonarr_url, settings.sonarr_api_key)
    )

    def apply() -> dict[str, Any]:
        with ArrClient(url, key, timeout_seconds=60) as client:
            item = client.get(f"/{resource}/{library_id}")
            client.delete(
                f"/{resource}/{library_id}",
                query={"deleteFiles": _bool(delete_files), exclusion_param: _bool(add_exclusion)},
            )
        return {
            "app": app,
            "library_id": library_id,
            "title": item.get("title"),
            "year": item.get("year"),
            "path": item.get("path"),
            "delete_files": delete_files,
            "add_exclusion": add_exclusion,
        }

    return run_mutation(
        settings,
        tool=TOOL_ARR_ITEM_DELETE,
        tier=1,
        gate=lambda: canonical_arr_delete(media_type, library_id, delete_files=delete_files),
        provided=expected_action,
        apply=apply,
    )


def seerr_request_action(
    settings: Settings, *, request_id: int, action: str, confirm: bool = False
) -> str:
    if action not in SEERR_ACTIONS or request_id < 1:
        return error_response(
            TOOL_SEERR_REQUEST_ACTION,
            code="INVALID_ARGUMENT",
            message=f"action must be one of {', '.join(SEERR_ACTIONS)} and request_id >= 1",
        )

    def apply() -> dict[str, Any]:
        headers = {"X-Api-Key": settings.overseerr_api_key} if settings.overseerr_api_key else {}
        with httpx.Client(base_url=settings.overseerr_url, headers=headers, timeout=20.0) as client:
            resp = client.post(f"/api/v1/request/{request_id}/{action}")
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}
        if not isinstance(payload, dict):
            raise Refused("UPSTREAM_ERROR", "unexpected Seerr response shape")
        return {"request_id": request_id, "action": action, "status": payload.get("status")}

    return run_mutation(
        settings,
        tool=TOOL_SEERR_REQUEST_ACTION,
        tier=2,
        gate=lambda: True,
        provided=confirm,
        apply=apply,
    )
