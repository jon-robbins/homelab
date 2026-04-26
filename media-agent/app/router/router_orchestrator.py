from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from fastapi.responses import JSONResponse

from ..core.models import RouterPendingOption, RouterRequest, RouterSessionState
from .router_contracts import RouterProviderOps
from .router_policy import (
    INDEXER_FLOW,
    LIBRARY_FLOW,
    REUSE_EXISTING_SEASON,
    ROUTER_FALLBACK_CODES,
    ROUTER_POLICY_TABLE,
    SESSION_OPTION_SOURCE_ACTIONS,
)
from .router_selection import (
    SelectionChoice,
    parse_selection_choice,
    resolve_pending_option,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ParsedIntentStage:
    intent: str
    intent_payload: dict[str, Any]
    session_state: RouterSessionState | None
    selection: SelectionChoice | None
    season_prompt_required: bool


@dataclass(slots=True)
class HydratedContextStage:
    intent: str
    intent_payload: dict[str, Any]
    session_state: RouterSessionState | None
    selection: SelectionChoice | None
    selected_option: RouterPendingOption | None
    action_payload: dict[str, Any] | None
    media_type_hint: str | None
    season_hint: int | None


@dataclass(slots=True)
class PlannedActionStage:
    intent: str
    intent_payload: dict[str, Any]
    session_state: RouterSessionState | None
    selection: SelectionChoice | None
    selected_option: RouterPendingOption | None
    action_payload: dict[str, Any] | None
    media_type_hint: str | None
    season_hint: int | None
    policy_path: tuple[str, ...]


@dataclass(slots=True)
class ExecutedActionStage:
    intent_payload: dict[str, Any]
    action_payload: dict[str, Any]
    tool_result: dict[str, Any]
    response_text: str
    media_type_hint: str | None
    season_hint: int | None


def _decision_log(correlation_id: str, stage: str, event: str, **data: Any) -> None:
    payload = {
        "correlation_id": correlation_id,
        "stage": stage,
        "event": event,
        "data": data,
    }
    logger.info("router_decision=%s", json.dumps(payload, sort_keys=True, default=str))


def parse_intent(
    *,
    ops: RouterProviderOps,
    request_id: str,
    correlation_id: str,
    router_request: RouterRequest,
) -> ParsedIntentStage:
    session_state: RouterSessionState | None = None
    if router_request.session_key:
        session_state = ops.get_session_state(router_request.session_key)

    selection = parse_selection_choice(router_request.message)
    intent_decision = ops.classify_intent(
        router_request.message, has_session_state=session_state is not None
    )
    season_prompt_required = bool(
        re.search(r"\bseason\b", router_request.message.lower())
        and not re.search(r"\bseason\s+\d{1,2}\b", router_request.message.lower())
    )
    _decision_log(
        correlation_id,
        "parse_intent",
        "intent_decided",
        request_id=request_id,
        session_key=router_request.session_key,
        intent=intent_decision.intent,
        has_session=bool(session_state),
        selection_rank=selection.rank if selection else None,
        selection_option_id=selection.option_id if selection else None,
        season_prompt_required=season_prompt_required,
    )
    return ParsedIntentStage(
        intent=intent_decision.intent,
        intent_payload=intent_decision.model_dump(),
        session_state=session_state,
        selection=selection,
        season_prompt_required=season_prompt_required,
    )


def hydrate_context(
    *,
    ops: RouterProviderOps,
    request_id: str,
    correlation_id: str,
    router_request: RouterRequest,
    parsed: ParsedIntentStage,
) -> HydratedContextStage:
    if parsed.intent == "selection" and parsed.session_state is not None:
        if parsed.selection is None:
            raise ValueError("selection must include a rank number or option id")
        selected = resolve_pending_option(
            options=parsed.session_state.options, selection=parsed.selection
        )
        if selected is None:
            rank_or_id = parsed.selection.option_id or str(parsed.selection.rank)
            raise ValueError(
                f"selection {rank_or_id} is not available in current options"
            )
        action_payload = ops.selection_to_action(parsed.session_state, selected)
        if action_payload is None:
            raise ValueError("selection payload is not available in current options")
        _decision_log(
            correlation_id,
            "hydrate_context",
            "selection_hydrated",
            request_id=request_id,
            source_action=parsed.session_state.source_action,
            option_id=selected.option_id,
            rank=selected.rank,
            action=action_payload.get("action"),
        )
        return HydratedContextStage(
            intent=parsed.intent,
            intent_payload=parsed.intent_payload,
            session_state=parsed.session_state,
            selection=parsed.selection,
            selected_option=selected,
            action_payload=action_payload,
            media_type_hint=parsed.session_state.media_type,
            season_hint=parsed.session_state.season,
        )

    if parsed.intent == "download":
        parsed_action = ops.parse_action(router_request.message)
        action_payload, media_type_hint, season_hint = _prefer_indexer_for_router_download(
            parsed_action
        )
        _decision_log(
            correlation_id,
            "hydrate_context",
            "action_parsed",
            request_id=request_id,
            parsed_action=parsed_action.get("action"),
            action=action_payload.get("action"),
            query=action_payload.get("query"),
            season=season_hint,
        )
        return HydratedContextStage(
            intent=parsed.intent,
            intent_payload=parsed.intent_payload,
            session_state=parsed.session_state,
            selection=parsed.selection,
            selected_option=None,
            action_payload=action_payload,
            media_type_hint=media_type_hint,
            season_hint=season_hint,
        )

    return HydratedContextStage(
        intent=parsed.intent,
        intent_payload=parsed.intent_payload,
        session_state=parsed.session_state,
        selection=parsed.selection,
        selected_option=None,
        action_payload=None,
        media_type_hint=None,
        season_hint=None,
    )


def _prefer_indexer_for_router_download(
    action_payload: dict[str, Any],
) -> tuple[dict[str, Any], str | None, int | None]:
    """Natural-language download requests should search Prowlarr first.

    The explicit `/download-options` action still exists for callers that want
    Sonarr/Radarr library semantics. The conversational router treats a title
    request as an acquisition request and searches indexers directly.
    """
    action = str(action_payload.get("action") or "")
    if action == "download_options_tv":
        query = str(action_payload.get("query") or "").strip()
        season = action_payload.get("season")
        if isinstance(season, int):
            query = f"{query} season {season}".strip()
        return (
            {
                "action": "indexer_search",
                "query": query,
                "limit": 10,
                "search_type": "search",
            },
            "tv",
            season if isinstance(season, int) else None,
        )
    if action == "download_options_movie":
        query = str(action_payload.get("query") or "").strip()
        return (
            {
                "action": "indexer_search",
                "query": query,
                "limit": 10,
                "search_type": "search",
            },
            "movie",
            None,
        )
    return action_payload, None, None


def plan_actions(
    *,
    request_id: str,
    correlation_id: str,
    hydrated: HydratedContextStage,
) -> PlannedActionStage:
    action_name = str((hydrated.action_payload or {}).get("action") or "")
    policy_path = ROUTER_POLICY_TABLE.get(action_name, tuple())
    _decision_log(
        correlation_id,
        "plan_actions",
        "policy_selected",
        request_id=request_id,
        intent=hydrated.intent,
        action=action_name,
        policy=list(policy_path),
    )
    return PlannedActionStage(
        intent=hydrated.intent,
        intent_payload=hydrated.intent_payload,
        session_state=hydrated.session_state,
        selection=hydrated.selection,
        selected_option=hydrated.selected_option,
        action_payload=hydrated.action_payload,
        media_type_hint=hydrated.media_type_hint,
        season_hint=hydrated.season_hint,
        policy_path=policy_path,
    )


def _try_reuse_existing_season(
    *,
    ops: RouterProviderOps,
    request_id: str,
    correlation_id: str,
    plan: PlannedActionStage,
    action_payload: dict[str, Any],
    media_type_hint: str | None,
    season_hint: int | None,
) -> ExecutedActionStage | None:
    action_name = str(action_payload.get("action") or "")
    if (
        REUSE_EXISTING_SEASON in plan.policy_path
        and action_name == "download_options_tv"
    ):
        season = action_payload.get("season")
        query = str(action_payload.get("query") or "").strip()
        if isinstance(season, int):
            reused = ops.library_reuse(query, season)
            if reused is not None:
                status = str(reused.get("status") or "")
                if status == "enabled":
                    _decision_log(
                        correlation_id,
                        "execute_action",
                        "reused_existing_torrent_enabled",
                        request_id=request_id,
                        season=season,
                    )
                    return ExecutedActionStage(
                        intent_payload=plan.intent_payload,
                        action_payload=action_payload,
                        tool_result={
                            "ok": True,
                            "existing_torrent_reused": True,
                            **reused,
                        },
                        response_text=(
                            f"I found an existing multi-season torrent and enabled season {season} files. "
                            "It should start downloading now."
                        ),
                        media_type_hint=media_type_hint,
                        season_hint=season_hint,
                    )
                if status == "already_downloaded":
                    _decision_log(
                        correlation_id,
                        "execute_action",
                        "reused_existing_torrent_already_downloaded",
                        request_id=request_id,
                        season=season,
                    )
                    return ExecutedActionStage(
                        intent_payload=plan.intent_payload,
                        action_payload=action_payload,
                        tool_result={"ok": True, "already_downloaded": True, **reused},
                        response_text=f"You already downloaded season {season} from an existing multi-season torrent.",
                        media_type_hint=media_type_hint,
                        season_hint=season_hint,
                    )
    return None


def _try_completed_download_short_circuit(
    *,
    ops: RouterProviderOps,
    request_id: str,
    correlation_id: str,
    plan: PlannedActionStage,
    action_payload: dict[str, Any],
    media_type_hint: str | None,
    season_hint: int | None,
) -> ExecutedActionStage | None:
    action_name = str(action_payload.get("action") or "")
    if LIBRARY_FLOW in plan.policy_path and action_name in {
        "download_options_tv",
        "download_options_movie",
    }:
        completed_match = ops.completed_download_match(action_payload)
        if completed_match is not None:
            _decision_log(
                correlation_id,
                "execute_action",
                "completed_download_short_circuit",
                request_id=request_id,
                action=action_name,
                matched_release=completed_match,
            )
            return ExecutedActionStage(
                intent_payload=plan.intent_payload,
                action_payload=action_payload,
                tool_result={
                    "ok": True,
                    "already_downloaded": True,
                    "matched_release": completed_match,
                },
                response_text=f"You already downloaded this: {completed_match}",
                media_type_hint=media_type_hint,
                season_hint=season_hint,
            )
    return None


def _run_primary_action(
    ops: RouterProviderOps,
    action_payload: dict[str, Any],
) -> dict[str, Any]:
    action_name = str(action_payload.get("action") or "")
    if action_name in {"download_options_tv", "download_options_movie", "search"}:
        return ops.library_lookup(action_payload)
    if action_name in {"download_grab_tv", "download_grab_movie", "indexer_grab"}:
        return ops.provider_grab(action_payload)
    if action_name == "indexer_search":
        season = (
            action_payload.get("season")
            if isinstance(action_payload.get("season"), int)
            else None
        )
        _, tool_result = ops.provider_search(
            str(action_payload.get("query") or "").strip(), season
        )
        return tool_result
    return ops.execute_action(action_payload)


def _try_indexer_fallback(
    *,
    ops: RouterProviderOps,
    request_id: str,
    correlation_id: str,
    plan: PlannedActionStage,
    action_payload: dict[str, Any],
    tool_result: dict[str, Any],
    media_type_hint: str | None,
    season_hint: int | None,
) -> tuple[dict[str, Any], dict[str, Any], str | None, int | None]:
    action_name = str(action_payload.get("action") or "")
    err = tool_result.get("error") if isinstance(tool_result, dict) else None
    err_code = (err or {}).get("code") if isinstance(err, dict) else None
    if (
        INDEXER_FLOW in plan.policy_path
        and action_name in {"download_options_tv", "download_options_movie"}
        and err_code in ROUTER_FALLBACK_CODES
    ):
        query = str(action_payload.get("query") or "").strip()
        season = (
            action_payload.get("season")
            if isinstance(action_payload.get("season"), int)
            else None
        )
        fallback_action, fallback_result = ops.provider_search(query, season)
        _decision_log(
            correlation_id,
            "execute_action",
            "indexer_fallback",
            request_id=request_id,
            from_action=action_name,
            error_code=err_code,
            fallback_ok=bool(fallback_result.get("ok")),
            fallback_options_count=(
                len(fallback_result.get("options") or [])
                if isinstance(fallback_result.get("options"), list)
                else 0
            ),
        )
        if fallback_result.get("ok") is True and (fallback_result.get("options") or []):
            previous_action = action_name
            action_payload = fallback_action
            tool_result = fallback_result
            action_name = str(action_payload.get("action") or action_name)
            media_type_hint = (
                "tv" if previous_action == "download_options_tv" else media_type_hint
            )
            season_hint = season if isinstance(season, int) else season_hint
    return action_payload, tool_result, media_type_hint, season_hint


def _save_pending_options_session(
    *,
    ops: RouterProviderOps,
    router_request: RouterRequest,
    action_payload: dict[str, Any],
    tool_result: dict[str, Any],
    action_name: str,
    media_type_hint: str | None,
    season_hint: int | None,
    request_id: str,
    correlation_id: str,
    router_state_ttl_s: int,
) -> None:
    if (
        router_request.session_key
        and action_name in SESSION_OPTION_SOURCE_ACTIONS
        and tool_result.get("ok") is True
        and (tool_result.get("options") or [])
    ):
        now_ms = int(time.time() * 1000)
        expires_ms = now_ms + max(30, int(router_state_ttl_s)) * 1000
        pending = ops.build_pending_options(action_name, tool_result)
        if pending:
            inferred_season = action_payload.get("season")
            if not isinstance(inferred_season, int):
                inferred_season = (
                    season_hint
                    if isinstance(season_hint, int)
                    else ops.extract_season_number(
                        str(action_payload.get("query") or "")
                    )
                )
            inferred_media_type = action_payload.get("type") or media_type_hint
            if inferred_media_type is None and action_name == "indexer_search":
                inferred_media_type = (
                    "tv"
                    if ops.extract_season_number(str(action_payload.get("query") or ""))
                    is not None
                    else None
                )
            ops.save_session_state(
                RouterSessionState(
                    session_key=router_request.session_key,
                    created_at_ms=now_ms,
                    expires_at_ms=expires_ms,
                    source_action=action_name,  # type: ignore[arg-type]
                    query=str(action_payload.get("query") or ""),
                    media_type=inferred_media_type,
                    season=inferred_season,
                    options=pending,
                )
            )
            _decision_log(
                correlation_id,
                "execute_action",
                "session_state_saved",
                request_id=request_id,
                session_key=router_request.session_key,
                option_count=len(pending),
            )


def _apply_post_grab_season_filter(
    *,
    ops: RouterProviderOps,
    router_request: RouterRequest,
    plan: PlannedActionStage,
    action_payload: dict[str, Any],
    tool_result: dict[str, Any],
    media_type_hint: str | None,
    season_hint: int | None,
    request_id: str,
    correlation_id: str,
) -> ExecutedActionStage | None:
    is_duplicate_grab = bool(
        isinstance(tool_result, dict)
        and tool_result.get("ok") is not True
        and isinstance(tool_result.get("error"), dict)
        and tool_result["error"].get("code") == "GRAB_FAILED"
        and "HTTP 500" in str(tool_result["error"].get("message") or "")
    )
    if (
        plan.intent == "selection"
        and plan.session_state is not None
        and action_payload.get("action") == "indexer_grab"
        and (tool_result.get("ok") is True or is_duplicate_grab)
        and plan.session_state.media_type == "tv"
        and isinstance(plan.session_state.season, int)
        and plan.selected_option is not None
        and plan.selected_option.release
    ):
        season_adjust = ops.post_grab_season_filter(
            plan.selected_option.release,
            plan.session_state.season,
        )
        if season_adjust is not None:
            tool_result["season_selection"] = season_adjust
            if is_duplicate_grab and season_adjust.get("status") == "season_only_applied":
                tool_result["ok"] = True
                tool_result["duplicate_grab_reused"] = True
                tool_result.pop("error", None)
        if router_request.session_key:
            ops.clear_session_state(router_request.session_key)
        season_state = tool_result.get("season_selection")
        if (
            isinstance(season_state, dict)
            and season_state.get("status") == "season_only_applied"
        ):
            _decision_log(
                correlation_id,
                "execute_action",
                "post_grab_season_only_applied",
                request_id=request_id,
                season=plan.session_state.season,
            )
            response_text = f"OK! It's downloading season {plan.session_state.season} only from that torrent."
            if is_duplicate_grab:
                response_text = (
                    f"That torrent was already in qBittorrent, so I reused it and set season "
                    f"{plan.session_state.season} only."
                )
            return ExecutedActionStage(
                intent_payload=plan.intent_payload,
                action_payload=action_payload,
                tool_result=tool_result,
                response_text=response_text,
                media_type_hint=media_type_hint,
                season_hint=season_hint,
            )
    return None


def execute_action(
    *,
    ops: RouterProviderOps,
    request_id: str,
    correlation_id: str,
    router_request: RouterRequest,
    plan: PlannedActionStage,
    router_state_ttl_s: int,
) -> ExecutedActionStage:
    if plan.action_payload is None:
        raise ValueError("missing action payload")

    action_payload = dict(plan.action_payload)
    media_type_hint = plan.media_type_hint
    season_hint = plan.season_hint

    reused = _try_reuse_existing_season(
        ops=ops,
        request_id=request_id,
        correlation_id=correlation_id,
        plan=plan,
        action_payload=action_payload,
        media_type_hint=media_type_hint,
        season_hint=season_hint,
    )
    if reused is not None:
        return reused

    completed = _try_completed_download_short_circuit(
        ops=ops,
        request_id=request_id,
        correlation_id=correlation_id,
        plan=plan,
        action_payload=action_payload,
        media_type_hint=media_type_hint,
        season_hint=season_hint,
    )
    if completed is not None:
        return completed

    tool_result = _run_primary_action(ops, action_payload)
    action_payload, tool_result, media_type_hint, season_hint = _try_indexer_fallback(
        ops=ops,
        request_id=request_id,
        correlation_id=correlation_id,
        plan=plan,
        action_payload=action_payload,
        tool_result=tool_result,
        media_type_hint=media_type_hint,
        season_hint=season_hint,
    )
    action_name = str(action_payload.get("action") or "")

    _save_pending_options_session(
        ops=ops,
        router_request=router_request,
        action_payload=action_payload,
        tool_result=tool_result,
        action_name=action_name,
        media_type_hint=media_type_hint,
        season_hint=season_hint,
        request_id=request_id,
        correlation_id=correlation_id,
        router_state_ttl_s=router_state_ttl_s,
    )

    post_grab = _apply_post_grab_season_filter(
        ops=ops,
        router_request=router_request,
        plan=plan,
        action_payload=action_payload,
        tool_result=tool_result,
        media_type_hint=media_type_hint,
        season_hint=season_hint,
        request_id=request_id,
        correlation_id=correlation_id,
    )
    if post_grab is not None:
        return post_grab

    if plan.intent == "selection" and router_request.session_key:
        ops.clear_session_state(router_request.session_key)

    response_text = ops.format_response(action_payload, tool_result)
    return ExecutedActionStage(
        intent_payload=plan.intent_payload,
        action_payload=action_payload,
        tool_result=tool_result,
        response_text=response_text,
        media_type_hint=media_type_hint,
        season_hint=season_hint,
    )


def render_response(
    *,
    request_id: str,
    correlation_id: str,
    parsed: ParsedIntentStage,
    executed: ExecutedActionStage | None = None,
) -> JSONResponse:
    if parsed.intent == "non_media":
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "request_id": request_id,
                "intent": parsed.intent_payload,
                "response_text": "This route handles media requests only. Use your normal chat flow for other tasks.",
            },
        )
    if parsed.season_prompt_required:
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "request_id": request_id,
                "intent": parsed.intent_payload,
                "response_text": "Tell me the season number and I will fetch options.",
            },
        )
    if executed is None:
        raise ValueError("render_response requires execution payload for media intents")
    _decision_log(
        correlation_id,
        "render_response",
        "response_ready",
        request_id=request_id,
        action=executed.action_payload.get("action"),
        tool_ok=bool(executed.tool_result.get("ok")),
    )
    return JSONResponse(
        status_code=200,
        content={
            "ok": True,
            "request_id": request_id,
            "intent": executed.intent_payload,
            "action": executed.action_payload,
            "tool_result": executed.tool_result,
            "response_text": executed.response_text,
        },
    )


def build_smoke_gate_payload(session_key: str = "smoke-cxg-s4") -> dict[str, Any]:
    return {
        "scenario": "cxg-season-4-first-option",
        "session_key": session_key,
        "steps": [
            {"message": "Get Crazy Ex-Girlfriend season 4", "session_key": session_key},
            {"message": "first option", "session_key": session_key},
        ],
        "expectations": {
            "follow_up_selection": {"rank": 1},
            "season_only_status": "season_only_applied",
        },
    }


def smoke_gate_verify_season_only(tool_result: dict[str, Any], season: int = 4) -> bool:
    season_selection = tool_result.get("season_selection")
    if not isinstance(season_selection, dict):
        return False
    return (
        season_selection.get("status") == "season_only_applied"
        and season_selection.get("season") == season
        and isinstance(season_selection.get("enabled_file_count"), int)
    )
