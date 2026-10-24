from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock, patch

from homelab_mcp.mutation_gate import canonical_season_ensure
from homelab_mcp.tools.mutations import media_downloads, qbt as mut_qbt


def test_sonarr_season_ensure_search_gate(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        media_downloads.sonarr_season_ensure_search(
            settings,
            season_number=11,
            series_id=226,
            expected_action="wrong",
        )
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "EXPECTED_ACTION_MISMATCH"
    assert "sonarr-season-ensure:226:11" in body["error"]["hint"]


@patch("homelab_mcp.tools.mutations.media_downloads.ensure_season_monitored_and_search")
@patch("homelab_mcp.tools.mutations.media_downloads.ArrClient")
def test_sonarr_season_ensure_search_applies(mock_cls, mock_ensure, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    mock_ensure.return_value = {
        "ok": True,
        "series": {"id": 226, "title": "90 Day Fiancé"},
        "season": 11,
        "episodes_monitored": 20,
        "command": {"id": 1, "name": "SeasonSearch"},
        "expected_action": canonical_season_ensure(226, 11),
    }
    body = json.loads(
        media_downloads.sonarr_season_ensure_search(
            settings,
            season_number=11,
            series_id=226,
            expected_action=canonical_season_ensure(226, 11),
        )
    )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True
    assert body["data"]["season"] == 11
    mock_ensure.assert_called_once()


def test_qbt_torrent_force_start_requires_confirm(mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    body = json.loads(
        mut_qbt.qbt_torrent_force_start(settings, torrent_hash="abc12345deadbeef", confirm=False)
    )
    assert body["ok"] is False
    assert body["error"]["code"] == "CONFIRMATION_REQUIRED"


@patch("homelab_mcp.tools.mutations.qbt._qbt_client")
def test_qbt_torrent_force_start_applies(mock_client_factory, mcp_settings) -> None:
    settings = replace(mcp_settings, enable_mutations=True)
    client = MagicMock()
    client.__enter__.return_value = client
    client.__exit__.return_value = None
    client.torrents_info_raw.return_value = [
        {
            "hash": "abc12345deadbeef",
            "name": "90.Day.Fiance.S11.1080p",
            "state": "queuedDL",
            "dlspeed": 0,
            "progress": 0,
        }
    ]
    client.torrents_set_priority.return_value = 1
    client.torrents_set_force_start.return_value = 1
    client.torrents_resume.return_value = 1
    mock_client_factory.return_value = client

    body = json.loads(
        mut_qbt.qbt_torrent_force_start(
            settings, torrent_hash="abc12345deadbeef", confirm=True
        )
    )
    assert body["ok"] is True
    assert body["data"]["mutation"] is True
    assert body["data"]["hash"] == "abc12345deadbeef"
    assert body["data"]["before_state"] == "queuedDL"
    client.torrents_set_priority.assert_called_once_with(["abc12345deadbeef"], "top")
    client.torrents_resume.assert_called_once()
    client.torrents_set_force_start.assert_called_once_with(["abc12345deadbeef"], True)
