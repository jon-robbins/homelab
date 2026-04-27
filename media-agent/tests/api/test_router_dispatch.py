from __future__ import annotations

from typing import Any

import httpx
import respx
from fastapi.testclient import TestClient

from app.actions.download_tv import DownloadOptionsTV
from app.actions.indexer_search import IndexerSearch
from app.actions.search import Search
from app.api import dependencies as deps_mod
from app.config import reset_settings
from app.main import app

AUTH = {"Authorization": "Bearer test-bearer-secret"}


def _ollama_env(monkeypatch) -> None:
    monkeypatch.setenv("MEDIA_AGENT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    monkeypatch.setenv("MEDIA_AGENT_ROUTER_MODEL", "unit-model")
    monkeypatch.setenv("MEDIA_AGENT_ROUTER_MAX_RETRIES", "1")
    reset_settings()


def _isolate_state_store(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MEDIA_AGENT_ROUTER_STATE_PATH", str(tmp_path / "router-state.json"))
    reset_settings()
    deps_mod.reset_router_state_store()


@respx.mock
def test_router_dispatch_search_success(monkeypatch) -> None:
    """LLM returns 'search' for download-intent -> rewritten to indexer_search."""
    _ollama_env(monkeypatch)
    respx.post("http://ollama.test/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "content": '{"action":"search","type":"tv","query":"sample"}'
                }
            },
        )
    )
    monkeypatch.setattr(
        IndexerSearch,
        "run_for_router",
        lambda self, ctx, args: {
            "ok": True,
            "source": "prowlarr",
            "options": [
                {
                    "rank": 1,
                    "title": "Sample Show S01",
                    "seeders": 10,
                    "leechers": 1,
                    "size_human": "2.0 GB",
                    "indexer": "Unit",
                }
            ],
        },
    )
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "Get sample"},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["action"]["action"] == "indexer_search"
    assert d["tool_result"]["ok"] is True
    assert d["response_text"].startswith("Got it! Here are some options")


@respx.mock
def test_router_dispatch_parser_invalid(monkeypatch) -> None:
    _ollama_env(monkeypatch)
    respx.post("http://ollama.test/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={"message": {"content": "not json"}},
        )
    )
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "Get sample"},
            headers=AUTH,
        )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


@respx.mock
def test_router_dispatch_fallback_to_indexer_search(monkeypatch) -> None:
    """A title+season download request goes directly to indexer_search.

    Today the orchestrator rewrites ``download_options_tv`` →
    ``indexer_search`` for conversational requests via
    ``prefer_indexer_for_title_request``. So only the IndexerSearch handler
    fires; DownloadOptionsTV must NOT.
    """
    _ollama_env(monkeypatch)
    respx.post("http://ollama.test/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "content": '{"action":"download_options_tv","query":"Crazy Ex-Girlfriend","season":4}'
                }
            },
        )
    )

    calls: list[dict[str, Any]] = []

    def _fake_indexer_run(self, ctx, args):
        calls.append({"action": "indexer_search", "query": args.query})
        return {
            "ok": True,
            "source": "prowlarr",
            "options": [
                {
                    "rank": 1,
                    "title": "Crazy Ex-Girlfriend S04 Pack",
                    "seeders": 10,
                    "leechers": 1,
                    "size_human": "8.0 GB",
                    "indexer": "Unit",
                }
            ],
        }

    def _fail_tv_run(self, ctx, args):  # noqa: ARG001
        raise AssertionError("DownloadOptionsTV must not run for title+season requests")

    monkeypatch.setattr(IndexerSearch, "run_for_router", _fake_indexer_run)
    monkeypatch.setattr(DownloadOptionsTV, "run_for_router", _fail_tv_run)

    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "Get Crazy Ex-Girlfriend season 4"},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["action"]["action"] == "indexer_search"
    assert d["tool_result"]["ok"] is True
    assert d["response_text"].startswith("Got it! Here are some options")
    assert [c["action"] for c in calls] == ["indexer_search"]
    assert calls[0]["query"] == "Crazy Ex-Girlfriend season 4"


