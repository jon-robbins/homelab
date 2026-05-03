import httpx
import respx
from fastapi.testclient import TestClient

import app.router.router_orchestrator as orchestrator_mod
from app.core.config import reset_settings
from app.main import app
from app.router.router_runtime_helpers import (
    _extract_season_number,
    _parse_selection_rank,
    _query_matches_torrent_name,
    _season_path_matches,
)
from app.router.router_selection import canonical_option_id, parse_selection_choice

AUTH = {"Authorization": "Bearer test-bearer-secret"}
__test__ = False


@respx.mock
def test_search_tv_success() -> None:
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
    respx.get("http://sonarr.test/son/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get("http://radarr.test/rad/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={})
    )

    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/search",
            json={"type": "tv", "query": "sample"},
            headers=AUTH,
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["type"] == "tv"
    assert data["query"] == "sample"
    assert data["results"][0]["title"] == "Sample Show"
    assert data["results"][0]["external_ids"]["tvdb"] == 111


@respx.mock
def test_search_unauthorized() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/search",
            json={"type": "tv", "query": "ab"},
        )
    assert r.status_code == 401
    assert r.json()["ok"] is False
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_search_validation_extra_field() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/search",
            json={"type": "tv", "query": "ab", "foo": 1},
            headers=AUTH,
        )
    assert r.status_code == 400


@respx.mock
def test_health() -> None:
    respx.get("http://sonarr.test/son/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get("http://radarr.test/rad/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={})
    )
    with TestClient(app) as client:
        r = client.get(
            "/internal/media-agent/v1/health", headers=AUTH
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sonarr"] == "ok"
    assert body["radarr"] == "ok"
    assert body["prowlarr"] == "n/a"


@respx.mock
def test_download_options_tv_merges_releases() -> None:
    respx.get("http://sonarr.test/son/api/v3/parse?title=Sample+S01E01").mock(
        return_value=httpx.Response(
            200,
            json={
                "episodes": [{"id": 10, "seasonNumber": 1, "episodeNumber": 1}],
                "series": {"id": 1, "title": "Sample"},
            },
        )
    )
    respx.get("http://sonarr.test/son/api/v3/series/1").mock(
        return_value=httpx.Response(
            200,
            json={"id": 1, "title": "Sample", "seriesType": "standard"},
        )
    )
    respx.get("http://sonarr.test/son/api/v3/episode?seriesId=1&seasonNumber=2").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 100, "episodeNumber": 1}, {"id": 101, "episodeNumber": 2}],
        )
    )
    respx.get("http://sonarr.test/son/api/v3/episode?seriesId=1&seasonNumber=1").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 50, "episodeNumber": 1}],
        )
    )
    respx.post("http://sonarr.test/son/api/v3/command").mock(
        return_value=httpx.Response(201, json={"id": 1, "name": "SeasonSearch"})
    )
    rel = [
        {
            "guid": "a",
            "title": "S02 pack",
            "seeders": 10,
            "leechers": 1,
            "size": 1000,
            "indexer": "Unit",
            "approved": True,
            "downloadAllowed": True,
            "rejections": [],
        }
    ]
    respx.get("http://sonarr.test/son/api/v3/release?episodeId=50").mock(
        return_value=httpx.Response(200, json=rel)
    )
    respx.get("http://sonarr.test/son/api/v3/release?episodeId=100").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get("http://sonarr.test/son/api/v3/release?episodeId=101").mock(
        return_value=httpx.Response(200, json=[])
    )

    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/download-options",
            json={
                "type": "tv",
                "query": "Sample",
                "season": 2,
                "include_full_series_packs": True,
            },
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["type"] == "tv"
    assert d["options"][0]["rank"] == 1
    assert d["options"][0]["title"] == "S02 pack"
    assert d["options"][0]["seeders"] == 10
    assert d["options"][0]["leechers"] == 1
    assert d["options"][0]["size"] == 1000
    assert d["options"][0]["guid"] == "a"
    assert d["options"][0]["episode_id"] == 50


@respx.mock
def test_download_grab_tv() -> None:
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
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/download-grab",
            json={"type": "tv", "episode_id": 10, "guid": "a"},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["app"] == "sonarr"


@respx.mock
def test_indexer_search_prowlarr(monkeypatch) -> None:
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
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/indexer-search",
            json={"query": "sample terms", "limit": 5},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["options"][0]["title"] == "Sample release"
    assert d["options"][0]["rank"] == 1
    assert d["options"][0]["release"]["guid"] == "g1"


