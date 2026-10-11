from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app

AUTH = {"Authorization": "Bearer test-bearer-secret"}


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
