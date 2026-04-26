"""Post-grab season-only filter for selection-driven indexer_grab calls.

After the router commits an ``indexer_grab`` from a TV+season selection, we
inspect the resulting torrent's file list in qBittorrent and disable the
files for unrelated seasons. This keeps multi-season torrents downloading
only the season the user actually asked for.

A duplicate grab (HTTP 500 from Prowlarr because the torrent is already in
qBittorrent) is also handled here: if the season-only filter succeeds, we
treat the result as ok and surface a friendly response.
"""

from __future__ import annotations

from typing import Any

from ..models.router import RouterPendingOption, RouterSessionState
from ..services.qb_files import season_only_selection_after_grab


def _is_duplicate_grab(tool_result: dict[str, Any]) -> bool:
    if not isinstance(tool_result, dict):
        return False
    if tool_result.get("ok") is True:
        return False
    err = tool_result.get("error")
    if not isinstance(err, dict):
        return False
    return (
        err.get("code") == "GRAB_FAILED"
        and "HTTP 500" in str(err.get("message") or "")
    )


def apply_post_grab_season_only(
    *,
    http,
    settings,
    session: RouterSessionState | None,
    selected_option: RouterPendingOption | None,
    action_payload: dict[str, Any],
    tool_result: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Returns ``(maybe-mutated tool_result, optional response_text override)``.

    Pre-conditions to apply the filter (mirrors prior orchestrator logic):

    - The action being executed is ``indexer_grab``.
    - The selection session pinned a TV media type and a specific season.
    - The selected option carries a ``release`` dict (we need the infoHash).
    - The tool_result is OK or is a duplicate-grab failure.
    """
    if session is None or selected_option is None:
        return tool_result, None
    if str(action_payload.get("action") or "") != "indexer_grab":
        return tool_result, None
    if session.media_type != "tv" or not isinstance(session.season, int):
        return tool_result, None
    if not selected_option.release:
        return tool_result, None

    duplicate = _is_duplicate_grab(tool_result)
    if tool_result.get("ok") is not True and not duplicate:
        return tool_result, None

    season_adjust = season_only_selection_after_grab(
        http, settings, selected_option.release, session.season
    )
    if season_adjust is not None:
        tool_result["season_selection"] = season_adjust
        if duplicate and season_adjust.get("status") == "season_only_applied":
            tool_result["ok"] = True
            tool_result["duplicate_grab_reused"] = True
            tool_result.pop("error", None)

    season_state = tool_result.get("season_selection")
    if (
        isinstance(season_state, dict)
        and season_state.get("status") == "season_only_applied"
    ):
        if duplicate:
            response_text = (
                f"That torrent was already in qBittorrent, so I reused it and set season "
                f"{session.season} only."
            )
        else:
            response_text = (
                f"OK! It's downloading season {session.season} only from that torrent."
            )
        return tool_result, response_text

    return tool_result, None


__all__ = ["apply_post_grab_season_only"]
