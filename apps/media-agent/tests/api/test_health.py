from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from app.main import app

AUTH = {"Authorization": "Bearer test-bearer-secret"}


@respx.mock
def test_health_reports_per_service_status() -> None:
    respx.get("http://sonarr.test/son/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={})
    )
    respx.get("http://radarr.test/rad/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={})
    )
    with TestClient(app) as client:
        r = client.get("/internal/media-agent/v1/health", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sonarr"] == "ok"
    assert body["radarr"] == "ok"
    assert body["prowlarr"] == "n/a"
