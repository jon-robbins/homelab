from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app

AUTH = {"Authorization": "Bearer test-bearer-secret"}


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


def test_search_unauthorized_without_bearer() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/search",
            json={"type": "tv", "query": "ab"},
        )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_search_validation_extra_field() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/internal/media-agent/v1/search",
            json={"type": "tv", "query": "ab", "foo": 1},
            headers=AUTH,
        )
    assert r.status_code == 400
