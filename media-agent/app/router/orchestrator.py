"""Top-down router dispatch.

Replaces the 5-stage dataclass pipeline (Phase A/B) with a single
``dispatch`` function that drives the conversation flow end-to-end:

    classify intent
        -> non_media               -> friendly redirect
        -> selection (no session)  -> friendly redirect  (mirrors prior stale-state behavior)
        -> selection (with session)
            -> resolve pending option
            -> map to grab action
            -> handler.run_for_router
            -> apply post-grab season-only filter (TV+season selections)
            -> clear session
        -> download
            -> if "season" without a number: prompt
            -> parse user message via the LLM action parser
            -> rewrite title-only requests to indexer_search
            -> handler.run_for_router  (handler may pivot via _router_action_override)
            -> persist pending options when handler returned a ranked list

Each handler is responsible for its own conversational policy (qB reuse,
completed-download short-circuit, indexer fallback). The orchestrator is
glue: intent -> registry -> session.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..actions import registry
from ..actions.registry import ActionContext
from ..config import Settings
from ..models.router import RouterRequest
from .intent import (
    classify_intent,
    parse_selection_choice,
    prefer_indexer_for_title_request,
    resolve_pending_option,
    season_prompt_needed,
)
from .parser import parse_router_action
from .post_grab import apply_post_grab_season_only
from .session import (
    RouterStateStore,
    maybe_persist_pending_options,
    selection_to_action_from_session,
)


@dataclass(slots=True, frozen=True)
class RouterContext:
    """Collaborators the router orchestrator needs."""

    http: httpx.Client
    settings: Settings
    state_store: RouterStateStore
    logger: logging.Logger

    @property
    def action_ctx(self) -> ActionContext:
        return ActionContext(http=self.http, settings=self.settings)


def _decision_log(
    logger: logging.Logger, correlation_id: str, stage: str, event: str, **data: Any
) -> None:
    payload = {
        "correlation_id": correlation_id,
        "stage": stage,
        "event": event,
        "data": data,
    }
    logger.info("router_decision=%s", json.dumps(payload, sort_keys=True, default=str))


def _non_media_body(decision, request_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "request_id": request_id,
        "intent": decision.model_dump(),
        "response_text": (
            "This route handles media requests only. "
            "Use your normal chat flow for other tasks."
        ),
    }


def _season_prompt_body(decision, request_id: str) -> dict[str, Any]:
    return {
        "ok": True,
        "request_id": request_id,
        "intent": decision.model_dump(),
        "response_text": "Tell me the season number and I will fetch options.",
    }


def _executed_body(
    *,
    decision,
    action_payload: dict[str, Any],
    tool_result: dict[str, Any],
    response_text: str,
    request_id: str,
) -> dict[str, Any]:
    return {
        "ok": True,
        "request_id": request_id,
        "intent": decision.model_dump(),
        "action": action_payload,
        "tool_result": tool_result,
        "response_text": response_text,
    }


def dispatch(
    ctx: RouterContext, request: RouterRequest, request_id: str
) -> dict[str, Any]:
    """Drive the router request top-down. Returns the JSON-ready response body.

    May raise ``ValueError`` when the user's selection cannot be resolved; the
    caller maps that to a 400. Upstream HTTP failures propagate so the route
    layer can map them via ``translate_upstream_errors``.
    """
    correlation_id = request_id
    session = (
        ctx.state_store.get(request.session_key) if request.session_key else None
    )
    decision = classify_intent(request.message, has_session_state=session is not None)

    _decision_log(
        ctx.logger,
        correlation_id,
        "classify_intent",
        "intent_decided",
        request_id=request_id,
        session_key=request.session_key,
        intent=decision.intent,
        has_session=bool(session),
    )

    if decision.intent == "non_media":
        return _non_media_body(decision, request_id)

    selected_option = None

    if decision.intent == "selection":
        if session is None:
            # Selection-shaped message but no live session — surface as non_media
            # so the caller falls back to its normal chat flow (matches the
            # prior orchestrator behavior asserted by
            # test_router_selection_followup_stale_state).
            return _non_media_body(decision, request_id)

        selection = parse_selection_choice(request.message)
        if selection is None:
            raise ValueError("selection must include a rank number or option id")
        selected_option = resolve_pending_option(
            options=session.options, selection=selection
        )
        if selected_option is None:
            rank_or_id = selection.option_id or str(selection.rank)
            raise ValueError(
                f"selection {rank_or_id} is not available in current options"
            )
        action_payload = selection_to_action_from_session(session, selected_option)
        if action_payload is None:
            raise ValueError("selection payload is not available in current options")
        _decision_log(
            ctx.logger,
            correlation_id,
            "hydrate_context",
            "selection_hydrated",
            request_id=request_id,
            source_action=session.source_action,
            option_id=selected_option.option_id,
            rank=selected_option.rank,
            action=action_payload.get("action"),
        )
    else:
        # download intent
        if season_prompt_needed(request.message):
            return _season_prompt_body(decision, request_id)
        parsed_action = parse_router_action(ctx.http, ctx.settings, request.message)
        action_payload = prefer_indexer_for_title_request(parsed_action)
        _decision_log(
            ctx.logger,
            correlation_id,
            "hydrate_context",
            "action_parsed",
            request_id=request_id,
            parsed_action=parsed_action.get("action"),
            action=action_payload.get("action"),
            query=action_payload.get("query"),
            season=action_payload.get("season"),
        )

    handler = registry.get(str(action_payload["action"]))
    args = handler.args_model.model_validate(action_payload)
    result = handler.run_for_router(ctx.action_ctx, args)

    # A handler may pivot to a different action (e.g. download_options_tv falling
    # through to indexer_search). The hint is a private key on the result dict.
    override = (
        result.pop("_router_action_override", None) if isinstance(result, dict) else None
    )
    if isinstance(override, dict) and override.get("action"):
        action_payload = override
        handler = registry.get(str(action_payload["action"]))
        args = handler.args_model.model_validate(action_payload)
        _decision_log(
            ctx.logger,
            correlation_id,
            "execute_action",
            "handler_action_override",
            request_id=request_id,
            action=action_payload.get("action"),
        )

    response_text_override = (
        result.pop("_router_response_text", None) if isinstance(result, dict) else None
    )

    persisted = maybe_persist_pending_options(
        store=ctx.state_store,
        session_key=request.session_key,
        action_payload=action_payload,
        tool_result=result,
        ttl_s=int(ctx.settings.router_state_ttl_s),
    )
    if persisted:
        _decision_log(
            ctx.logger,
            correlation_id,
            "execute_action",
            "session_state_saved",
            request_id=request_id,
            session_key=request.session_key,
            option_count=persisted,
        )

    if decision.intent == "selection":
        result, post_text = apply_post_grab_season_only(
            http=ctx.http,
            settings=ctx.settings,
            session=session,
            selected_option=selected_option,
            action_payload=action_payload,
            tool_result=result,
        )
        if post_text is not None:
            response_text_override = post_text
            _decision_log(
                ctx.logger,
                correlation_id,
                "execute_action",
                "post_grab_season_only_applied",
                request_id=request_id,
                season=session.season if session else None,
            )
        if request.session_key:
            ctx.state_store.clear(request.session_key)

    response_text = response_text_override or handler.format_response(args, result)
    _decision_log(
        ctx.logger,
        correlation_id,
        "render_response",
        "response_ready",
        request_id=request_id,
        action=action_payload.get("action"),
        tool_ok=bool(result.get("ok")),
    )
    return _executed_body(
        decision=decision,
        action_payload=action_payload,
        tool_result=result,
        response_text=response_text,
        request_id=request_id,
    )


__all__ = ["RouterContext", "dispatch"]
