"""Phase B contract tests for the per-action handler registry.

Pin down the surface introduced by the refactor in ``app/actions/`` so future
phases (orchestrator switch, additional handlers) can't quietly drift.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError

import app.actions  # noqa: F401  ensures handlers register
from app.actions import registry
from app.actions.registry import ActionContext
from app.config import get_settings

_EXPECTED_ACTION_NAMES = (
    "search",
    "download_options_tv",
    "download_options_movie",
    "download_grab_tv",
    "download_grab_movie",
    "indexer_search",
    "indexer_grab",
)


def test_registry_has_seven_actions() -> None:
    names = registry.all_names()
    assert len(names) == 7
    assert set(names) == set(_EXPECTED_ACTION_NAMES)


def test_registry_definitions_match_models() -> None:
    by_name = {d["name"]: d for d in registry.all_definitions()}
    for name in registry.all_names():
        handler = registry.get(name)
        definition = by_name[name]
        assert handler.args_model.__name__ == definition["model"]


def test_registry_get_returns_handler_with_required_methods() -> None:
    required_attrs = (
        "name",
        "description",
        "result_category",
        "args_model",
        "run",
        "format_response",
        "selection_to_grab",
    )
    for name in registry.all_names():
        handler = registry.get(name)
        for attr in required_attrs:
            assert hasattr(handler, attr), f"{name} missing {attr}"


def test_registry_dispatch_validates_payload() -> None:
    with httpx.Client() as http:
        ctx = ActionContext(http=http, settings=get_settings())
        with pytest.raises(ValidationError):
            registry.dispatch(ctx, {"action": "search"})


def test_registry_dispatch_unknown_action() -> None:
    with httpx.Client() as http:
        ctx = ActionContext(http=http, settings=get_settings())
        with pytest.raises(ValidationError):
            registry.dispatch(ctx, {"action": "does_not_exist"})


def test_router_emittable_subset() -> None:
    emittable = set(registry.router_emittable_names())
    all_names = set(registry.all_names())
    assert emittable.issubset(all_names)
    expected = {h.name for h in registry.all_handlers() if h.router_may_emit}
    assert emittable == expected
