"""Helpers in app.router.parser that don't need an Ollama call."""

from __future__ import annotations

import pytest

from app.router.parser import (
    ROUTER_SCHEMA,
    heuristic_action_from_message,
    normalize_router_candidate,
    parse_json_object,
)


def test_router_schema_includes_all_emittable_actions() -> None:
    enum = ROUTER_SCHEMA["properties"]["action"]["enum"]
    assert "search" in enum
    assert "indexer_search" in enum
    assert "download_options_tv" in enum


def test_normalize_router_candidate_unwraps_nested_payload_and_aliases() -> None:
    candidate = {
        "payload": {
            "action": "search",
            "type": "tv",
            "title": "ab",
        }
    }
    normalized = normalize_router_candidate(candidate)
    assert normalized["action"] == "search"
    assert normalized["query"] == "ab"
    assert "title" not in normalized


def test_normalize_router_candidate_drops_unknown_fields() -> None:
    candidate = {
        "action": "search",
        "type": "tv",
        "query": "ab",
        "extra": "ignored",
    }
    normalized = normalize_router_candidate(candidate)
    assert "extra" not in normalized


def test_parse_json_object_extracts_object_from_noisy_text() -> None:
    text = "Sure! Here you go:\n{\"action\": \"search\", \"type\": \"tv\", \"query\": \"ab\"}\nThanks"
    obj = parse_json_object(text)
    assert obj["action"] == "search"


def test_parse_json_object_raises_on_no_object() -> None:
    with pytest.raises(ValueError):
        parse_json_object("plain text without braces")


def test_heuristic_action_from_message_extracts_show_and_season() -> None:
    fallback = heuristic_action_from_message("get Crazy Ex Girlfriend season 4")
    assert fallback is not None
    assert fallback["action"] == "indexer_search"
    assert "Crazy Ex Girlfriend" in fallback["query"]
    assert "season 4" in fallback["query"]


def test_heuristic_action_from_message_returns_none_without_season() -> None:
    assert heuristic_action_from_message("get Crazy Ex Girlfriend") is None
