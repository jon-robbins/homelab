"""Per-action handler registry.

Each agent-callable capability is a single ActionHandler that owns:
  - args_model: Pydantic model used by the strict /action endpoint and the
    LLM router parser.
  - run(): the deterministic workflow used by /action.
  - run_for_router(): the conversational workflow used by /router (defaults
    to run; download_options_tv/movie override to add qB reuse,
    completed-download short-circuit, and indexer fallback).
  - format_response(): conversational text rendered by the router.
  - selection_to_grab(): for actions that produce ranked options, maps a
    selected RouterPendingOption to the grab-action payload.

Adding a new action = create a new module under app/actions/ and import it
from app/actions/__init__.py.

Router-only conventions on the result dict returned by run_for_router:
  - ``_router_action_override`` (dict) — when the handler effectively
    pivoted to a different action (e.g. download_options_tv falling
    through to indexer_search), set this to the new action call payload.
    The orchestrator uses it to relabel the executed action for session
    persistence and response formatting.
  - ``_router_response_text`` (str) — friendly conversational text that
    overrides the default ``format_response`` for special-cased flows
    such as qB reuse and completed-download short-circuits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel

from app.config import Settings
from app.models.router import RouterPendingOption, RouterSessionState


@dataclass(slots=True, frozen=True)
class ActionContext:
    """Runtime collaborators a handler needs to do their work."""

    http: httpx.Client
    settings: Settings


class ActionHandler[ArgsT: BaseModel]:
    """Base class for per-action handlers.

    Subclasses set the class-level metadata (``name``, ``description``,
    ``result_category``, ``args_model``, ``router_may_emit``) and implement
    ``run``. They may override ``run_for_router``, ``format_response``, and
    ``selection_to_grab`` as needed.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    result_category: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]
    router_may_emit: ClassVar[bool] = True

    def run(self, ctx: ActionContext, args: ArgsT) -> dict[str, Any]:
        """Strict deterministic execution; no fallbacks. Used by /action."""
        raise NotImplementedError

    def run_for_router(self, ctx: ActionContext, args: ArgsT) -> dict[str, Any]:
        """Conversational execution with reuse / short-circuit / fallback.

        Default delegates to ``run``. Override per-handler to add
        conversational policies. May set ``_router_action_override`` /
        ``_router_response_text`` on the returned dict; see the module
        docstring.
        """
        return self.run(ctx, args)

    def format_response(self, args: ArgsT, result: dict[str, Any]) -> str:
        """Render conversational text for the router endpoint."""
        from .formatting import default_format_response

        return default_format_response(self.name, result)

    def selection_to_grab(
        self, state: RouterSessionState, selected: RouterPendingOption
    ) -> dict[str, Any] | None:
        """Map a selected pending option to a grab-action payload."""
        return None


_REGISTRY: dict[str, ActionHandler[BaseModel]] = {}
_ORDER: list[str] = []


def register_action(handler_cls: type[ActionHandler[BaseModel]]) -> type[ActionHandler[BaseModel]]:
    """Class decorator that instantiates and registers a handler."""
    instance = handler_cls()
    if instance.name not in _REGISTRY:
        _ORDER.append(instance.name)
    _REGISTRY[instance.name] = instance
    return handler_cls


def get(name: str) -> ActionHandler[BaseModel]:
    return _REGISTRY[name]


def has(name: str) -> bool:
    return name in _REGISTRY


def all_handlers() -> list[ActionHandler[BaseModel]]:
    """Return handlers in registration order."""
    return [_REGISTRY[name] for name in _ORDER]


def all_definitions() -> list[dict[str, Any]]:
    """Public-facing action metadata for GET /functions."""
    return [
        {
            "name": h.name,
            "model": h.args_model.__name__,
            "description": h.description,
            "result_category": h.result_category,
            "router_may_emit": h.router_may_emit,
        }
        for h in all_handlers()
    ]


def all_names() -> tuple[str, ...]:
    return tuple(_ORDER)


def router_emittable_names() -> tuple[str, ...]:
    return tuple(name for name in _ORDER if _REGISTRY[name].router_may_emit)


def dispatch(ctx: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate payload via the discriminated union, then run the handler."""
    from app.models.actions import ACTION_CALL_ADAPTER

    call = ACTION_CALL_ADAPTER.validate_python(payload)
    handler = _REGISTRY[str(call.action)]
    args = handler.args_model.model_validate(payload)
    return handler.run(ctx, args)


def dispatch_for_router(ctx: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate payload, then run via the conversational ``run_for_router`` path."""
    from app.models.actions import ACTION_CALL_ADAPTER

    call = ACTION_CALL_ADAPTER.validate_python(payload)
    handler = _REGISTRY[str(call.action)]
    args = handler.args_model.model_validate(payload)
    return handler.run_for_router(ctx, args)


__all__ = [
    "ActionContext",
    "ActionHandler",
    "register_action",
    "get",
    "has",
    "all_handlers",
    "all_definitions",
    "all_names",
    "router_emittable_names",
    "dispatch",
    "dispatch_for_router",
]