@respx.mock
def test_router_dispatch_fallback_persists_indexer_options_for_selection(
    monkeypatch, tmp_path
) -> None:
    """Falling through to indexer_search persists the session under
    ``source_action="indexer_search"``, so the follow-up selection grabs via
    ``indexer_grab`` not ``download_grab_tv``."""
    _ollama_env(monkeypatch)
    _isolate_state_store(monkeypatch, tmp_path)
    respx.post("http://ollama.test/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "content": '{"action":"download_options_tv","query":"Crazy Ex-Girlfriend","season":4}'
                }
            },
        )
    )

    def _fake_indexer_run(self, ctx, args):  # noqa: ARG001
        return {
            "ok": True,
            "source": "prowlarr",
            "options": [
                {
                    "rank": 1,
                    "title": "Crazy Ex-Girlfriend S01-S04",
                    "release": {"infoHash": "h1", "indexerId": 1, "guid": "g1"},
                }
            ],
        }

    from app.actions.indexer_grab import IndexerGrab

    def _fake_indexer_grab(self, ctx, args):  # noqa: ARG001
        return {"ok": True, "source": "prowlarr"}

    import app.router.post_grab as pg

    def _stub_season_only(http, s, release, season):  # noqa: ARG001
        return {
            "status": "season_only_applied",
            "season": 4,
            "torrent_hash": "h1",
            "enabled_file_count": 2,
            "disabled_other_season_file_count": 6,
        }

    monkeypatch.setattr(IndexerSearch, "run_for_router", _fake_indexer_run)
    monkeypatch.setattr(IndexerGrab, "run_for_router", _fake_indexer_grab)
    monkeypatch.setattr(pg, "season_only_selection_after_grab", _stub_season_only)

    with TestClient(app) as client:
        r1 = client.post(
            "/internal/media-agent/v1/router",
            json={
                "message": "Get Crazy Ex-Girlfriend season 4",
                "session_key": "chat-fallback",
            },
            headers=AUTH,
        )
        r2 = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "first option", "session_key": "chat-fallback"},
            headers=AUTH,
        )
    assert r1.status_code == 200, r1.text
    assert r1.json()["action"]["action"] == "indexer_search"
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["action"]["action"] == "indexer_grab"
    assert d2["tool_result"]["season_selection"]["status"] == "season_only_applied"


@respx.mock
def test_router_title_request_uses_indexer_first(monkeypatch) -> None:
    """A movie title-only request (no season) is also rewritten to indexer_search."""
    _ollama_env(monkeypatch)
    respx.post("http://ollama.test/api/chat").mock(
        return_value=httpx.Response(
            200,
            json={
                "message": {
                    "content": '{"action":"download_options_movie","query":"Interstellar"}'
                }
            },
        )
    )

    calls: list[dict[str, Any]] = []

    def _fake_indexer_run(self, ctx, args):
        calls.append({"action": "indexer_search", "query": args.query, "limit": args.limit, "search_type": args.search_type})
        return {
            "ok": True,
            "options": [
                {
                    "rank": 1,
                    "title": "Interstellar.2014.1080p",
                    "release": {"guid": "g1", "indexerId": 1},
                }
            ],
        }

    from app.actions.download_movie import DownloadOptionsMovie

    def _fail_movie_run(self, ctx, args):  # noqa: ARG001
        raise AssertionError("DownloadOptionsMovie must not run for title-only requests")

    monkeypatch.setattr(IndexerSearch, "run_for_router", _fake_indexer_run)
    monkeypatch.setattr(DownloadOptionsMovie, "run_for_router", _fail_movie_run)

    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "Download Interstellar"},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["action"]["action"] == "indexer_search"
    assert calls == [
        {
            "action": "indexer_search",
            "query": "Interstellar",
            "limit": 10,
            "search_type": "search",
        }
    ]
    assert d["response_text"].startswith("Got it! Here are some options")


def test_router_non_media_intent() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "what time is it in madrid"},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["intent"]["intent"] == "non_media"
    assert "media requests only" in d["response_text"]


def test_router_missing_season_number() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "Get Crazy Ex Girlfriend season"},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["response_text"] == "Tell me the season number and I will fetch options."
