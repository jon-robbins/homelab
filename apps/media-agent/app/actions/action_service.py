from __future__ import annotations

from typing import Any

import httpx

from ..core.config import Settings
from ..core.models import (
    ACTION_CALL_ADAPTER,
    ActionDownloadGrabMovie,
    ActionDownloadGrabTV,
    ActionDownloadOptionsMovie,
    ActionDownloadOptionsTV,
    ActionIndexerGrab,
    ActionIndexerSearch,
    ActionSearch,
)
from .download_options import (
    grab_radarr,
    grab_sonarr,
    run_download_options_movie,
    run_download_options_tv,
)
from .lookup import normalize_query, run_lookup
from .prowlarr_flow import prowlarr_grab, run_indexer_search

# Deterministic action execution used by HTTP routes and router orchestration.

DESTRUCTIVE_ACTIONS = frozenset(
    {
        "download_grab_tv",
        "download_grab_movie",
        "indexer_grab",
    }
)


def hitl_gate_destructive_action(
    action: str,
    *,
    settings: Settings,
    confirmed: bool = False,
) -> dict[str, Any] | None:
    """Human-in-the-loop gate before grab/indexer side effects.

    Returns a blocked result dict when clearance is missing; ``None`` means proceed.

  Planned HITL extensions (not yet wired):
    1. Persist pending destructive intents (action + payload + session_key) to a
       short-lived store (Redis or the existing router state file).
    2. Expose ``GET /internal/media-agent/v1/hitl/pending`` for a human UI.
    3. Require ``POST /hitl/approve`` with the confirm token before execution.
    4. Auto-expire pending intents after ``MEDIA_AGENT_HITL_TTL_S``.
    """
    if action not in DESTRUCTIVE_ACTIONS:
        return None
    if settings.media_agent_allow_destructive or confirmed:
        return None
    return {
        "ok": False,
        "error": {
            "code": "HITL_REQUIRED",
            "message": (
                "destructive action blocked; approve via "
                "X-Media-Agent-Destructive-Confirm or MEDIA_AGENT_ALLOW_DESTRUCTIVE"
            ),
        },
    }

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
    *,
    destructive_confirmed: bool = False,
) -> dict[str, Any]:
    blocked = hitl_gate_destructive_action(
        str(getattr(call, "action", "")),
        settings=settings,
        confirmed=destructive_confirmed,
    )
    if blocked is not None:
        return blocked
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
