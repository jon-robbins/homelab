from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from app.actions.indexer_grab import IndexerGrab
from app.actions.indexer_search import IndexerSearch
from app.api import dependencies as deps_mod
from app.config import reset_settings
from app.main import app
from app.models.router import RouterPendingOption, RouterSessionState

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
def test_router_selection_followup_success(monkeypatch, tmp_path) -> None:
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

    indexer_calls: list[dict] = []

    def _fake_indexer_run(self, ctx, args):  # noqa: ARG001
        indexer_calls.append({"query": args.query})
        return {
            "ok": True,
            "options": [
                {
                    "rank": 1,
                    "title": "Pack 1",
                    "release": {"guid": "g1", "indexerId": 1, "infoHash": "h1"},
                    "seeders": 10,
                    "leechers": 1,
                    "size_human": "2.0 GB",
                    "indexer": "Unit",
                }
            ],
        }

    grab_calls: list[dict] = []

    def _fake_indexer_grab(self, ctx, args):  # noqa: ARG001
        grab_calls.append({"release": args.release})
        return {"ok": True, "source": "prowlarr"}

    monkeypatch.setattr(IndexerSearch, "run_for_router", _fake_indexer_run)
    monkeypatch.setattr(IndexerGrab, "run_for_router", _fake_indexer_grab)

    with TestClient(app) as client:
        r1 = client.post(
            "/internal/media-agent/v1/router",
            json={
                "message": "Get Crazy Ex-Girlfriend season 4",
                "session_key": "chat-1",
            },
            headers=AUTH,
        )
        r2 = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "1", "session_key": "chat-1"},
            headers=AUTH,
        )
    assert r1.status_code == 200, r1.text
    assert r1.json()["response_text"].startswith("Got it! Here are some options")
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2["action"]["action"] == "indexer_grab"
    assert indexer_calls[0]["query"] == "Crazy Ex-Girlfriend season 4"
    assert grab_calls[0]["release"]["guid"] == "g1"


@respx.mock
def test_router_selection_followup_by_option_id(monkeypatch, tmp_path) -> None:
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
            "options": [
                {
                    "rank": 1,
                    "title": "Pack 1",
                    "release": {"guid": "g1", "indexerId": 1, "infoHash": "h1"},
                },
                {
                    "rank": 2,
                    "title": "Pack 2",
                    "release": {"guid": "g2", "indexerId": 1, "infoHash": "h2"},
                },
            ],
        }

    grab_calls: list[dict] = []

    def _fake_indexer_grab(self, ctx, args):  # noqa: ARG001
        grab_calls.append({"release": args.release})
        return {"ok": True, "source": "prowlarr"}

    monkeypatch.setattr(IndexerSearch, "run_for_router", _fake_indexer_run)
    monkeypatch.setattr(IndexerGrab, "run_for_router", _fake_indexer_grab)

    with TestClient(app) as client:
        r1 = client.post(
            "/internal/media-agent/v1/router",
            json={
                "message": "Get Crazy Ex-Girlfriend season 4",
                "session_key": "chat-opt",
            },
            headers=AUTH,
        )
        state = deps_mod.get_router_state_store().get("chat-opt")
        assert state is not None
        option_id = state.options[0].option_id
        assert option_id is not None
        r2 = client.post(
            "/internal/media-agent/v1/router",
            json={
                "message": f"download option_id {option_id}",
                "session_key": "chat-opt",
            },
            headers=AUTH,
        )
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r2.json()["action"]["action"] == "indexer_grab"
    assert grab_calls[0]["release"]["guid"] == "g1"


def test_router_selection_followup_stale_state(monkeypatch, tmp_path) -> None:
    _isolate_state_store(monkeypatch, tmp_path)
    store = deps_mod.get_router_state_store()
    store.set(
        RouterSessionState(
            session_key="chat-stale",
            created_at_ms=1,
            expires_at_ms=2,
            source_action="download_options_tv",
            query="Show",
            media_type="tv",
            season=4,
            options=[RouterPendingOption(rank=1, title="one", guid="g1", episode_id=99)],
        )
    )
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "1", "session_key": "chat-stale"},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["intent"]["intent"] == "non_media"


def test_router_selection_followup_indexer_grab_applies_season_only(
    monkeypatch, tmp_path
) -> None:
    _isolate_state_store(monkeypatch, tmp_path)
    store = deps_mod.get_router_state_store()
    store.set(
        RouterSessionState(
            session_key="chat-indexer-season",
            created_at_ms=1,
            expires_at_ms=9999999999999,
            source_action="indexer_search",
            query="Crazy Ex Girlfriend season 4",
            media_type="tv",
            season=4,
            options=[
                RouterPendingOption(
                    rank=1,
                    title="CXG S01-S04",
                    release={"infoHash": "abc123"},
                )
            ],
        )
    )

    def _fake_indexer_grab(self, ctx, args):  # noqa: ARG001
        return {"ok": True, "source": "prowlarr"}

    import app.router.post_grab as pg

    def _stub_season_only(http, s, release, season):  # noqa: ARG001
        return {
            "status": "season_only_applied",
            "season": 4,
            "torrent_hash": "abc123",
            "enabled_file_count": 10,
            "disabled_other_season_file_count": 30,
        }

    monkeypatch.setattr(IndexerGrab, "run_for_router", _fake_indexer_grab)
    monkeypatch.setattr(pg, "season_only_selection_after_grab", _stub_season_only)

    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "1", "session_key": "chat-indexer-season"},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["action"]["action"] == "indexer_grab"
    assert d["tool_result"]["season_selection"]["status"] == "season_only_applied"
    assert d["response_text"] == "OK! It's downloading season 4 only from that torrent."


def test_router_selection_followup_indexer_grab_duplicate_reuses_and_applies_season_only(
    monkeypatch, tmp_path
) -> None:
    _isolate_state_store(monkeypatch, tmp_path)
    store = deps_mod.get_router_state_store()
    store.set(
        RouterSessionState(
            session_key="chat-indexer-dup-season",
            created_at_ms=1,
            expires_at_ms=9999999999999,
            source_action="indexer_search",
            query="Crazy Ex Girlfriend season 4",
            media_type="tv",
            season=4,
            options=[
                RouterPendingOption(
                    rank=1,
                    title="CXG S01-S04",
                    release={"infoHash": "abc123"},
                )
            ],
        )
    )

    def _fake_indexer_grab(self, ctx, args):  # noqa: ARG001
        return {
            "ok": False,
            "error": {"code": "GRAB_FAILED", "message": "prowlarr grab failed: HTTP 500"},
        }

    import app.router.post_grab as pg

    def _stub_season_only(http, s, release, season):  # noqa: ARG001
        return {
            "status": "season_only_applied",
            "season": 4,
            "torrent_hash": "abc123",
            "enabled_file_count": 10,
            "disabled_other_season_file_count": 30,
        }

    monkeypatch.setattr(IndexerGrab, "run_for_router", _fake_indexer_grab)
    monkeypatch.setattr(pg, "season_only_selection_after_grab", _stub_season_only)

    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/router",
            json={"message": "1", "session_key": "chat-indexer-dup-season"},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["action"]["action"] == "indexer_grab"
    assert d["tool_result"]["ok"] is True
    assert d["tool_result"]["duplicate_grab_reused"] is True
    assert d["tool_result"]["season_selection"]["status"] == "season_only_applied"
    assert "already in qBittorrent" in d["response_text"]
