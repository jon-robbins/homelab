"""Search Prowlarr indexers directly."""

from __future__ import annotations

from typing import Any, ClassVar

from app.models.actions import ActionIndexerSearch
from app.models.router import RouterPendingOption, RouterSessionState
from app.services.indexer_pipeline import run_indexer_search

from .formatting import format_action_error, format_options_table
from .registry import ActionContext, ActionHandler, register_action


@register_action
class IndexerSearch(ActionHandler[ActionIndexerSearch]):
    name: ClassVar[str] = "indexer_search"
    description: ClassVar[str] = "Search indexers directly via Prowlarr."
    result_category: ClassVar[str] = "options"
    args_model: ClassVar[type] = ActionIndexerSearch

    def run(self, ctx: ActionContext, args: ActionIndexerSearch) -> dict[str, Any]:
        return run_indexer_search(
            ctx.http, ctx.settings, args.query, args.search_type, args.limit
        )

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
