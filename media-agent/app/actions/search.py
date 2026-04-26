"""Library metadata lookup action."""

from __future__ import annotations

from typing import Any, ClassVar

from app.models.actions import ActionSearch
from app.services.lookup import normalize_query, run_lookup

from .formatting import format_search_results
from .registry import ActionContext, ActionHandler, register_action


@register_action
class Search(ActionHandler[ActionSearch]):
    name: ClassVar[str] = "search"
    description: ClassVar[str] = "Library metadata lookup (tv/movie)."
    result_category: ClassVar[str] = "lookup"
    args_model: ClassVar[type] = ActionSearch

    def run(self, ctx: ActionContext, args: ActionSearch) -> dict[str, Any]:
        normalized = normalize_query(args.query)
        results = run_lookup(ctx.http, ctx.settings, args.type, normalized)
        return {
            "ok": True,
            "type": args.type,
            "query": args.query,
            "normalized_query": normalized,
            "results": [item.model_dump() for item in results],
        }

    def format_response(self, args: ActionSearch, result: dict[str, Any]) -> str:
        if result.get("ok") is not True:
            from .formatting import format_action_error

            return format_action_error(result)
        return format_search_results(result)


__all__ = ["Search"]
