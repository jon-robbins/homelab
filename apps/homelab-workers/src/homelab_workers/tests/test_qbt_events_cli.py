from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from homelab_workers.cli import qbt_events


class FakeQbt:
    def __init__(self, *_args, **_kwargs) -> None:
        self.rows: list[dict] = [
            {"hash": "p1", "name": "90 Day Fiance S11E01", "state": "downloading", "progress": 0.0},
            {"hash": "x1", "name": "Other Show", "state": "downloading", "progress": 0.0},
        ]

    def __enter__(self) -> FakeQbt:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def torrents_info_raw(self) -> list[dict]:
        return self.rows

    def torrents_pause(self, *, hashes: list[str]) -> None:
        return None

    def torrents_resume(self, *, hashes: list[str]) -> None:
        return None

    def torrents_set_force_start(self, _hashes: list[str], _value: bool) -> None:
        return None

    def torrents_set_priority(self, _hashes: list[str], _priority: str) -> None:
        return None


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    request = Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request) as response:
        return response.status, json.loads(response.read())


def _get(base: str, path: str) -> tuple[int, dict]:
    with urlopen(f"{base}{path}") as response:
        return response.status, json.loads(response.read())


@pytest.fixture
def server_env(tmp_path, monkeypatch):
    session_path = tmp_path / "session.json"
    status_path = tmp_path / "status.json"
    monkeypatch.setenv("QBT_EVENTS_SESSION_PATH", str(session_path))
    monkeypatch.setenv("QBT_EVENTS_STATUS_PATH", str(status_path))
    monkeypatch.setattr(qbt_events, "_qbt_client", lambda: FakeQbt())
    server = qbt_events.make_server("127.0.0.1", 0)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base, session_path, status_path
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_healthz_returns_ok(server_env) -> None:
    base, _, _ = server_env
    status, body = _get(base, "/healthz")
    assert status == 200
    assert body == {"ok": True, "service": "homelab-qbt-events"}


def test_start_without_confirm_returns_400(server_env) -> None:
    base, _, _ = server_env
    request = Request(
        f"{base}/v1/exclusive/start",
        data=json.dumps({"name_contains": "90 Day Fiance"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(request)
    except HTTPError as error:
        try:
            assert error.code == 400
            assert json.loads(error.read()) == {"ok": False, "error": "confirm_required"}
        finally:
            error.close()
    else:
        raise AssertionError("expected confirm_required")


def test_start_exclusive_ok(server_env) -> None:
    base, session_path, _ = server_env
    status, body = _post(
        base,
        "/v1/exclusive/start",
        {"name_contains": "90 Day Fiance", "confirm": True},
    )
    assert status == 200
    assert body["ok"] is True
    assert body["name_contains"] == "90 Day Fiance"
    assert set(body["priority_hashes"]) == {"p1"}
    assert body["paused_hashes"] == ["x1"]
    assert session_path.exists()


def test_second_start_returns_409(server_env) -> None:
    base, _, _ = server_env
    _post(base, "/v1/exclusive/start", {"name_contains": "90 Day Fiance", "confirm": True})
    request = Request(
        f"{base}/v1/exclusive/start",
        data=json.dumps({"name_contains": "90 Day Fiance", "confirm": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urlopen(request)
    except HTTPError as error:
        try:
            assert error.code == 409
            payload = json.loads(error.read())
            assert payload["ok"] is False
            assert payload["error"] == "SESSION_ACTIVE"
        finally:
            error.close()
    else:
        raise AssertionError("expected SESSION_ACTIVE")


def test_torrent_finished_ack(server_env) -> None:
    base, _, _ = server_env
    _post(base, "/v1/exclusive/start", {"name_contains": "90 Day Fiance", "confirm": True})
    status, body = _post(base, "/v1/torrent-finished", {"hash": "p1"})
    assert status == 200
    assert body["ok"] is True
    assert body["hash"] == "p1"
    assert body["handlers"][0]["name"] == "exclusive"
    assert body["handlers"][0]["handled"] is True


def test_cancel_exclusive(server_env) -> None:
    base, session_path, _ = server_env
    start_body = _post(
        base,
        "/v1/exclusive/start",
        {"name_contains": "90 Day Fiance", "confirm": True},
    )[1]
    status, body = _post(base, "/v1/exclusive/cancel", {"confirm": True})
    assert status == 200
    assert body["ok"] is True
    assert body["session_id"] == start_body["session_id"]
    assert body["resumed_count"] == 1
    assert body["resumed_hashes"] == ["x1"]
    payload = json.loads(session_path.read_text())
    assert payload["active"] is False


def test_status_idle_and_active(server_env) -> None:
    base, _, _ = server_env
    status, idle = _get(base, "/v1/exclusive/status")
    assert status == 200
    assert idle == {"ok": True, "active": False, "session": None}

    _post(base, "/v1/exclusive/start", {"name_contains": "90 Day Fiance", "confirm": True})
    status, active = _get(base, "/v1/exclusive/status")
    assert status == 200
    assert active["ok"] is True
    assert active["active"] is True
    assert active["session"]["name_contains"] == "90 Day Fiance"
    assert active["session"]["priority_count"] == 1
    assert active["session"]["remaining_count"] == 1


def test_healthcheck_cli() -> None:
    assert qbt_events.healthcheck() == 0
