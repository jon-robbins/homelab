from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app

AUTH = {"Authorization": "Bearer test-bearer-secret"}


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
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
