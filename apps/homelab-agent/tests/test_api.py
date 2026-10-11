from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

AUTH = {"Authorization": "Bearer test-token"}


def test_health_is_public() -> None:
    # /health is intentionally unauthenticated so compose healthchecks can probe it.
    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/health", headers=AUTH).json() == {"ok": True}


def test_models_requires_auth() -> None:
    client = TestClient(app)
    r = client.get("/v1/models", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "qwen3.8-27b"
