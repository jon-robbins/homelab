"""Grab a selected movie release via Radarr."""

from __future__ import annotations

from typing import Any, ClassVar

from app.models.actions import ActionDownloadGrabMovie
from app.services.radarr_release_pipeline import grab_radarr

from .formatting import format_action_error, format_grab_ack
from .registry import ActionContext, ActionHandler, register_action


@register_action
class DownloadGrabMovie(ActionHandler[ActionDownloadGrabMovie]):
    name: ClassVar[str] = "download_grab_movie"
    description: ClassVar[str] = "Grab selected movie release."
    result_category: ClassVar[str] = "grab"
    args_model: ClassVar[type] = ActionDownloadGrabMovie

    def run(
        self, ctx: ActionContext, args: ActionDownloadGrabMovie
    ) -> dict[str, Any]:
        return grab_radarr(ctx.http, ctx.settings, args.movie_id, args.guid)

    def format_response(
        self, args: ActionDownloadGrabMovie, result: dict[str, Any]
    ) -> str:
        if result.get("ok") is not True:
            return format_action_error(result)
        return format_grab_ack()


__all__ = ["DownloadGrabMovie"]
