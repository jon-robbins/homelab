"""Grab a selected TV release via Sonarr."""

from __future__ import annotations

from typing import Any, ClassVar

from app.models.actions import ActionDownloadGrabTV
from app.services.sonarr_release_pipeline import grab_sonarr

from .formatting import format_action_error, format_grab_ack
from .registry import ActionContext, ActionHandler, register_action


@register_action
class DownloadGrabTV(ActionHandler[ActionDownloadGrabTV]):
    name: ClassVar[str] = "download_grab_tv"
    description: ClassVar[str] = "Grab selected TV release."
    result_category: ClassVar[str] = "grab"
    args_model: ClassVar[type] = ActionDownloadGrabTV

    def run(self, ctx: ActionContext, args: ActionDownloadGrabTV) -> dict[str, Any]:
        return grab_sonarr(ctx.http, ctx.settings, args.episode_id, args.guid)

    def format_response(
        self, args: ActionDownloadGrabTV, result: dict[str, Any]
    ) -> str:
        if result.get("ok") is not True:
            return format_action_error(result)
        return format_grab_ack()


__all__ = ["DownloadGrabTV"]
