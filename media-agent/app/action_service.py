from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .download_options import (
    grab_radarr,
    grab_sonarr,
    run_download_options_movie,
    run_download_options_tv,
)
from .lookup import normalize_query, run_lookup
from .models import (
    ACTION_CALL_ADAPTER,
    ActionDownloadGrabMovie,
    ActionDownloadGrabTV,
    ActionDownloadOptionsMovie,
    ActionDownloadOptionsTV,
    ActionIndexerGrab,
    ActionIndexerSearch,
    ActionSearch,
)
from .prowlarr_flow import prowlarr_grab, run_indexer_search

# Deterministic action execution used by HTTP routes and router orchestration.

def execute_action_payload(
    client: httpx.Client,
    settings: Settings,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate and run an action payload without going through FastAPI routing."""
    call = ACTION_CALL_ADAPTER.validate_python(payload)
    return execute_validated_action(client, settings, call)


def execute_validated_action(
    client: httpx.Client,
    settings: Settings,
    call: Any,
) -> dict[str, Any]:
    if isinstance(call, ActionSearch):
        normalized = normalize_query(call.query)
        results = run_lookup(client, settings, call.type, normalized)
        return {
            "ok": True,
            "type": call.type,
            "query": call.query,
            "normalized_query": normalized,
            "results": [item.model_dump() for item in results],
        }
    if isinstance(call, ActionDownloadOptionsTV):
        return run_download_options_tv(
            client,
            settings,
            call.query,
            call.season,
            call.series_id,
            call.include_full_series_packs,
        )
    if isinstance(call, ActionDownloadOptionsMovie):
        return run_download_options_movie(client, settings, call.query, call.movie_id)
    if isinstance(call, ActionDownloadGrabTV):
        return grab_sonarr(client, settings, call.episode_id, call.guid)
    if isinstance(call, ActionDownloadGrabMovie):
        return grab_radarr(client, settings, call.movie_id, call.guid)
    if isinstance(call, ActionIndexerSearch):
        return run_indexer_search(
            client,
            settings,
            call.query,
            call.search_type,
            call.limit,
        )
    if isinstance(call, ActionIndexerGrab):
        return prowlarr_grab(client, settings, call.release)
    return {
        "ok": False,
        "error": {"code": "VALIDATION_ERROR", "message": "unsupported action"},
    }
