"""TV download-options action.

The strict ``run`` path is the Sonarr library lookup only — same as the
legacy ``/action`` endpoint. ``run_for_router`` consolidates the
conversational policies (qBittorrent reuse, completed-download
short-circuit, indexer fallback) that the router orchestrator delegates
through ``handler.run_for_router``.

When the indexer fallback fires, the result dict carries a private
``_router_action_override`` so the orchestrator knows the executed action
is now ``indexer_search`` (matters for session persistence and response
formatting). When the qBittorrent short-circuits hit, the result carries a
``_router_response_text`` with the friendly phrasing.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.integrations.qbittorrent import (
    find_completed_download_name,
    try_enable_requested_season_in_existing_torrent,
)
from app.models.actions import ActionDownloadOptionsTV
from app.models.router import RouterPendingOption, RouterSessionState
from app.services.indexer_pipeline import run_indexer_search
from app.services.sonarr_release_pipeline import run_download_options_tv

from .formatting import format_action_error, format_options_table
from .registry import ActionContext, ActionHandler, register_action

_INDEXER_FALLBACK_CODES = frozenset(
    {"NO_RELEASES", "SERIES_NOT_IN_LIBRARY", "UNKNOWN_SERIES_ID"}
)


@register_action
class DownloadOptionsTV(ActionHandler[ActionDownloadOptionsTV]):
    name: ClassVar[str] = "download_options_tv"
    description: ClassVar[str] = "Get ranked TV release options."
    result_category: ClassVar[str] = "options"
    args_model: ClassVar[type] = ActionDownloadOptionsTV

    def run(
        self, ctx: ActionContext, args: ActionDownloadOptionsTV
    ) -> dict[str, Any]:
        return run_download_options_tv(
            ctx.http,
            ctx.settings,
            args.query,
            args.season,
            args.series_id,
            args.include_full_series_packs,
        )

    def run_for_router(
        self, ctx: ActionContext, args: ActionDownloadOptionsTV
    ) -> dict[str, Any]:
        reused = try_enable_requested_season_in_existing_torrent(
            http=ctx.http, s=ctx.settings, query=args.query, season=args.season
        )
        if reused is not None:
            status = str(reused.get("status") or "")
            if status == "enabled":
                return {
                    "ok": True,
                    "existing_torrent_reused": True,
                    "_router_response_text": (
                        f"OK! It's downloading. I found an existing "
                        f"multi-season torrent and enabled season "
                        f"{args.season} files."
                    ),
                    **reused,
                }
            if status == "already_downloaded":
                return {
                    "ok": True,
                    "already_downloaded": True,
                    "_router_response_text": (
                        f"OK! It's downloading. Season {args.season} "
                        f"is already available from an existing torrent."
                    ),
                    **reused,
                }

        completed = find_completed_download_name(
            http=ctx.http, s=ctx.settings, query=args.query, season=args.season
        )
        if completed is not None:
            return {
                "ok": True,
                "already_downloaded": True,
                "matched_release": completed,
                "_router_response_text": f"You already downloaded this: {completed}",
            }

        result = self.run(ctx, args)
        err = result.get("error") if isinstance(result, dict) else None
        err_code = (err or {}).get("code") if isinstance(err, dict) else None
        if err_code in _INDEXER_FALLBACK_CODES:
            q = f"{args.query} season {args.season}".strip()
            fallback = run_indexer_search(ctx.http, ctx.settings, q, "search", 10)
            fallback["_router_action_override"] = {
                "action": "indexer_search",
                "query": q,
                "limit": 10,
                "search_type": "search",
            }
            return fallback
        return result

    def format_response(
        self, args: ActionDownloadOptionsTV, result: dict[str, Any]
    ) -> str:
        if result.get("ok") is not True:
            return format_action_error(result)
        return format_options_table(result)

    def selection_to_grab(
        self, state: RouterSessionState, selected: RouterPendingOption
    ) -> dict[str, Any] | None:
        if not selected.guid or selected.episode_id is None:
            return None
        return {
            "action": "download_grab_tv",
            "guid": selected.guid,
            "episode_id": selected.episode_id,
        }


__all__ = ["DownloadOptionsTV"]
