"""Phase B per-handler smoke tests.

Lightweight tests that drive the handlers directly through ``registry`` (not
through FastAPI) and per-handler unit tests for ``selection_to_grab`` and
``format_response``.
"""

from __future__ import annotations

import httpx
import respx

import app.actions  # noqa: F401  ensures handlers register
from app.actions import registry
from app.actions.registry import ActionContext
from app.config import get_settings, reset_settings
from app.models.actions import (
    ActionDownloadGrabMovie,
    ActionDownloadGrabTV,
    ActionDownloadOptionsTV,
    ActionSearch,
)
from app.models.router import RouterPendingOption, RouterSessionState


def _ctx(http: httpx.Client) -> ActionContext:
    return ActionContext(http=http, settings=get_settings())


def _state(source_action: str) -> RouterSessionState:
    return RouterSessionState(
        session_key="s",
        created_at_ms=1,
        expires_at_ms=2,
        source_action=source_action,  # type: ignore[arg-type]
        query="q",
    )


@respx.mock
def test_search_action_runs() -> None:
    respx.get("http://sonarr.test/son/api/v3/series/lookup?term=sample").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "title": "Sample Show",
                    "year": 2020,
                    "overview": "A show.",
                    "tvdbId": 111,
                }
            ],
        )
    )
    respx.get("http://sonarr.test/son/api/v3/series").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get("http://radarr.test/rad/api/v3/movie").mock(
        return_value=httpx.Response(200, json=[])
    )

    with httpx.Client() as http:
        result = registry.dispatch(
            _ctx(http),
            {"action": "search", "type": "tv", "query": "sample"},
        )
    assert result["ok"] is True
    assert result["results"][0]["title"] == "Sample Show"


@respx.mock
def test_indexer_search_action_runs(monkeypatch) -> None:
    monkeypatch.setenv("PROWLARR_URL", "http://prowlarr.test")
    monkeypatch.setenv("PROWLARR_API_KEY", "pk")
    reset_settings()
    respx.get("http://prowlarr.test/api/v1/search").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "title": "Sample release",
                    "guid": "g1",
                    "indexerId": 2,
                    "size": 5000000,
                    "seeders": 5,
                    "leechers": 1,
                    "indexer": "T",
                }
            ],
        )
    )
    with httpx.Client() as http:
        result = registry.dispatch(
            _ctx(http),
            {"action": "indexer_search", "query": "sample terms", "limit": 5},
        )
    assert result["ok"] is True
    assert result["options"][0]["title"] == "Sample release"


@respx.mock
def test_grab_tv_action_runs() -> None:
    rel = [
        {
            "guid": "a",
            "title": "x",
            "downloadAllowed": True,
            "indexer": "T",
        }
    ]
    respx.get("http://sonarr.test/son/api/v3/release?episodeId=10").mock(
        return_value=httpx.Response(200, json=rel)
    )
    respx.post("http://sonarr.test/son/api/v3/release").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )
    with httpx.Client() as http:
        result = registry.dispatch(
            _ctx(http),
            {"action": "download_grab_tv", "episode_id": 10, "guid": "a"},
        )
    assert result["ok"] is True
    assert result["app"] == "sonarr"


def test_indexer_search_handler_selection_to_grab() -> None:
    handler = registry.get("indexer_search")
    state = _state("indexer_search")
    option = RouterPendingOption(
        rank=1, title="t", release={"guid": "g1", "indexerId": 2}
    )
    assert handler.selection_to_grab(state, option) == {
        "action": "indexer_grab",
        "release": {"guid": "g1", "indexerId": 2},
    }


def test_download_tv_handler_selection_to_grab() -> None:
    handler = registry.get("download_options_tv")
    state = _state("download_options_tv")
    option = RouterPendingOption(rank=1, title="t", guid="g1", episode_id=10)
    assert handler.selection_to_grab(state, option) == {
        "action": "download_grab_tv",
        "guid": "g1",
        "episode_id": 10,
    }


def test_download_movie_handler_selection_to_grab() -> None:
    handler = registry.get("download_options_movie")
    state = _state("download_options_movie")
    option = RouterPendingOption(rank=1, title="t", guid="g1", movie_id=20)
    assert handler.selection_to_grab(state, option) == {
        "action": "download_grab_movie",
        "guid": "g1",
        "movie_id": 20,
    }


def test_download_tv_handler_format_response_options_table() -> None:
    handler = registry.get("download_options_tv")
    args = ActionDownloadOptionsTV(
        action="download_options_tv", query="ab", season=1
    )
    result = {
        "ok": True,
        "options": [
            {
                "rank": 1,
                "title": "X",
                "seeders": 10,
                "leechers": 1,
                "size_human": "5.0 GiB",
                "indexer": "T",
            }
        ],
    }
    text = handler.format_response(args, result)
    assert text.startswith("Got it! Here are some options")


def test_search_handler_format_response_results_list() -> None:
    handler = registry.get("search")
    args = ActionSearch(action="search", type="tv", query="ab")
    result = {"ok": True, "results": [{"title": "X", "year": 2020}]}
    text = handler.format_response(args, result)
    assert text.startswith("I found these matches:")


def test_grab_handlers_format_response_returns_ok_downloading() -> None:
    tv_handler = registry.get("download_grab_tv")
    tv_args = ActionDownloadGrabTV(
        action="download_grab_tv", guid="g1", episode_id=10
    )
    assert tv_handler.format_response(tv_args, {"ok": True}) == "OK! It's downloading."

    movie_handler = registry.get("download_grab_movie")
    movie_args = ActionDownloadGrabMovie(
        action="download_grab_movie", guid="g1", movie_id=20
    )
    assert (
        movie_handler.format_response(movie_args, {"ok": True})
        == "OK! It's downloading."
    )