@respx.mock
def test_indexer_grab_prowlarr(monkeypatch) -> None:
    monkeypatch.setenv("PROWLARR_URL", "http://prowlarr.test")
    monkeypatch.setenv("PROWLARR_API_KEY", "pk")
    reset_settings()
    respx.post("http://prowlarr.test/api/v1/search").mock(
        return_value=httpx.Response(200, json={})
    )
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/indexer-grab",
            json={"release": {"guid": "g1", "indexerId": 2, "title": "t"}},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_functions_list_requires_auth() -> None:
    with TestClient(app) as client:
        r = client.get("/internal/media-agent/v1/functions")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_functions_list_success() -> None:
    with TestClient(app) as client:
        r = client.get("/internal/media-agent/v1/functions", headers=AUTH)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert "search" in d["functions"]
    assert "download_options_tv" in d["functions"]
    assert "indexer_grab" in d["functions"]


@respx.mock
def test_action_dispatch_search_success() -> None:
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
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/action",
            json={"action": "search", "type": "tv", "query": "sample"},
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["type"] == "tv"
    assert d["results"][0]["title"] == "Sample Show"


def test_action_dispatch_validation_error() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/action",
            json={"action": "download_grab_tv", "guid": "g1"},
            headers=AUTH,
        )
    assert r.status_code == 400
    assert r.json()["ok"] is False
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_season_matcher_ignores_single_episode_release() -> None:
    matched = _query_matches_torrent_name(
        query="Crazy Ex Girlfriend",
        torrent_name="Crazy Ex-Girlfriend S04E14 Im Finding My Bliss 720p AMZN WEB-DL",
        season=4,
    )
    assert matched is False


def test_season_matcher_rejects_season_range_pack() -> None:
    matched = _query_matches_torrent_name(
        query="Crazy Ex Girlfriend",
        torrent_name="Crazy Ex-Girlfriend Seasons 1-4 Complete Pack 1080p",
        season=4,
    )
    assert matched is False


def test_season_matcher_accepts_exact_season_pack() -> None:
    matched = _query_matches_torrent_name(
        query="Crazy Ex Girlfriend",
        torrent_name="Crazy Ex-Girlfriend Season 4 Complete 1080p",
        season=4,
    )
    assert matched is True


def test_season_path_matcher_targets_requested_season_files() -> None:
    assert _season_path_matches("Crazy Ex-Girlfriend/Season 4/Episode 01.mkv", 4) is True
    assert _season_path_matches("Crazy Ex-Girlfriend/S04E14.mkv", 4) is True
    assert _season_path_matches("Crazy Ex-Girlfriend/Season 1/Episode 01.mkv", 4) is False


def test_extract_season_prefers_specific_path_segment() -> None:
    path = "Crazy Ex-Girlfriend (2015) S01-S04 Season 1-4/Season 4/S04E01.mkv"
    assert _extract_season_number(path) == 4


def test_parse_selection_rank_supports_ordinal_words() -> None:
    assert _parse_selection_rank("download only season 4 from first option") == 1


def test_parse_selection_rank_does_not_treat_season_number_as_rank() -> None:
    assert _parse_selection_rank("download season 4") is None


def test_parse_selection_rank_accepts_option_id_without_season_confusion() -> None:
    choice = parse_selection_choice(
        "download season 4 option_id opt-01-abc123def4"
    )
    assert choice is not None
    assert choice.option_id == "opt-01-abc123def4"
    assert choice.rank is None


def test_canonical_option_id_is_deterministic() -> None:
    option_id_1 = canonical_option_id(
        source_action="indexer_search",
        rank=1,
        title="Crazy Ex-Girlfriend S04 Pack",
        guid="guid-1",
        episode_id=None,
        movie_id=None,
        release={"guid": "rel-guid", "infoHash": "abc123"},
    )
    option_id_2 = canonical_option_id(
        source_action="indexer_search",
        rank=1,
        title="Crazy Ex-Girlfriend S04 Pack",
        guid="guid-1",
        episode_id=None,
        movie_id=None,
        release={"guid": "rel-guid", "infoHash": "abc123"},
    )
    assert option_id_1 == option_id_2
    assert option_id_1.startswith("opt-01-")


def test_smoke_gate_verify_helper() -> None:
    assert (
        orchestrator_mod.smoke_gate_verify_season_only(
            {
                "season_selection": {
                    "status": "season_only_applied",
                    "season": 4,
                    "enabled_file_count": 10,
                }
            },
            season=4,
        )
        is True
    )
    assert (
        orchestrator_mod.smoke_gate_verify_season_only(
            {
                "season_selection": {
                    "status": "season_only_applied",
                    "season": 5,
                    "enabled_file_count": 10,
                }
            },
            season=4,
        )
        is False
    )


def test_router_smoke_gate_endpoint_contract() -> None:
    with TestClient(app) as client:
        r = client.get(
            "/internal/media-agent/v1/router-smoke-gate?session_key=test-smoke",
            headers=AUTH,
        )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True
    assert d["scenario"] == "cxg-season-4-first-option"
    assert d["session_key"] == "test-smoke"
    assert len(d["steps"]) == 2
    assert d["steps"][1]["message"] == "first option"
    assert d["verify"]["helper"] == "smoke_gate_verify_season_only(tool_result, season=4)"
