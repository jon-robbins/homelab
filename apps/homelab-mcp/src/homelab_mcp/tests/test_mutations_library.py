from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from homelab_mcp.tools.mutations import library as mut_library

HASH_A = "a" * 40
HASH_B = "B" * 40


@pytest.fixture
def settings(mcp_settings, tmp_path):
    return replace(mcp_settings, enable_mutations=True, audit_path=str(tmp_path / "audit.jsonl"))


def _body(raw: str) -> dict:
    return json.loads(raw)


def test_qbt_delete_requires_bound_expected_action(settings) -> None:
    body = _body(mut_library.torrents_delete(settings, hashes=[HASH_A], delete_files=True))
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"
    assert f"qbt-delete:files:{HASH_A}" in body["error"]["hint"]


def test_qbt_delete_rejects_bad_hashes(settings) -> None:
    body = _body(mut_library.torrents_delete(settings, hashes=["xyz"], delete_files=False))
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_qbt_delete_applies(httpx_mock, settings) -> None:
    httpx_mock.add_response(
        method="POST", url="http://qbittorrent:8080/api/v2/auth/login", text="Ok.", is_reusable=True
    )
    httpx_mock.add_response(method="POST", url="http://qbittorrent:8080/api/v2/torrents/delete")
    expected = f"qbt-delete:keep-files:{HASH_A},{HASH_B.lower()}"
    body = _body(
        mut_library.torrents_delete(
            settings, hashes=[HASH_B, HASH_A], delete_files=False, expected_action=expected
        )
    )
    assert body["ok"] is True, body
    assert body["data"]["applied"] == {"hashes": [HASH_A, HASH_B.lower()], "delete_files": False}
    delete_req = httpx_mock.get_requests(url="http://qbittorrent:8080/api/v2/torrents/delete")[0]
    assert b"deleteFiles=false" in delete_req.content


def test_arr_delete_movie_applies(httpx_mock, settings) -> None:
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"http://radarr:7878/radarr/api/v3/movie/42\?.*"),
        json={"id": 42, "title": "Film", "year": 2020, "path": "/movies/Film (2020)"},
    )
    httpx_mock.add_response(
        method="DELETE",
        url=re.compile(r"http://radarr:7878/radarr/api/v3/movie/42\?.*"),
    )
    body = _body(
        mut_library.arr_item_delete(
            settings,
            media_type="movie",
            library_id=42,
            delete_files=True,
            expected_action="arr-delete:movie:42:files",
        )
    )
    assert body["ok"] is True, body
    assert body["data"]["applied"]["title"] == "Film"
    delete_req = httpx_mock.get_requests(method="DELETE")[0]
    assert delete_req.url.params["deleteFiles"] == "true"
    assert delete_req.url.params["addImportExclusion"] == "false"


def test_arr_delete_series_uses_sonarr_exclusion_param(httpx_mock, settings) -> None:
    httpx_mock.add_response(
        method="GET",
        url=re.compile(r"http://sonarr:8989/sonarr/api/v3/series/7\?.*"),
        json={"id": 7, "title": "Show", "path": "/tv/Show"},
    )
    httpx_mock.add_response(
        method="DELETE", url=re.compile(r"http://sonarr:8989/sonarr/api/v3/series/7\?.*")
    )
    body = _body(
        mut_library.arr_item_delete(
            settings,
            media_type="series",
            library_id=7,
            delete_files=False,
            add_exclusion=True,
            expected_action="arr-delete:series:7:keep-files",
        )
    )
    assert body["ok"] is True, body
    delete_req = httpx_mock.get_requests(method="DELETE")[0]
    assert delete_req.url.params["addImportListExclusion"] == "true"


def test_arr_delete_rejects_unknown_type(settings) -> None:
    body = _body(
        mut_library.arr_item_delete(settings, media_type="music", library_id=1, delete_files=False)
    )
    assert body["error"]["code"] == "INVALID_ARGUMENT"


def test_arr_delete_not_found(httpx_mock, settings) -> None:
    httpx_mock.add_response(
        method="GET", url=re.compile(r"http://radarr:7878/radarr/api/v3/movie/9\?.*"), status_code=404
    )
    body = _body(
        mut_library.arr_item_delete(
            settings,
            media_type="movie",
            library_id=9,
            delete_files=False,
            expected_action="arr-delete:movie:9:keep-files",
        )
    )
    assert body["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("action", ["approve", "decline", "retry"])
def test_seerr_request_action_applies(httpx_mock, settings, action) -> None:
    httpx_mock.add_response(
        method="POST",
        url=f"http://overseerr:5055/api/v1/request/15/{action}",
        json={"id": 15, "status": 2},
    )
    body = _body(mut_library.seerr_request_action(settings, request_id=15, action=action, confirm=True))
    assert body["ok"] is True, body
    assert body["data"]["applied"] == {"request_id": 15, "action": action, "status": 2}
    request = httpx_mock.get_request()
    assert request.headers["X-Api-Key"] == "overseerr-key"


def test_seerr_request_action_requires_confirm(settings) -> None:
    body = _body(mut_library.seerr_request_action(settings, request_id=15, action="approve"))
    assert body["error"]["code"] == "CONFIRMATION_REQUIRED"


def test_seerr_request_action_maps_404(httpx_mock, settings) -> None:
    httpx_mock.add_response(
        method="POST", url="http://overseerr:5055/api/v1/request/99/approve", status_code=404
    )
    body = _body(
        mut_library.seerr_request_action(settings, request_id=99, action="approve", confirm=True)
    )
    assert body["error"]["code"] == "NOT_FOUND"


def test_library_mutations_disabled(mcp_settings) -> None:
    for raw in (
        mut_library.torrents_delete(mcp_settings, hashes=[HASH_A], delete_files=False),
        mut_library.arr_item_delete(mcp_settings, media_type="movie", library_id=1, delete_files=False),
        mut_library.seerr_request_action(mcp_settings, request_id=1, action="approve", confirm=True),
    ):
        assert _body(raw)["error"]["code"] == "MUTATIONS_DISABLED"
