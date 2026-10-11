from __future__ import annotations

from dataclasses import dataclass

"""Canonical action registry for media-agent extension points."""


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    name: str
    model_name: str
    description: str
    result_category: str
    router_may_emit: bool = True

    def public_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "model": self.model_name,
            "description": self.description,
            "result_category": self.result_category,
            "router_may_emit": self.router_may_emit,
        }


ACTION_DEFINITIONS: tuple[ActionDefinition, ...] = (
    ActionDefinition("search", "ActionSearch", "Library metadata lookup (tv/movie).", "lookup"),
    ActionDefinition(
        "download_options_tv",
        "ActionDownloadOptionsTV",
        "Get ranked TV release options.",
        "options",
    ),
    ActionDefinition(
        "download_options_movie",
        "ActionDownloadOptionsMovie",
        "Get ranked movie release options.",
        "options",
    ),
    ActionDefinition(
        "download_grab_tv",
        "ActionDownloadGrabTV",
        "Grab selected TV release.",
        "grab",
    ),
    ActionDefinition(
        "download_grab_movie",
        "ActionDownloadGrabMovie",
        "Grab selected movie release.",
        "grab",
    ),
    ActionDefinition(
        "indexer_search",
        "ActionIndexerSearch",
        "Search indexers directly via Prowlarr.",
        "options",
    ),
    ActionDefinition(
        "indexer_grab",
        "ActionIndexerGrab",
        "Grab a release object from indexer search.",
        "grab",
    ),
)

ACTION_NAMES: tuple[str, ...] = tuple(action.name for action in ACTION_DEFINITIONS)
ROUTER_ACTION_NAMES: tuple[str, ...] = tuple(
    action.name for action in ACTION_DEFINITIONS if action.router_may_emit
)

ACTION_BY_NAME: dict[str, ActionDefinition] = {
    action.name: action for action in ACTION_DEFINITIONS
}
ACTION_DESCRIPTION_BY_NAME: dict[str, str] = {
    action.name: action.description for action in ACTION_DEFINITIONS
}
