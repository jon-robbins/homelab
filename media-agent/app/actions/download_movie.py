"""Movie download-options action.

Strict ``run`` is the Radarr library lookup. ``run_for_router`` adds the
indexer-fallback policy (no qBittorrent reuse / short-circuit for movies,
mirroring the existing router policy table). When the fallback fires the
result carries a private ``_router_action_override`` so the orchestrator
knows the executed action is now ``indexer_search``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.models.actions import ActionDownloadOptionsMovie
from app.models.router import RouterPendingOption, RouterSessionState
from app.services.indexer_pipeline import run_indexer_search
from app.services.radarr_release_pipeline import run_download_options_movie

from .formatting import format_action_error, format_options_table
from .registry import ActionContext, ActionHandler, register_action

_INDEXER_FALLBACK_CODES = frozenset(
    {"NO_RELEASES", "MOVIE_NOT_IN_LIBRARY", "UNKNOWN_MOVIE_ID"}
)


@register_action
class DownloadOptionsMovie(ActionHandler[ActionDownloadOptionsMovie]):
    name: ClassVar[str] = "download_options_movie"
    description: ClassVar[str] = "Get ranked movie release options."
    result_category: ClassVar[str] = "options"
    args_model: ClassVar[type] = ActionDownloadOptionsMovie

    def run(
        self, ctx: ActionContext, args: ActionDownloadOptionsMovie
    ) -> dict[str, Any]:
        return run_download_options_movie(
            ctx.http, ctx.settings, args.query, args.movie_id
        )

    def run_for_router(
        self, ctx: ActionContext, args: ActionDownloadOptionsMovie
    ) -> dict[str, Any]:
        result = self.run(ctx, args)
        err = result.get("error") if isinstance(result, dict) else None
        err_code = (err or {}).get("code") if isinstance(err, dict) else None
        if err_code in _INDEXER_FALLBACK_CODES:
            q = (args.query or "").strip()
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
        self, args: ActionDownloadOptionsMovie, result: dict[str, Any]
    ) -> str:
        if result.get("ok") is not True:
            return format_action_error(result)
        return format_options_table(result)

    def selection_to_grab(
        self, state: RouterSessionState, selected: RouterPendingOption
    ) -> dict[str, Any] | None:
        if not selected.guid or selected.movie_id is None:
            return None
        return {
            "action": "download_grab_movie",
            "guid": selected.guid,
            "movie_id": selected.movie_id,
        }


__all__ = ["DownloadOptionsMovie"]
