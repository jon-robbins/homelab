from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

from homelab_mcp.mutation_gate import canonical_arr_ensure_movie, canonical_arr_ensure_series
from homelab_mcp.tools.mutations import media_downloads


def test_arr_ensure_movie_gate(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        media_downloads.arr_ensure(
            settings,
            media_type="movie",
            tmdb_id=1306400,
            expected_action="wrong",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"
    assert "ensure:movie:1306400" in body["error"]["hint"]


@patch("homelab_mcp.tools.mutations.media_downloads.worker_arr_ensure")
@patch("homelab_mcp.tools.mutations.media_downloads.ArrClient")
def test_arr_ensure_movie_applies(mock_cls, mock_ensure, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_ensure.return_value = {
        "ok": True,
        "media_type": "movie",
        "created": True,
        "library_id": 42,
        "path": "/movies/Test",
        "monitored": True,
    }
    body = json.loads(
        media_downloads.arr_ensure(
            settings,
            media_type="movie",
            tmdb_id=1306400,
            expected_action=canonical_arr_ensure_movie(1306400),
        )
    )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True
    assert body["data"]["library_id"] == 42
    assert body["data"]["already_existed"] is False
    mock_ensure.assert_called_once()


def test_arr_ensure_series_gate(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        media_downloads.arr_ensure(
            settings,
            media_type="series",
            tvdb_id=100,
            season=2,
            expected_action="wrong",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"
    assert "ensure:series:100:s2" in body["error"]["hint"]


@patch("homelab_mcp.tools.mutations.media_downloads.worker_arr_ensure")
@patch("homelab_mcp.tools.mutations.media_downloads.ArrClient")
def test_arr_ensure_series_applies(mock_cls, mock_ensure, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_ensure.return_value = {
        "ok": True,
        "media_type": "series",
        "created": False,
        "library_id": 226,
        "path": "/series/Test",
        "monitored": True,
        "season": 2,
    }
    body = json.loads(
        media_downloads.arr_ensure(
            settings,
            media_type="series",
            tvdb_id=100,
            season=2,
            expected_action=canonical_arr_ensure_series(100, 2),
        )
    )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True
    assert body["data"]["season"] == 2
    assert body["data"]["already_existed"] is True
    mock_ensure.assert_called_once()


def test_arr_queue_remove_requires_confirm(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        media_downloads.arr_queue_remove(
            settings,
            media_type="series",
            queue_id=99,
            confirm=False,
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "CONFIRMATION_REQUIRED"


@patch("homelab_mcp.tools.mutations.media_downloads.worker_arr_queue_remove")
@patch("homelab_mcp.tools.mutations.media_downloads.ArrClient")
def test_arr_queue_remove_applies(mock_cls, mock_remove, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_remove.return_value = {
        "ok": True,
        "removed": True,
        "queue_id": 99,
        "remove_from_client": True,
        "blocklist": False,
    }
    body = json.loads(
        media_downloads.arr_queue_remove(
            settings,
            media_type="series",
            queue_id=99,
            confirm=True,
        )
    )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True
    assert body["data"]["queue_id"] == 99
    mock_remove.assert_called_once()
