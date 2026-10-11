"""Destructive-action blast-radius policy tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import reset_settings
from app.main import app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("MEDIA_AGENT_ALLOW_DESTRUCTIVE", "false")
    monkeypatch.setenv("MEDIA_AGENT_DESTRUCTIVE_CONFIRM_TOKEN", "confirm-me")
    reset_settings()
    with TestClient(app) as c:
        yield c
    reset_settings()


def _auth_headers(**extra: str) -> dict[str, str]:
    headers = {"Authorization": "Bearer test-bearer-secret"}
    headers.update(extra)
    return headers


def test_download_grab_blocked_without_clearance(client: TestClient) -> None:
    r = client.post(
        "/internal/media-agent/v1/download-grab",
        headers=_auth_headers(),
        json={"type": "tv", "episode_id": 1, "guid": "g1"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_download_grab_allowed_with_confirm_header(client: TestClient) -> None:
    r = client.post(
        "/internal/media-agent/v1/download-grab",
        headers=_auth_headers(**{"X-Media-Agent-Destructive-Confirm": "confirm-me"}),
        json={"type": "tv", "episode_id": 1, "guid": "g1"},
    )
    assert r.status_code != 403


def test_action_grab_blocked_without_clearance(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.actions.grab_tv import DownloadGrabTV

    def _fake_run(self, ctx, args):  # noqa: ARG001
        return {"ok": True}

    monkeypatch.setattr(DownloadGrabTV, "run", _fake_run)
    r = client.post(
        "/internal/media-agent/v1/action",
        headers=_auth_headers(),
        json={"action": "download_grab_tv", "episode_id": 10, "guid": "g1"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


def test_delete_method_blocked(client: TestClient) -> None:
    r = client.delete(
        "/internal/media-agent/v1/health",
        headers=_auth_headers(),
    )
    assert r.status_code == 403
