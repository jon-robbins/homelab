from __future__ import annotations

import json
from dataclasses import replace

import pytest

from homelab_mcp.tools.mutations import qbt as mut_qbt


@pytest.fixture
def events_settings(mcp_settings):
    return replace(
        mcp_settings,
        qbt_events_url="http://homelab-qbt-events:8101",
    )


def test_qbt_exclusive_prioritize_requires_confirm(events_settings) -> None:
    settings = replace(events_settings, enable_mutations=True)
    body = json.loads(
        mut_qbt.qbt_exclusive_prioritize(settings, name_contains="90 Day Fiance", confirm=False)
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_qbt_exclusive_prioritize_mutations_disabled(events_settings) -> None:
    body = json.loads(
        mut_qbt.qbt_exclusive_prioritize(events_settings, name_contains="90 Day Fiance", confirm=True)
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "MUTATIONS_DISABLED"


def test_qbt_exclusive_prioritize_applies(httpx_mock, events_settings) -> None:
    settings = replace(events_settings, enable_mutations=True)
    httpx_mock.add_response(
        method="POST",
        url="http://homelab-qbt-events:8101/v1/exclusive/start",
        json={
            "ok": True,
            "session_id": "sess-1",
            "name_contains": "90 Day Fiance",
            "priority_hashes": ["p1"],
            "priority_names": ["90 Day Fiance S11E01"],
            "paused_hashes": ["x1"],
            "paused_count": 1,
            "priority_count": 1,
        },
    )
    body = json.loads(
        mut_qbt.qbt_exclusive_prioritize(settings, name_contains="90 Day Fiance", confirm=True)
    )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True
    assert body["data"]["session_id"] == "sess-1"
    assert body["data"]["priority_hashes"] == ["p1"]
    request = httpx_mock.get_request()
    assert request is not None
    assert json.loads(request.content) == {
        "name_contains": "90 Day Fiance",
        "confirm": True,
    }


@pytest.mark.parametrize(
    ("status_code", "error_body", "expected_code"),
    [
        (409, {"ok": False, "error": "SESSION_ACTIVE"}, "SESSION_ACTIVE"),
        (400, {"ok": False, "error": "no torrents match"}, "INVALID_ARGUMENT"),
        (502, {"ok": False, "error": "qbt unreachable"}, "UPSTREAM_ERROR"),
    ],
)
def test_qbt_exclusive_prioritize_maps_http_errors(
    httpx_mock, events_settings, status_code, error_body, expected_code
) -> None:
    settings = replace(events_settings, enable_mutations=True)
    httpx_mock.add_response(
        method="POST",
        url="http://homelab-qbt-events:8101/v1/exclusive/start",
        status_code=status_code,
        json=error_body,
    )
    body = json.loads(
        mut_qbt.qbt_exclusive_prioritize(settings, name_contains="90 Day Fiance", confirm=True)
    )
    assert body["ok"] is False
    assert body["error"]["code"] == expected_code


def test_qbt_exclusive_cancel_requires_confirm(events_settings) -> None:
    settings = replace(events_settings, enable_mutations=True)
    body = json.loads(mut_qbt.qbt_exclusive_cancel(settings, confirm=False))
    assert body["ok"] is False
    assert body["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_qbt_exclusive_cancel_applies(httpx_mock, events_settings) -> None:
    settings = replace(events_settings, enable_mutations=True)
    httpx_mock.add_response(
        method="POST",
        url="http://homelab-qbt-events:8101/v1/exclusive/cancel",
        json={
            "ok": True,
            "resumed_count": 2,
            "resumed_hashes": ["x1", "x2"],
            "session_id": "sess-1",
        },
    )
    body = json.loads(mut_qbt.qbt_exclusive_cancel(settings, confirm=True))
    assert body["ok"] is True
    assert body["data"]["mutation"] is True
    assert body["data"]["resumed_count"] == 2
    request = httpx_mock.get_request()
    assert request is not None
    assert json.loads(request.content) == {"confirm": True}


def test_qbt_exclusive_cancel_maps_no_session(httpx_mock, events_settings) -> None:
    settings = replace(events_settings, enable_mutations=True)
    httpx_mock.add_response(
        method="POST",
        url="http://homelab-qbt-events:8101/v1/exclusive/cancel",
        status_code=404,
        json={"ok": False, "error": "NO_SESSION"},
    )
    body = json.loads(mut_qbt.qbt_exclusive_cancel(settings, confirm=True))
    assert body["ok"] is False
    assert body["error"]["code"] == "NO_SESSION"


def test_qbt_exclusive_status_works_without_mutations(httpx_mock, events_settings) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://homelab-qbt-events:8101/v1/exclusive/status",
        json={"ok": True, "active": False, "session": None},
    )
    body = json.loads(mut_qbt.qbt_exclusive_status(events_settings))
    assert body["ok"] is True
    assert body["data"]["active"] is False


def test_qbt_exclusive_status_active(httpx_mock, events_settings) -> None:
    httpx_mock.add_response(
        method="GET",
        url="http://homelab-qbt-events:8101/v1/exclusive/status",
        json={
            "ok": True,
            "active": True,
            "session": {
                "session_id": "sess-1",
                "name_contains": "90 Day Fiance",
                "priority_count": 2,
                "remaining_count": 1,
            },
        },
    )
    body = json.loads(mut_qbt.qbt_exclusive_status(events_settings))
    assert body["ok"] is True
    assert body["data"]["active"] is True
    assert body["data"]["session"]["name_contains"] == "90 Day Fiance"
