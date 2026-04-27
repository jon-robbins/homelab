"""Search Prowlarr indexers directly."""

from __future__ import annotations

import re
from typing import Any, ClassVar

from app.models.actions import ActionIndexerSearch
from app.models.router import RouterPendingOption, RouterSessionState
from app.services.indexer_pipeline import run_indexer_search
from app.services.qb_files import try_enable_requested_season_in_existing_torrent

from .formatting import format_action_error, format_options_table
from .registry import ActionContext, ActionHandler, register_action

_TRAILING_SEASON_RE = re.compile(r"\s+(?:season|s)\s*\d+$", re.IGNORECASE)


@register_action
class IndexerSearch(ActionHandler[ActionIndexerSearch]):
    name: ClassVar[str] = "indexer_search"
    description: ClassVar[str] = "Search indexers directly via Prowlarr."
    result_category: ClassVar[str] = "options"
    args_model: ClassVar[type] = ActionIndexerSearch

    def run(self, ctx: ActionContext, args: ActionIndexerSearch) -> dict[str, Any]:
        return run_indexer_search(
            ctx.http, ctx.settings, args.query, args.search_type, args.limit,
            season=args.season,
        )

    def run_for_router(
        self, ctx: ActionContext, args: ActionIndexerSearch
    ) -> dict[str, Any]:
        # For TV+season queries, check qBittorrent for an existing multi-season
        # pack before hitting Prowlarr (which may have disabled indexers).
        if args.type == "tv" and isinstance(args.season, int):
            # Strip the "season N" suffix so the title-only query matches
            # torrent names like "Show S01-S04 ..." that don't contain "season".
            title_query = _TRAILING_SEASON_RE.sub("", args.query).strip() or args.query
            reused = try_enable_requested_season_in_existing_torrent(
                http=ctx.http, s=ctx.settings, query=title_query, season=args.season
            )
            if isinstance(reused, dict):
                status = str(reused.get("status") or "")
                if status in ("enabled", "already_downloaded", "already_selected"):
                    return {
                        "ok": True,
                        "existing_torrent_reused": True,
                        "_router_response_text": (
                            f"OK! It's downloading. I found an existing "
                            f"multi-season torrent and enabled season "
                            f"{args.season}."
                            if status == "enabled"
                            else f"OK! It's downloading. Season {args.season} "
                            f"is already available from an existing torrent."
                        ),
                        **reused,
                    }
        return self.run(ctx, args)

    def format_response(
        self, args: ActionIndexerSearch, result: dict[str, Any]
    ) -> str:
        if result.get("ok") is not True:
            return format_action_error(result)
        return format_options_table(result)

    def selection_to_grab(
        self, state: RouterSessionState, selected: RouterPendingOption
    ) -> dict[str, Any] | None:
        if not selected.release:
            return None
        return {"action": "indexer_grab", "release": selected.release}


__all__ = ["IndexerSearch"]
