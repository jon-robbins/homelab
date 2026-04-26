from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from app.config import reset_settings
from app.main import app

AUTH = {"Authorization": "Bearer test-bearer-secret"}


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
