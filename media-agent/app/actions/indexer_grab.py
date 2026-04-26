"""Grab a release object returned from an indexer search.

The post-grab season-only filter that the router applies after a successful
indexer_grab remains a router-orchestration concern (Phase C). For Phase B,
``run`` simply forwards to ``prowlarr_grab``.
"""

from __future__ import annotations

from typing import Any, ClassVar

from app.models.actions import ActionIndexerGrab
from app.services.indexer_pipeline import prowlarr_grab

from .formatting import format_action_error, format_grab_ack
from .registry import ActionContext, ActionHandler, register_action


@register_action
class IndexerGrab(ActionHandler[ActionIndexerGrab]):
    name: ClassVar[str] = "indexer_grab"
    description: ClassVar[str] = "Grab a release object from indexer search."
    result_category: ClassVar[str] = "grab"
    args_model: ClassVar[type] = ActionIndexerGrab

    def run(self, ctx: ActionContext, args: ActionIndexerGrab) -> dict[str, Any]:
        return prowlarr_grab(ctx.http, ctx.settings, args.release)

    def format_response(
        self, args: ActionIndexerGrab, result: dict[str, Any]
    ) -> str:
        if result.get("ok") is not True:
            return format_action_error(result)
        return format_grab_ack()


__all__ = ["IndexerGrab"]
